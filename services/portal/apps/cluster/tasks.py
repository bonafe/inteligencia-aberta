"""Heartbeat periódico de recursos da máquina (CPU/RAM/disco).

Toda máquina do cluster roda esta task via Celery Beat, hospede a infra
compartilhada ou não — não há tratamento diferente por hierarquia (ADR-006).
Ela nunca pode derrubar o worker: qualquer falha de coleta (psutil
indisponível, disco sem permissão de leitura) fica só no log.
"""

import logging
import os
import socket

from celery import shared_task

logger = logging.getLogger(__name__)

#: Intervalo do agendamento em `CELERY_BEAT_SCHEDULE` (settings/base.py) — lido
#: aqui também porque `MaquinaStatus.online` deriva "recente" a partir dele.
HEARTBEAT_INTERVALO_S = 30
#: Quantos intervalos sem heartbeat até considerar a máquina offline. Frouxo
#: de propósito — perder um heartbeat não deve marcar a máquina como offline.
HEARTBEAT_TOLERANCIA = 3


def _coletar_recursos() -> dict:
    """Lê CPU/RAM/disco da máquina atual. Nunca levanta — na ausência de
    `psutil` ou de permissão, devolve o que conseguir, mesmo que vazio."""
    recursos = {}
    try:
        import psutil

        recursos["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        recursos["cpu_count"] = psutil.cpu_count() or None
        memoria = psutil.virtual_memory()
        recursos["ram_total_mb"] = int(memoria.total / (1024 * 1024))
        recursos["ram_disponivel_mb"] = int(memoria.available / (1024 * 1024))
        disco = psutil.disk_usage("/")
        recursos["disco_disponivel_gb"] = round(disco.free / (1024 ** 3), 1)
    except Exception:
        logger.exception("falha ao coletar recursos da máquina para heartbeat")
    return recursos


def _autorregistrar_no_local():
    """Garante a `Maquina` que representa esta própria instância (ver
    `apps.cluster.pares.garantir_maquina_local`), para ela nunca precisar de cadastro
    manual contra si mesma.

    A organização é a do administrador da instância (`pares.organizacao_da_instancia`): o
    cadastro é aberto e cada pessoa tem a sua, mas a máquina é de quem administra a instância.
    Sem como saber qual é (várias organizações e nenhum superusuário), o autorregistro não
    acontece e segue exigindo `CLUSTER_MACHINE_ID` explícito.

    O apelido vem de `CLUSTER_LOCAL_APELIDO` (uma linha no `.env`, estável entre
    reinícios de container) — nunca de `socket.gethostname()` sozinho: dentro do Docker,
    o hostname do container muda a cada recriação.
    """
    from .pares import garantir_maquina_local, organizacao_da_instancia

    organizacao = organizacao_da_instancia()
    return garantir_maquina_local(organizacao) if organizacao is not None else None


def _disco_ollama_livre_gb():
    """Espaço livre onde o Ollama guarda os modelos, se o volume estiver montado no portal."""
    from django.conf import settings

    caminho = getattr(settings, "OLLAMA_DATA_DIR_NA_PORTAL", "")
    if not caminho:
        return None
    try:
        import psutil

        return round(psutil.disk_usage(caminho).free / (1024 ** 3), 1)
    except (OSError, ImportError):
        return None


def _inventario_local(maquina) -> None:
    """Grava o inventário detalhado do Ollama desta máquina (snapshot). Nunca levanta.

    O heartbeat (evento) continua levando só os **nomes** — o payload de um evento tem teto de 8 KB e um
    inventário com detalhes não cabe com folga —; os detalhes vão direto para o banco, a cada ciclo.
    """
    from django.conf import settings

    from . import ollama_admin
    from .projecao import aplicar_inventario

    if not getattr(settings, "OLLAMA_HOST", ""):
        aplicar_inventario(maquina, [], disponivel=None)         # sem Ollama nesta máquina
        return
    try:
        modelos = ollama_admin.listar_detalhado()
    except ollama_admin.ErroOllama:
        aplicar_inventario(maquina, [], disponivel=False, disco_ollama_livre_gb=_disco_ollama_livre_gb())
        return
    try:
        carregados = {m["nome"] for m in ollama_admin.ps()}
    except ollama_admin.ErroOllama:
        carregados = set()
    try:
        versao = ollama_admin.versao()
    except ollama_admin.ErroOllama:
        versao = ""
    for m in modelos:
        m["carregado"] = m["nome"] in carregados
    aplicar_inventario(maquina, modelos, versao=versao, disponivel=True, disco_ollama_livre_gb=_disco_ollama_livre_gb())


@shared_task(name="apps.cluster.tasks.executar_operacao_modelo", acks_late=True, max_retries=0)
def executar_operacao_modelo(operacao_id):
    """Instala ou remove um modelo do Ollama desta máquina (ver `apps.cluster.operacoes`). Sem retry: instalar
    de novo é uma ação do usuário (o Ollama reaproveita o que já baixou)."""
    from .operacoes import executar

    return executar(operacao_id)


@shared_task(name="apps.cluster.tasks.acompanhar_operacoes")
def acompanhar_operacoes():
    """Traz o progresso das operações em pares e fecha as que ninguém executa. Sai cedo se nada está ativo."""
    from .models import OperacaoModeloOllama
    from .operacoes import acompanhar_espelhos, varrer_travadas

    if not OperacaoModeloOllama.objects.filter(status__in=OperacaoModeloOllama.ATIVOS).exists():
        return None
    return {"travadas": varrer_travadas(), **acompanhar_espelhos()}


@shared_task(name="apps.cluster.tasks.puxar_pares")
def puxar_pares():
    """Puxa o estado dos pares próprios confirmados (ver `apps.cluster.pull`)."""
    from .pull import puxar_pares as _puxar

    return _puxar()


@shared_task(name="apps.cluster.tasks.emitir_heartbeat_maquina")
def emitir_heartbeat_maquina():
    """Emite `maquina.heartbeat` para a `Maquina` desta instância.

    Com `CLUSTER_MACHINE_ID` configurado, usa essa identidade. Sem ele, tenta
    `_autorregistrar_no_local()` — cobre o caso comum (uma única organização).
    Sem nenhum dos dois (múltiplas organizações, ambíguo demais pra adivinhar),
    a task roda e não faz nada — não é erro."""
    from django.conf import settings

    from apps.events.emit import emit

    from .models import Maquina
    from .projecao import aplicar_heartbeat

    machine_id = getattr(settings, "CLUSTER_MACHINE_ID", "") or os.environ.get("CLUSTER_MACHINE_ID", "")
    if machine_id:
        try:
            maquina = Maquina.objects.get(id=machine_id, ativa=True)
        except Exception:
            logger.warning("CLUSTER_MACHINE_ID=%s não corresponde a uma Maquina ativa", machine_id)
            return "máquina não encontrada ou inativa"
    else:
        maquina = _autorregistrar_no_local()
        if maquina is None:
            return "sem CLUSTER_MACHINE_ID e sem autorregistro possível — heartbeat não aplicável"

    filas = [q.strip() for q in os.environ.get("CELERY_QUEUES", "").split(",") if q.strip()]

    payload = {**_coletar_recursos(), "filas": filas}
    if maquina.ollama_endpoint:
        # listar_modelos() nunca levanta — degrada pra lista vazia se o
        # Ollama desta máquina estiver offline no momento do heartbeat.
        # O heartbeat é sempre DESTA máquina: consulta o Ollama pelo endereço local
        # (OLLAMA_HOST), não pelo anunciado — que é o de fora (VPN) e pode nem ser
        # alcançável de dentro do container.
        from apps.artifacts.extractors.ollama_client import listar_modelos
        payload["modelos_ollama"] = listar_modelos(host=getattr(settings, "OLLAMA_HOST", "") or maquina.ollama_endpoint)

    evento = emit(
        "maquina.heartbeat", "ok",
        subject_type="maquina", subject_id=maquina.id,
        tenant_id=maquina.organizacao_id,
        message=f"heartbeat de {maquina.apelido}",
        payload=payload,
    )
    if evento is not None:
        aplicar_heartbeat(evento)
    try:
        _inventario_local(maquina)
    except Exception:
        logger.exception("falha ao gravar o inventário local do Ollama")
    return "ok" if evento else "emit falhou — ver log"

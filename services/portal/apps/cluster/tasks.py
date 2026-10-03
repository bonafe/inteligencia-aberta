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
    """Cria (ou encontra) a `Maquina` que representa esta própria instância,
    quando ela hospeda a infraestrutura compartilhada — pra esse nó nunca
    precisar rodar `registrar_maquina` contra si mesmo, o mesmo motivo por
    trás do autorregistro de peers via `scripts/entrar_no_cluster.py`, só que
    sem precisar de rede nenhuma (é o próprio processo).

    Só age quando `CLUSTER_HOSPEDA_INFRA=true` (default) e existe exatamente
    uma `Organization` — com mais de uma, não há como adivinhar a dona sem
    ambiguidade, e o autorregistro simplesmente não acontece (segue exigindo
    `CLUSTER_MACHINE_ID`/`registrar_maquina` explícito nesse caso).

    O apelido vem de `CLUSTER_LOCAL_APELIDO` (uma linha no `.env`, estável
    entre reinícios de container) — nunca de `socket.gethostname()` sozinho:
    dentro do Docker, o hostname do container muda a cada recriação, e usar
    isso como chave geraria uma `Maquina` nova a cada `docker compose up`.
    """
    from django.conf import settings

    from apps.accounts.models import Organization

    from .models import Maquina

    if not getattr(settings, "CLUSTER_HOSPEDA_INFRA", True):
        return None

    orgs = list(Organization.objects.all()[:2])
    if len(orgs) != 1:
        return None
    organizacao = orgs[0]

    apelido = os.environ.get("CLUSTER_LOCAL_APELIDO") or socket.gethostname()
    maquina, criada = Maquina.objects.get_or_create(
        organizacao=organizacao, apelido=apelido,
        defaults={
            "dono": organizacao.owner,
            "modo": Maquina.Modo.COMPUTE,
            "hostname_declarado": apelido,
            "hospeda_infra_compartilhada": True,
            # Sem token: esta Maquina nunca autentica contra si mesma por
            # HTTP (X-Machine-Token é para peers remotos puxando replicação)
            # — é o próprio processo chamando funções Python diretamente.
            "token_hash": "",
            "ollama_endpoint": getattr(settings, "OLLAMA_HOST", ""),
        },
    )
    if criada:
        logger.info("máquina local autorregistrada — apelido=%s", apelido)
    return maquina


@shared_task(name="apps.cluster.tasks.emitir_heartbeat_maquina")
def emitir_heartbeat_maquina():
    """Emite `maquina.heartbeat` para a `Maquina` desta instância.

    Com `CLUSTER_MACHINE_ID` configurado (peer que se autorregistrou via
    `scripts/entrar_no_cluster.py`), usa essa identidade. Sem ele, tenta
    `_autorregistrar_no_local()` — cobre o caso comum (esta máquina hospeda a
    infra compartilhada). Sem nenhum dos dois (múltiplas organizações,
    ambíguo demais pra adivinhar), a task roda e não faz nada — não é erro."""
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
        from apps.artifacts.extractors.ollama_client import listar_modelos
        payload["modelos_ollama"] = listar_modelos(host=maquina.ollama_endpoint)

    evento = emit(
        "maquina.heartbeat", "ok",
        subject_type="maquina", subject_id=maquina.id,
        tenant_id=maquina.organizacao_id,
        message=f"heartbeat de {maquina.apelido}",
        payload=payload,
    )
    if evento is not None:
        aplicar_heartbeat(evento)
    return "ok" if evento else "emit falhou — ver log"

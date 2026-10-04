"""Projeção de `maquina.heartbeat` em `MaquinaStatus`/`MaquinaModeloOllama`, e
de `llm.chamada_ollama` na velocidade média de `MaquinaModeloOllama`.

Mesma disciplina de `apps.events.projecao.aplicar_evento`: idempotente,
nunca levanta, e reconstruível do zero a partir do log
(`manage.py reconstruir_status_maquinas`). Diferença deliberada daquele:
roda fora da transação de `emit()` (chamada explícita pelo autor do evento,
não amarrada em `emit()` em si) porque `MaquinaStatus`/`MaquinaModeloOllama`
são snapshots descartáveis — perder um evento é só uma janela de dado
desatualizado, não perda de histórico.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def aplicar_heartbeat(evento) -> "MaquinaStatus | None":  # noqa: F821
    """Reflete um evento `maquina.heartbeat` em `MaquinaStatus`.

    Ignora eventos de sequence menor que a já aplicada — diferente de
    `aplicar_evento` (que usa "maior sequence vence" campo a campo), aqui um
    heartbeat é um snapshot completo e não parcial, então descartar os
    atrasados é a escolha certa (não perde contagem nenhuma, ao contrário do
    caso da projeção do pipeline).
    """
    from .models import Maquina, MaquinaModeloOllama, MaquinaStatus

    try:
        if evento.subject_type != "maquina" or not evento.subject_id:
            return None
        with transaction.atomic():
            try:
                maquina = Maquina.objects.get(id=evento.subject_id)
            except Maquina.DoesNotExist:
                logger.warning("heartbeat para máquina inexistente — subject_id=%s", evento.subject_id)
                return None

            status, _ = MaquinaStatus.objects.select_for_update().get_or_create(maquina=maquina)
            if evento.sequence <= status.ultimo_evento_sequence and status.ultimo_heartbeat_em:
                return status

            payload = evento.payload or {}
            status.cpu_percent = payload.get("cpu_percent")
            status.cpu_count = payload.get("cpu_count")
            status.ram_total_mb = payload.get("ram_total_mb")
            status.ram_disponivel_mb = payload.get("ram_disponivel_mb")
            status.disco_disponivel_gb = payload.get("disco_disponivel_gb")
            status.filas = payload.get("filas") or []
            status.ultimo_heartbeat_em = evento.occurred_at
            status.ultimo_evento_sequence = evento.sequence
            status.save()

            # Modelos Ollama instalados nesta máquina, se ela tiver
            # ollama_endpoint configurado (ver apps/cluster/tasks.py). Só
            # marca "visto" — a velocidade real (tokens_por_segundo_medio)
            # vem de aplicar_metrica_llm, não daqui.
            for nome_modelo in payload.get("modelos_ollama") or []:
                MaquinaModeloOllama.objects.update_or_create(
                    maquina=maquina, nome_modelo=nome_modelo,
                    defaults={"visto_pela_ultima_vez": evento.occurred_at},
                )
            return status
    except Exception:
        logger.exception("projeção de heartbeat falhou para o evento %s", getattr(evento, "id", None))
        return None


def aplicar_metrica_llm(evento) -> "MaquinaModeloOllama | None":  # noqa: F821
    """Reflete um evento `llm.chamada_ollama` (legado) ou `llm.chamada`
    (formato unificado, qualquer provider/finalidade — ver
    `apps.events.llm_telemetria`) em `MaquinaModeloOllama` — média corrida de
    tokens/segundo, usada por `apps.cluster.llm_router` para escolher a
    máquina mais rápida para um modelo.

    Chamadas anthropic também passam por `llm.chamada`, mas não têm máquina
    executora nossa nem tokens_por_segundo — o guard de provider abaixo as
    ignora, silenciosamente, sem isso virar uma exceção.

    Diferente de `aplicar_heartbeat`: não descarta por sequence (cada chamada
    é uma amostra independente que soma à média, não um snapshot que
    substitui o anterior) — reprocessar o log do zero
    (`reconstruir_status_maquinas`) reaplica cada amostra exatamente uma vez,
    então a ordem de chegada não afeta o resultado final da média.
    """
    from .models import Maquina, MaquinaModeloOllama

    try:
        payload = evento.payload or {}
        if payload.get("provider") not in (None, "ollama"):
            return None

        nome_modelo = payload.get("modelo") or payload.get("modelo_resposta") or payload.get("modelo_solicitado")
        tokens_por_segundo = payload.get("tokens_por_segundo")
        # Legado (llm.chamada_ollama): a máquina vem em subject_id/subject_type.
        # Novo (llm.chamada): vem no próprio payload.
        maquina_id = payload.get("maquina_id") or (
            evento.subject_id if evento.subject_type == "maquina" else None
        )
        if not nome_modelo or tokens_por_segundo is None or not maquina_id:
            return None

        with transaction.atomic():
            try:
                maquina = Maquina.objects.get(id=maquina_id)
            except Maquina.DoesNotExist:
                logger.warning("métrica de LLM para máquina inexistente — subject_id=%s", maquina_id)
                return None

            registro, _ = MaquinaModeloOllama.objects.select_for_update().get_or_create(
                maquina=maquina, nome_modelo=nome_modelo,
            )
            medio_atual = registro.tokens_por_segundo_medio or 0.0
            n = registro.amostras_n
            registro.tokens_por_segundo_medio = (medio_atual * n + tokens_por_segundo) / (n + 1)
            registro.amostras_n = n + 1
            registro.num_thread_observado = payload.get("num_thread") or registro.num_thread_observado
            registro.num_ctx_observado = payload.get("num_ctx") or registro.num_ctx_observado
            registro.visto_pela_ultima_vez = evento.occurred_at
            registro.save()
            return registro
    except Exception:
        logger.exception("projeção de métrica LLM falhou para o evento %s", getattr(evento, "id", None))
        return None


# ── inventário do Ollama (snapshot, local ou vindo de um par) ───────────────

LIMITE_MODELOS = 500
_LIMITE_INT = 2 ** 62


def _texto(valor, limite: int) -> str:
    if not isinstance(valor, (str, int, float)) or isinstance(valor, bool):
        return ""
    texto = "".join(c for c in str(valor) if c.isprintable())
    return texto.strip()[:limite]


def _inteiro(valor):
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= _LIMITE_INT else None


def _numero(valor, maximo: float):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor) if 0 <= valor <= maximo else None


def sanear_modelos(lista) -> list[dict]:
    """Modelos vindos de **fora** (um par), reduzidos a tipos e tamanhos seguros. Nunca confia no que chega."""
    saida = []
    for bruto in (lista if isinstance(lista, list) else [])[:LIMITE_MODELOS]:
        if not isinstance(bruto, dict):
            continue
        nome = _texto(bruto.get("nome"), 200)
        if not nome:
            continue
        saida.append({
            "nome": nome, "tamanho_bytes": _inteiro(bruto.get("tamanho_bytes")),
            "digest": _texto(bruto.get("digest"), 100), "familia": _texto(bruto.get("familia"), 60),
            "parametros": _texto(bruto.get("parametros"), 20), "quantizacao": _texto(bruto.get("quantizacao"), 30),
            "carregado": bruto.get("carregado") is True,
        })
    return saida


def aplicar_inventario(maquina, modelos: list[dict], *, versao: str = "", disponivel: bool | None = True,
                       disco_ollama_livre_gb: float | None = None, agora=None) -> None:
    """Grava o inventário do Ollama de uma máquina como **snapshot**: o que não está na lista, sai.

    `modelos` já saneados (`nome`, `tamanho_bytes`, `digest`, `familia`, `parametros`, `quantizacao`,
    `carregado`). Com `disponivel` diferente de `True` (Ollama fora do ar ou ausente) os modelos **não**
    são tocados — não se apaga o que se sabe só porque o Ollama caiu um instante. A velocidade aprendida
    (`tokens_por_segundo_medio`) de um modelo que continua instalado é preservada.
    """
    from django.utils import timezone

    from .models import MaquinaModeloOllama, MaquinaStatus

    agora = agora or timezone.now()
    with transaction.atomic():
        status, _ = MaquinaStatus.objects.select_for_update().get_or_create(maquina=maquina)
        status.ollama_disponivel = disponivel
        status.ollama_versao = _texto(versao, 40)
        status.disco_ollama_livre_gb = disco_ollama_livre_gb
        status.save(update_fields=["ollama_disponivel", "ollama_versao", "disco_ollama_livre_gb"])
        if disponivel is not True:
            return
        nomes = set()
        for m in modelos:
            nomes.add(m["nome"])
            MaquinaModeloOllama.objects.update_or_create(
                maquina=maquina, nome_modelo=m["nome"],
                defaults={
                    "visto_pela_ultima_vez": agora, "tamanho_bytes": m.get("tamanho_bytes"),
                    "digest": m.get("digest") or "", "familia": m.get("familia") or "",
                    "parametros": m.get("parametros") or "", "quantizacao": m.get("quantizacao") or "",
                    "carregado": bool(m.get("carregado")),
                },
            )
        MaquinaModeloOllama.objects.filter(maquina=maquina).exclude(nome_modelo__in=nomes).delete()


def aplicar_estado_remoto(par, estado: dict, *, agora=None) -> None:
    """Reflete no banco local o `estado` que um par **próprio** devolveu (pull).

    Tudo o que chega é saneado: tipos, faixas e tamanhos. `ultimo_heartbeat_em` passa a ser o instante
    **deste pull** (é o que define online/offline do par). O endereço de gateway que o par anuncia só vale se
    o **host for o mesmo** do `endpoint_controle` que o administrador conferiu no enrolamento — um par
    comprometido não consegue redirecionar o tráfego de LLM (e o token do gateway) para um terceiro.
    """
    from django.utils import timezone

    from apps.federacao.endpoints import validar_endpoint

    from .models import MaquinaStatus

    agora = agora or timezone.now()
    estado = estado if isinstance(estado, dict) else {}
    recursos = estado.get("recursos") if isinstance(estado.get("recursos"), dict) else {}
    ollama = estado.get("ollama") if isinstance(estado.get("ollama"), dict) else {}

    with transaction.atomic():
        status, _ = MaquinaStatus.objects.select_for_update().get_or_create(maquina=par)
        status.cpu_percent = _numero(recursos.get("cpu_percent"), 100000)
        status.cpu_count = _inteiro(recursos.get("cpu_count"))
        status.ram_total_mb = _inteiro(recursos.get("ram_total_mb"))
        status.ram_disponivel_mb = _inteiro(recursos.get("ram_disponivel_mb"))
        status.disco_disponivel_gb = _numero(recursos.get("disco_disponivel_gb"), 1e9)
        status.ultimo_heartbeat_em = agora
        status.save(update_fields=["cpu_percent", "cpu_count", "ram_total_mb", "ram_disponivel_mb",
                                   "disco_disponivel_gb", "ultimo_heartbeat_em"])

        configurado = ollama.get("configurado") is True
        disponivel = ollama.get("disponivel") if ollama.get("disponivel") in (True, False) else None
        aplicar_inventario(
            par, sanear_modelos(estado.get("modelos")), versao=_texto(ollama.get("versao"), 40),
            disponivel=(disponivel if configurado else None),
            disco_ollama_livre_gb=_numero(recursos.get("disco_ollama_livre_gb"), 1e9), agora=agora,
        )

        gateway = ""
        anunciado = estado.get("gateway_endpoint")
        if isinstance(anunciado, str) and anunciado:
            try:
                from urllib.parse import urlsplit

                candidato = validar_endpoint(anunciado)
                if urlsplit(candidato).hostname == urlsplit(par.endpoint_controle).hostname:
                    gateway = candidato
            except ValueError:
                pass
        par.gateway_endpoint = gateway
        par.capacidades_json = {"v": _inteiro(estado.get("v")), "versao": _texto(estado.get("versao"), 40),
                                "nome": _texto(estado.get("nome"), 120), "ollama_configurado": configurado}
        par.ultimo_pull_em = agora
        par.ultimo_pull_erro = ""
        par.pull_falhas = 0
        par.ultima_tentativa_em = agora
        par.save(update_fields=["gateway_endpoint", "capacidades_json", "ultimo_pull_em", "ultimo_pull_erro",
                                "pull_falhas", "ultima_tentativa_em"])

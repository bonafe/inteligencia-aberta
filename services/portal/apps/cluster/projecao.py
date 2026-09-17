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

"""Escolhe em qual máquina do cluster rodar uma chamada Ollama.

Único lugar que decide "qual máquina" — `ollama_client.py` só sabe falar com
UM Ollama por vez; `llm_common.py::gerar_texto` chama `escolher_execucao`
antes de chamar `ollama_client.gerar`/`gerar_chat`.

Critério: máquinas nunca testadas para o modelo pedido (`amostras_n == 0`)
entram primeiro — garante que toda máquina nova seja experimentada pelo
menos uma vez (sem isso, uma máquina só descoberta agora nunca teria dado
pra competir e nunca seria escolhida). Entre as já testadas, maior
`tokens_por_segundo_medio` vence — aprendido de chamadas reais, não de
benchmark sintético (ver apps.cluster.projecao.aplicar_metrica_llm).
"""

import dataclasses
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

#: Um modelo "visto" há mais que isso no heartbeat é tratado como não
#: confiável (a máquina pode ter removido o modelo, ou o Ollama dela caiu
#: sem que MaquinaStatus.online ainda tenha refletido isso).
STALENESS_MODELO_S = 180


@dataclasses.dataclass
class ExecucaoOllama:
    maquina_id: str
    host: str
    num_thread: int | None


def escolher_execucao(nome_modelo: str) -> ExecucaoOllama | None:
    """Melhor máquina do cluster para rodar `nome_modelo` agora, ou `None` se
    nenhuma máquina ativa+online tem esse modelo (chamador cai para o Ollama
    local — ver `llm_common.gerar_texto`)."""
    from .models import Maquina

    try:
        limite_visto = timezone.now() - timezone.timedelta(seconds=STALENESS_MODELO_S)
        candidatas = list(
            Maquina.objects.filter(
                ativa=True,
                modelos_ollama__nome_modelo=nome_modelo,
                modelos_ollama__visto_pela_ultima_vez__gte=limite_visto,
            )
            .exclude(ollama_endpoint="")
            .select_related("status")
            .prefetch_related("modelos_ollama")
        )
    except Exception:
        logger.exception("falha ao consultar candidatas para o modelo %s", nome_modelo)
        return None

    melhores = []
    for maquina in candidatas:
        status = getattr(maquina, "status", None)
        if not status or not status.online:
            continue
        capacidade = next((m for m in maquina.modelos_ollama.all() if m.nome_modelo == nome_modelo), None)
        if capacidade is None:
            continue
        # Nunca testada (amostras_n == 0) entra com prioridade máxima —
        # float("inf") garante que sempre vence a ordenação abaixo até ganhar
        # sua primeira amostra real.
        prioridade = float("inf") if capacidade.amostras_n == 0 else (capacidade.tokens_por_segundo_medio or 0.0)
        melhores.append((prioridade, maquina, status.cpu_count))

    if not melhores:
        return None

    melhores.sort(key=lambda t: t[0], reverse=True)
    _, maquina, cpu_count = melhores[0]
    return ExecucaoOllama(maquina_id=str(maquina.id), host=maquina.ollama_endpoint, num_thread=cpu_count)

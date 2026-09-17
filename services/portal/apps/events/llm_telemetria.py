"""Telemetria unificada de qualquer chamada a um provider de LLM (Anthropic ou
Ollama), qualquer que seja o motivo — classificação/extração automática da
cascata, estruturação manual, comparação-juiz, ou o gateway compatível com
OpenAI (`apps.cluster.gateway`).

Ponto único chamado pelos 4 lugares reais que falam com um provider
(`apps.artifacts.extractors.ollama_client._chamar`,
`apps.artifacts.extractors.llm_common.gerar_texto` [ramo anthropic],
`apps.artifacts.extractors.llm_classifier.llm_classify` e
`.llm_extract_and_schema`) — sempre depois que a chamada terminou, sucesso ou
erro, porque `duration_ms` e os campos de uso só existem depois.

Nunca levanta — mesma garantia de `apps.events.emit.emit()`. Uma falha aqui
nunca pode derrubar uma chamada de LLM que já terminou (bem ou mal).
"""

import dataclasses
import logging

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ResultadoLLM:
    """Forma comum, já traduzida dos campos nativos de cada provider."""

    provider: str                        # "anthropic" | "ollama"
    modelo_solicitado: str
    sucesso: bool
    modelo_resposta: str = ""            # message.model / resposta["model"]
    tokens_entrada: int | None = None    # usage.input_tokens / prompt_eval_count
    tokens_saida: int | None = None      # usage.output_tokens / eval_count
    stop_reason: str = ""                # stop_reason / done_reason
    request_id: str = ""                 # message.id (anthropic); vazio em ollama
    num_thread: int | None = None        # só ollama
    num_ctx: int | None = None           # só ollama
    chars_enviados: int | None = None
    error_message: str = ""
    # Nanossegundos de geração pura reportados pelo próprio Ollama
    # (`eval_duration`) — usado só para tokens/segundo, porque é mais preciso
    # que `duration_ms` de parede (que inclui rede/fila e pode até chegar a
    # zero numa chamada rápida o bastante).
    eval_duration_ns: int | None = None


def _maquina_local_id():
    """A `Maquina` desta instância, se `CLUSTER_MACHINE_ID` estiver configurado.

    Mesmo padrão de `apps.artifacts.signals_replicacao._origem_maquina_atual` —
    usado aqui só para chamadas anthropic, que não têm máquina executora (não
    rodam na nossa infraestrutura): a única leitura de "onde" que faz sentido
    registrar é qual dos nossos nós emitiu a chamada.
    """
    from django.conf import settings

    machine_id = getattr(settings, "CLUSTER_MACHINE_ID", "")
    if not machine_id:
        return None
    from apps.cluster.models import Maquina

    maquina = Maquina.objects.filter(id=machine_id).first()
    return maquina.id if maquina else None


def registrar_chamada_llm(
    *, finalidade: str, resultado: ResultadoLLM, duration_ms: int,
    maquina_id=None, subject_type: str = "artifact", subject_id=None,
    tenant_id=None,
) -> "ChamadaLLM | None":  # noqa: F821
    """Registra uma chamada de LLM: emite o evento `llm.chamada` e grava a
    projeção `ChamadaLLM` (fora da transação do `emit()`, mesmo padrão de
    `apps.cluster.projecao.aplicar_heartbeat`/`aplicar_metrica_llm` — uma
    janela mínima entre as duas escritas é aceitável e recuperável via
    `manage.py reconstruir_chamadas_llm`).

    Para ollama bem-sucedido, também alimenta
    `apps.cluster.projecao.aplicar_metrica_llm` — a média corrida que o
    roteador usa para escolher a máquina mais rápida — sem duplicar a
    matemática da média, só reaproveitando o mesmo evento.

    `maquina_id`: para ollama é a máquina EXECUTORA (já resolvida pelo
    chamador via `apps.cluster.llm_router.escolher_execucao`). Para
    anthropic, quando não vier explícito, resolve sozinha a máquina local
    emissora via `CLUSTER_MACHINE_ID`.
    """
    try:
        from apps.events.emit import emit

        from .projecao import aplicar_chamada_llm

        if maquina_id is None and resultado.provider == "anthropic":
            maquina_id = _maquina_local_id()

        if not resultado.sucesso:
            status = "falhou"
        elif resultado.error_message:
            # A chamada em si funcionou, só o que veio de volta não deu pra
            # usar (JSON inválido) — "vazio" é o vocabulário certo aqui, não
            # "falhou": rodou e não produziu, a distinção central do modelo.
            status = "vazio"
        else:
            status = "ok"

        tokens_por_segundo = None
        if (
            resultado.provider == "ollama" and resultado.sucesso
            and resultado.tokens_saida and resultado.eval_duration_ns
        ):
            tokens_por_segundo = round(resultado.tokens_saida / (resultado.eval_duration_ns / 1e9), 2)

        payload = {
            "provider": resultado.provider,
            "finalidade": finalidade,
            "modelo_solicitado": resultado.modelo_solicitado,
            "modelo_resposta": resultado.modelo_resposta,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_saida": resultado.tokens_saida,
            "chars_enviados": resultado.chars_enviados,
            "stop_reason": resultado.stop_reason,
            "request_id": resultado.request_id,
            "num_thread": resultado.num_thread,
            "num_ctx": resultado.num_ctx,
            "maquina_id": str(maquina_id) if maquina_id else None,
            "tokens_por_segundo": tokens_por_segundo,
        }

        evento = emit(
            "llm.chamada", status,
            subject_type=subject_type, subject_id=subject_id, tenant_id=tenant_id,
            message=f"{resultado.provider}:{resultado.modelo_solicitado} — {finalidade}",
            payload=payload,
            error=resultado.error_message,
            duration_ms=duration_ms,
        )
        if evento is None:
            return None

        chamada = aplicar_chamada_llm(evento)

        if resultado.provider == "ollama" and resultado.sucesso:
            from apps.cluster.projecao import aplicar_metrica_llm
            aplicar_metrica_llm(evento)

        return chamada
    except Exception:
        logger.exception(
            "registrar_chamada_llm falhou — finalidade=%s provider=%s",
            finalidade, getattr(resultado, "provider", "?"),
        )
        return None

import json
import logging

from django.conf import settings

from .llm_common import _extract_json, _get_client

logger = logging.getLogger(__name__)


_PAGE_TYPES = {
    "artigo": "Texto narrativo: notícias, reportagens, artigos de blog",
    "tabular_financeiro": "Extratos bancários, faturas, histórico de transações com datas e valores monetários",
    "tabular_generico": "Tabelas estruturadas sem padrão financeiro claro (licitações, rankings, resultados)",
    "processo_judicial": "Páginas de tribunais com número CNJ, partes, movimentações processuais",
    "perfil_pessoa_juridica": "Fichas cadastrais de empresas: CNPJ, razão social, sócios, situação",
    "documento_juridico": "Contratos, petições, decisões judiciais em prosa formal numerada",
    "misto": "Combinação significativa de texto narrativo e tabelas (relatórios anuais)",
    "desconhecido": "Nenhum dos tipos anteriores se aplica claramente",
}

# ── Prompts ──────────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "Você é um classificador de páginas HTML capturadas por um investigador jornalístico.\n"
    "Recebe o esqueleto estrutural comprimido do HTML e a URL da página.\n"
    "Retorne APENAS um objeto JSON válido. Sem markdown, sem texto antes ou depois do JSON.\n\n"
    "{\n"
    '  "page_type": "<um dos tipos listados>",\n'
    '  "confidence": <float 0.0–1.0>,\n'
    '  "reasoning": "<uma frase curta explicando a classificação>",\n'
    '  "hints": {\n'
    '    "primary_selector": "<seletor CSS do elemento principal ou null>",\n'
    '    "key_labels": ["<label1>", "<label2>"]\n'
    "  }\n"
    "}\n\n"
    "Tipos disponíveis:\n"
    + "\n".join(f'- "{k}": {v}' for k, v in _PAGE_TYPES.items())
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _classifier_model() -> str:
    return getattr(settings, "LLM_CLASSIFIER_MODEL", "claude-haiku-4-5")


# ── Funções públicas ──────────────────────────────────────────────────────────

def llm_classify(skeleton: str, url: str, *, artifact_id=None, tenant_id=None) -> tuple[str, float, dict]:
    """Classify page type when structural analysis is uncertain (confidence < 0.75).

    Returns (page_type, confidence, hints). Falls back to ('desconhecido', 0.5, {}) on error.
    Uses the cheaper classifier model — classification only, no extraction.

    Toda chamada é telemetrada via apps.events.llm_telemetria, inclusive
    quando falha — antes, uma exceção de rede/API ficava indistinguível de um
    resultado ('desconhecido', 0.5, {}) legítimo no rastro de eventos.
    """
    from time import perf_counter

    from apps.events.llm_telemetria import ResultadoLLM, registrar_chamada_llm
    from apps.events.models import Finalidade

    client = _get_client()
    if not client:
        logger.warning("llm_classify: ANTHROPIC_API_KEY não configurada")
        return "desconhecido", 0.5, {}

    model = _classifier_model()
    chars_enviados = len(url) + len(skeleton)
    logger.info("llm_classify — url=%s skeleton_kb=%.1f model=%s",
                url, len(skeleton.encode()) / 1024, model)

    t0 = perf_counter()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=512,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": f"URL: {url}\n\nEsqueleto HTML:\n{skeleton}"}],
        )
    except Exception as exc:
        registrar_chamada_llm(
            finalidade=Finalidade.CLASSIFICACAO_AUTOMATICA,
            duration_ms=int((perf_counter() - t0) * 1000),
            subject_id=artifact_id, tenant_id=tenant_id,
            resultado=ResultadoLLM(
                provider="anthropic", modelo_solicitado=model, sucesso=False,
                chars_enviados=chars_enviados, error_message=str(exc),
            ),
        )
        logger.exception("llm_classify falhou")
        return "desconhecido", 0.5, {}

    duration_ms = int((perf_counter() - t0) * 1000)

    def _registrar(error_message: str = "") -> None:
        registrar_chamada_llm(
            finalidade=Finalidade.CLASSIFICACAO_AUTOMATICA, duration_ms=duration_ms,
            subject_id=artifact_id, tenant_id=tenant_id,
            resultado=ResultadoLLM(
                provider="anthropic", modelo_solicitado=model, sucesso=True,
                modelo_resposta=message.model, tokens_entrada=message.usage.input_tokens,
                tokens_saida=message.usage.output_tokens, stop_reason=message.stop_reason or "",
                request_id=message.id, chars_enviados=chars_enviados, error_message=error_message,
            ),
        )

    try:
        data = _extract_json(message.content[0].text)

        page_type = data.get("page_type", "desconhecido")
        if page_type not in _PAGE_TYPES:
            page_type = "desconhecido"

        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.5))))
        hints = data.get("hints") or {}

        _registrar()
        logger.info("llm_classify — page_type=%s confidence=%.2f reasoning=%s",
                    page_type, confidence, data.get("reasoning", ""))
        return page_type, confidence, hints

    except json.JSONDecodeError:
        _registrar(error_message="resposta não é JSON válido")
        logger.warning("llm_classify: resposta não é JSON válido")
        return "desconhecido", 0.5, {}
    except Exception:
        _registrar(error_message="erro ao interpretar a resposta")
        logger.exception("llm_classify falhou")
        return "desconhecido", 0.5, {}

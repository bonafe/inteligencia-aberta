import json
import logging
from datetime import datetime, timezone

from django.conf import settings

from .llm_common import _EXTRACT_SYSTEM, _extract_json, _get_client

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


def _extractor_model() -> str:
    # Extraction needs higher quality — default to Sonnet (one-time cost per URL pattern)
    return getattr(settings, "LLM_EXTRACTOR_MODEL", "claude-sonnet-5")


# ── Funções públicas ──────────────────────────────────────────────────────────

def llm_classify(skeleton: str, url: str) -> tuple[str, float, dict]:
    """Classify page type when structural analysis is uncertain (confidence < 0.75).

    Returns (page_type, confidence, hints). Falls back to ('desconhecido', 0.5, {}) on error.
    Uses the cheaper classifier model — classification only, no extraction.
    """
    client = _get_client()
    if not client:
        logger.warning("llm_classify: ANTHROPIC_API_KEY não configurada")
        return "desconhecido", 0.5, {}

    model = _classifier_model()
    logger.info("llm_classify — url=%s skeleton_kb=%.1f model=%s",
                url, len(skeleton.encode()) / 1024, model)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=512,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": f"URL: {url}\n\nEsqueleto HTML:\n{skeleton}"}],
        )
        data = _extract_json(message.content[0].text)

        page_type = data.get("page_type", "desconhecido")
        if page_type not in _PAGE_TYPES:
            page_type = "desconhecido"

        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.5))))
        hints = data.get("hints") or {}

        logger.info("llm_classify — page_type=%s confidence=%.2f reasoning=%s",
                    page_type, confidence, data.get("reasoning", ""))
        return page_type, confidence, hints

    except json.JSONDecodeError:
        logger.warning("llm_classify: resposta não é JSON válido")
        return "desconhecido", 0.5, {}
    except Exception:
        logger.exception("llm_classify falhou")
        return "desconhecido", 0.5, {}


def llm_extract_and_schema(skeleton: str, url: str, page_type_hint: str = "") -> dict:
    """First-capture extraction: understand the page, extract structured data, generate schema.

    This replaces the deterministic extractor on first capture when allow_external_llm=True.
    Returns a dict with keys: categoria, page_type, structured_data, schema. Never returns
    "text" — the search text always comes from extract_narrative_text() (trafilatura), run
    independently of this call, so a partial or malformed LLM response never degrades the
    text used for embedding/search.
    Returns {} on any error (caller falls back to deterministic extractor).

    Uses the higher-quality extractor model (default: Sonnet) since this is a one-time
    cost per URL pattern — the schema it produces is reused on all subsequent captures.
    """
    client = _get_client()
    if not client:
        logger.warning("llm_extract_and_schema: ANTHROPIC_API_KEY não configurada")
        return {}

    model = _extractor_model()
    skeleton_kb = len(skeleton.encode()) / 1024
    logger.info("llm_extract_and_schema — url=%s skeleton_kb=%.1f model=%s page_type_hint=%s",
                url, skeleton_kb, model, page_type_hint)

    hint_line = f"Dica da análise estrutural: {page_type_hint}\n\n" if page_type_hint else ""
    user_prompt = f"{hint_line}URL: {url}\n\nEsqueleto HTML:\n{skeleton}"

    try:
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        data = _extract_json(message.content[0].text)

        categoria = data.get("categoria", "")
        page_type = data.get("page_type", page_type_hint or "desconhecido")
        if page_type not in _PAGE_TYPES:
            page_type = page_type_hint or "desconhecido"

        structured_data = data.get("structured_data") or {}
        schema = data.get("schema") or {}

        if schema:
            schema["generated_by"] = "llm"
            schema["model"] = model
            schema["categoria"] = categoria
            schema["generated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            "llm_extract_and_schema — categoria='%s' page_type=%s "
            "structured_keys=%d schema_fields=%d schema_tables=%d",
            categoria, page_type,
            len(structured_data), len(schema.get("fields", {})), len(schema.get("tables", [])),
        )

        return {
            "categoria": categoria,
            "page_type": page_type,
            "structured_data": structured_data or None,
            "schema": schema,
        }

    except json.JSONDecodeError:
        logger.warning("llm_extract_and_schema: resposta não é JSON válido")
        return {}
    except Exception:
        logger.exception("llm_extract_and_schema falhou")
        return {}

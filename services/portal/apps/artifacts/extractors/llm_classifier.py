import json
import logging
import re
from datetime import datetime, timezone

from django.conf import settings

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Parse JSON from LLM output, tolerating markdown code fences and surrounding text."""
    text = text.strip()

    fence = _CODE_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(text)
    if match:
        return json.loads(match.group(0))

    raise json.JSONDecodeError("no JSON object found", text, 0)


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

_EXTRACT_SYSTEM = """\
Você é um extrator de dados inteligente para uma plataforma de jornalismo investigativo brasileiro.

Você recebe o esqueleto HTML comprimido (até 20 KB) de uma página capturada. A página pode conter \
qualquer tipo de informação: extrato bancário, ficha de empresa, processo judicial, notícia, \
resultado médico, tabela de licitações, planilha de dados públicos — qualquer coisa.

Sua tarefa, em ordem:

1. CATEGORIZAR — Descreva em UMA FRASE específica o que esta página contém.
   Não "tabela financeira" mas "Extrato de conta corrente do Banco do Brasil, março 2025".
   Não "página de empresa" mas "Ficha cadastral da empresa XYZ LTDA na Receita Federal".

2. EXTRAIR — Extraia todos os dados estruturados visíveis. Para listas longas, limite a 100 itens.
   Use nomes de campo em português. Estruture conforme o conteúdo — não há formato fixo.

3. SCHEMA — Gere seletores CSS para automatizar esta extração em capturas futuras com a mesma estrutura.

Você NÃO precisa gerar texto narrativo para busca — o sistema sempre extrai o texto
de busca da página com trafilatura, de forma independente da sua resposta. Foque
inteiramente em extrair dados estruturados corretos e um schema reutilizável.

RETORNE APENAS O JSON ABAIXO. Nada antes, nada depois, sem markdown.

{
  "categoria": "<descrição específica em uma frase>",
  "page_type": "<artigo|tabular_financeiro|tabular_generico|processo_judicial|perfil_pessoa_juridica|documento_juridico|misto|desconhecido>",
  "structured_data": {
    "<campo_em_portugues>": "<valor ou lista ou objeto aninhado conforme o conteúdo>"
  },
  "schema": {
    "version": "1.0",
    "fields": {
      "<nome>": {"selector": "<seletor CSS>", "transform": "<text|brl_float|date_br|attr:href>"}
    },
    "tables": [
      {
        "selector": "<seletor CSS da tabela>",
        "columns": {
          "<nome_coluna>": {"index": <int>, "transform": "<transform>"}
        }
      }
    ]
  }
}

Regras para o schema:
- Seletores simples: classes (.foo), IDs (#bar), nth-child, atributos ([data-x="y"])
- PROIBIDO: :contains() — não é suportado pelo parser
- Sem campos individuais relevantes → "fields": {}
- Sem tabelas → "tables": []
"""

# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_client():
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


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

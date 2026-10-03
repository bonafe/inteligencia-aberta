"""Estruturação manual: extração de dados estruturados por LLM disparada sob
demanda pelo visualizador (não roda automaticamente no pipeline de captura —
ver apps.artifacts.tasks.extract_text_from_mhtml), capaz de falar com
qualquer provider (Claude ou Ollama) via gerar_texto().

Puramente funcional — nunca grava nada; quem chama decide onde persistir.
"""
from .llm_common import _EXTRACT_SYSTEM, _extract_json, gerar_texto


def estruturar_manual(
    provider: str, model_name: str, skeleton: str, url: str, page_type_hint: str = "",
    *, subject_id=None, tenant_id=None,
) -> dict:
    """Retorna {categoria, page_type, structured_data, maquina_id}. Levanta em
    erro de chamada (rede indisponível, parse impossível) — quem chama decide
    vazio vs falhou."""
    from apps.events.models import Finalidade

    hint_line = f"Dica da análise estrutural: {page_type_hint}\n\n" if page_type_hint else ""
    user_prompt = f"{hint_line}URL: {url}\n\nEsqueleto HTML:\n{skeleton}"

    texto, maquina_id = gerar_texto(
        provider, model_name, _EXTRACT_SYSTEM, user_prompt, max_tokens=4096,
        finalidade=Finalidade.ESTRUTURACAO_MANUAL, subject_id=subject_id, tenant_id=tenant_id,
    )
    data = _extract_json(texto)

    return {
        "categoria": data.get("categoria", ""),
        "page_type": data.get("page_type", page_type_hint or "desconhecido"),
        "structured_data": data.get("structured_data") or None,
        "maquina_id": maquina_id,
    }

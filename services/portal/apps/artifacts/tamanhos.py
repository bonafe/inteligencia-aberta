"""Tamanho de cada representação que o pipeline produz para a mesma página —
do MHTML bruto até o JSON estruturado final. `montar()` roda uma vez em
tasks.py e o resultado é persistido em DocumentText.tamanhos; a view só
decora com os labels para o gráfico de barras da galeria.

`None` distingue "esta etapa não rodou/não persiste" (ex.: extruct não achou
nada) de um valor baixo de verdade; o gráfico trata os dois como "sem dado".
"""
import json

LABELS = {
    "mhtml": "MHTML bruto",
    "html": "HTML decodificado",
    "texto": "Texto (trafilatura)",
    "texto_completo": "Texto completo",
    "dom2parser_repr": "Representação dom2parser",
    "dom2parser_dados": "Dados estruturados (dom2parser)",
    "extruct_dados": "Dados estruturados (extruct)",
    "deterministico_dados": "Dados estruturados (determinístico)",
    # Vencedora entre as estratégias acima — decisão adiada de propósito, ver
    # DocumentText.structured_data. Fica "sem dado" até esse processo existir.
    "structured_data": "Dados estruturados (final, pendente)",
}


def _bytes_utf8(texto):
    return len(texto.encode("utf-8")) if texto else None


def _bytes_json(valor):
    return len(json.dumps(valor, ensure_ascii=False).encode("utf-8")) if valor else None


def montar(
    *, mhtml_bytes, html_content, text, full_text, dom_representation,
    structured_data, dados_estruturados_dom2parser, dados_estruturados_extruct,
    dados_estruturados_deterministico,
) -> dict:
    return {
        "mhtml": len(mhtml_bytes) if mhtml_bytes else None,
        "html": _bytes_utf8(html_content),
        "texto": _bytes_utf8(text),
        "texto_completo": _bytes_utf8(full_text),
        "dom2parser_repr": _bytes_utf8(dom_representation),
        "dom2parser_dados": _bytes_json(dados_estruturados_dom2parser),
        "extruct_dados": _bytes_json(dados_estruturados_extruct),
        "deterministico_dados": _bytes_json(dados_estruturados_deterministico),
        "structured_data": _bytes_json(structured_data),
    }


def para_grafico(tamanhos: dict | None) -> list[dict]:
    tamanhos = tamanhos or {}
    return [{"chave": chave, "label": label, "bytes": tamanhos.get(chave)}
            for chave, label in LABELS.items()]

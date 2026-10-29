"""HTML enxuto: a página inteira, sem o que não é dado, para o LLM ler.

Entrada da estruturação manual por LLM (`estruturacao_manual.py`). O texto
puro perde a estrutura — numa tabela cada célula vira uma linha solta e o
modelo deixa de saber de que bloco é cada valor —, e o esqueleto do
dom2parser só traz amostras. O HTML enxuto mantém tabelas, linhas, células e
as classes/ids que dizem o que é cada parte (`tituloLancamento`, `total`…).
Medido numa fatura de cartão: 174 mil caracteres de HTML viram 23 mil, menos
que o próprio texto visível (25 mil), e o modelo acertou 134 de 134
lançamentos contra 132 com o texto e 7 com o esqueleto.

Reaproveita a sanitização do dom2parser (script/style/svg/comentários, menus,
conteúdo oculto por estilo inline, atributos visuais), **menos** o
`dedup_identical_subtrees`: ele junta subárvores idênticas e apagaria duas
compras iguais no mesmo dia. Por cima, uma passada mais agressiva só para o
LLM: lista curta de atributos, sem formulário/imagem, tags de formatação
desembrulhadas, sem elementos e linhas vazios.

Determinístico e sem Django — pode ser testado isoladamente.
"""

from __future__ import annotations

import re

#: Atributos que carregam sentido para o LLM; o resto (href, data-*, name…) só gasta tokens.
ATRIBUTOS_MANTIDOS = frozenset({"class", "id", "colspan", "rowspan", "aria-label", "title", "alt"})
#: Removidos com o conteúdo.
TAGS_REMOVIDAS = ("head", "iframe", "img", "input", "select", "option", "button", "textarea", "br", "hr")
#: Só formatação: some a tag, fica o texto.
TAGS_DESEMBRULHADAS = ("span", "font", "b", "strong", "i", "em", "u", "small", "a", "label", "nobr", "center")
#: Células vazias ficam: tiram uma coluna do lugar se somem.
_VAZIOS_MANTIDOS = frozenset({"td", "th", "html", "body"})

_ESPACOS = re.compile(r"[ \t\r\f\v]+")
_QUEBRAS = re.compile(r"\s*\n\s*")
_ENTRE_TAGS = re.compile(r">\s+<")
_INICIO_DE_BLOCO = re.compile(r"(<(?:tr|table|div|li|h\d|p|section|article|dl|dt|dd)\b)")


def enxugar(html: str) -> str:
    """Devolve o HTML enxuto, uma linha por bloco/linha de tabela. String vazia se não houver nada."""
    from dom2parser.html_io import parse_html
    from dom2parser.sanitize import attrs, boilerplate, strip
    from lxml import etree

    if not html or not html.strip():
        return ""

    root = strip.sanitize(parse_html(html))
    boilerplate.strip_navigation(root)
    boilerplate.strip_hidden_by_inline_style(root)
    attrs.strip_noisy_attrs(root)

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for nome in list(el.attrib):
            if nome not in ATRIBUTOS_MANTIDOS or not el.attrib[nome].strip():
                del el.attrib[nome]
    etree.strip_elements(root, *TAGS_REMOVIDAS, with_tail=False)
    etree.strip_tags(root, *TAGS_DESEMBRULHADAS)

    for tr in list(root.iter("tr")):
        if not "".join(tr.itertext()).strip():
            tr.getparent().remove(tr)
    # De baixo para cima: um pai que só tinha filhos vazios fica vazio e sai na mesma passada.
    for el in reversed(list(root.iter())):
        if (el is root or not isinstance(el.tag, str) or el.tag in _VAZIOS_MANTIDOS
                or len(el) or (el.text or "").strip()):
            continue
        pai = el.getparent()
        if pai is not None:
            _remover_preservando_tail(el, pai)

    saida = etree.tostring(root, encoding="unicode", method="html")
    saida = _ESPACOS.sub(" ", saida)
    saida = _QUEBRAS.sub("\n", saida)
    saida = _ENTRE_TAGS.sub("><", saida)
    return _INICIO_DE_BLOCO.sub(r"\n\1", saida).strip()


def _remover_preservando_tail(el, pai) -> None:
    """Remove `el` sem perder o texto que vem depois dele (o `tail`, no lxml)."""
    if el.tail:
        anterior = el.getprevious()
        if anterior is not None:
            anterior.tail = (anterior.tail or "") + el.tail
        else:
            pai.text = (pai.text or "") + el.tail
    pai.remove(el)

"""Shadow DOM gravado no MHTML.

O Chrome serializa cada shadow root aberto como `<template shadowmode="open">` dentro do host.
`shadowmode` é o nome antigo do atributo do shadow DOM declarativo: o navegador atual só reconhece
`shadowrootmode` e, sem ele, o bloco é um `<template>` inerte, que não aparece na tela e cujo
texto os extratores (BeautifulSoup, trafilatura, lxml) não leem. Páginas feitas de web components
(o extrato novo do BB, por exemplo) ficavam com o conteúdo inteiro aí dentro.

- `declarar_shadow_dom`: para exibir; o navegador volta a anexar os shadow roots.
- `achatar_shadow_dom`: para extrair; o conteúdo do shadow root passa a ser filho do host, como
  se fosse DOM comum. Os filhos do próprio host (o que iria nos `<slot>`) ficam depois dele.
"""
import re

_TAG_TEMPLATE = re.compile(r"<(/?)template\b([^>]*)>", re.IGNORECASE)
_ATRIBUTO_SHADOW = re.compile(r"\bshadow(?:root)?mode\s*=", re.IGNORECASE)
_SHADOWMODE_ANTIGO = re.compile(r"(<template\b[^>]*?\s)shadowmode(\s*=)", re.IGNORECASE)


def declarar_shadow_dom(html: str) -> str:
    return _SHADOWMODE_ANTIGO.sub(r"\1shadowrootmode\2", html)


def achatar_shadow_dom(html: str) -> str:
    """Tira as tags `<template>` de shadow root e mantém o conteúdo; os outros `<template>` ficam."""
    if not _ATRIBUTO_SHADOW.search(html):
        return html
    partes: list[str] = []
    abertos: list[bool] = []        # para cada <template> aberto: é shadow root?
    fim = 0
    for m in _TAG_TEMPLATE.finditer(html):
        fechando, atributos = m.group(1) == "/", m.group(2)
        if fechando:
            shadow = abertos.pop() if abertos else False
        else:
            shadow = bool(_ATRIBUTO_SHADOW.search(atributos))
            abertos.append(shadow)
        if shadow:
            partes.append(html[fim:m.start()])
            fim = m.end()
    partes.append(html[fim:])
    return "".join(partes)

"""Onde uma extensão guarda o que descobriu sobre um artefato do núcleo.

`content["extensoes"][<nome da extensão>]` é um espaço por extensão: ela escreve só ali, o núcleo e as
outras extensões não o tocam. É informação derivada (proveniência), não fato do sistema — fatos viram
alegações (`registrar_alegacao`)."""


def anexar(artefato, extensao: str, dados: dict) -> None:
    mapa = dict((artefato.content or {}).get("extensoes", {}))
    mapa[extensao] = dados
    artefato.content = {**(artefato.content or {}), "extensoes": mapa}
    artefato.save(update_fields=["content", "updated_at"])


def ler(artefato, extensao: str) -> dict | None:
    return ((artefato.content or {}).get("extensoes") or {}).get(extensao)

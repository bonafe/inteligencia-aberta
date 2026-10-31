from .registro import acoes_leitura, menu


def extensoes(request):
    """O menu das extensões instaladas (`base.html`) e as ações de leitura (visualizador)."""
    return {"menu_extensoes": menu(), "acoes_leitura": acoes_leitura()}

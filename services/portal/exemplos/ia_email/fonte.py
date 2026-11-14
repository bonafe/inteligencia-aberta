from dataclasses import asdict

from apps.artifacts import acervo


def importar(*, organizacao, usuario, caminho, nivel, simular, opcoes):
    """Guarda os `.eml` pelo serviço público do acervo; o resumo é o do acervo."""
    raiz = caminho.parent if caminho.is_file() else caminho
    resumo = acervo.importar_pasta(organizacao=organizacao, usuario=usuario, base=caminho, raiz=str(raiz),
                                   nivel=nivel, simular=simular, projetos=False)
    return {k: v for k, v in asdict(resumo).items() if k != "erros"} | {"erros": resumo.total_erros}

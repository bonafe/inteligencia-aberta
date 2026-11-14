async def buscar_noticias(termo: str) -> dict:
    # TODO Fase 0: integrar com NewsAPI ou RSS de fontes públicas
    return {"termo": termo, "resultados": []}


FERRAMENTA = {
    "id": "buscar_noticias",
    "descricao": "Notícias sobre um termo. (Ainda não integrada: devolve lista vazia.)",
    "fonte": "",
    "parametros": {"termo": {"tipo": "string", "obrigatorio": True, "descricao": "assunto da busca"}},
    "classificacao_maxima": "publico",
    "executar": buscar_noticias,
}

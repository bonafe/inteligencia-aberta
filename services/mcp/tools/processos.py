async def buscar_processos(termo: str) -> dict:
    # TODO Fase 0: integrar com DataJud (CNJ) ou similar
    return {"termo": termo, "resultados": []}


FERRAMENTA = {
    "id": "buscar_processos",
    "descricao": "Processos judiciais por termo. (Ainda não integrada: devolve lista vazia.)",
    "fonte": "",
    "parametros": {"termo": {"tipo": "string", "obrigatorio": True, "descricao": "nome, CPF/CNPJ ou número"}},
    "classificacao_maxima": "publico",
    "executar": buscar_processos,
}

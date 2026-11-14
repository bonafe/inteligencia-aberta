import httpx


async def consultar_cnpj(cnpj: str) -> dict:
    cnpj_clean = "".join(filter(str.isdigit, cnpj))
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_clean}")
        response.raise_for_status()
        return response.json()


FERRAMENTA = {
    "id": "consultar_cnpj",
    "descricao": "Dados cadastrais de uma empresa pelo CNPJ (BrasilAPI, base da Receita Federal).",
    "fonte": "BrasilAPI / Receita Federal",
    "parametros": {"cnpj": {"tipo": "string", "obrigatorio": True, "descricao": "CNPJ, com ou sem pontuação"}},
    "classificacao_maxima": "publico",      # o CNPJ vai para um serviço externo
    "executar": consultar_cnpj,
    "timeout_s": 10.0,
}

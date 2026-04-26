import httpx


async def consultar_cnpj(cnpj: str) -> dict:
    cnpj_clean = "".join(filter(str.isdigit, cnpj))
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_clean}")
        response.raise_for_status()
        return response.json()

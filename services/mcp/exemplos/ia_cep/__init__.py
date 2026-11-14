"""Exemplo de plugin de ferramentas do MCP (ADR 023): duas ferramentas, uma que sai para a internet e
outra puramente local. Liga-se com `FERRAMENTAS_MCP=ia_cep` (o pacote no PYTHONPATH ou instalado com pip)."""
import httpx


async def consultar_cep(cep: str) -> dict:
    cep = "".join(filter(str.isdigit, cep))
    if len(cep) != 8:
        raise ValueError("o CEP precisa de 8 dígitos")
    async with httpx.AsyncClient(timeout=8) as cliente:
        resposta = await cliente.get(f"https://viacep.com.br/ws/{cep}/json/")
        resposta.raise_for_status()
        return resposta.json()


async def contar_palavras(texto: str) -> dict:
    return {"palavras": len(texto.split()), "caracteres": len(texto)}


FERRAMENTAS = [
    {
        "id": "consultar_cep",
        "descricao": "Endereço de um CEP (ViaCEP).",
        "fonte": "ViaCEP",
        "parametros": {"cep": {"tipo": "string", "obrigatorio": True, "descricao": "8 dígitos, com ou sem hífen"}},
        "classificacao_maxima": "publico",         # o CEP vai para um serviço externo
        "executar": consultar_cep,
        "timeout_s": 10.0,
    },
    {
        "id": "contar_palavras",
        "descricao": "Conta palavras e caracteres de um texto, sem sair desta máquina.",
        "parametros": {"texto": {"tipo": "string", "obrigatorio": True, "descricao": "o texto"}},
        "classificacao_maxima": "confidencial",    # não envia nada para fora: aceita qualquer nível
        "executar": contar_palavras,
    },
]

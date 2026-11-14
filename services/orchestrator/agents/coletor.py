"""Coletor: executa as etapas do plano chamando as ferramentas do MCP (docs/componentes/agentes/coletor.md).

Entrada: `state["plan"]["etapas"]`, cada uma `{"ferramenta": "<id>", "parametros": {...}}`.
Saída: `state["collected_data"]`, **um item por etapa, na ordem do plano**, sempre com o desfecho: o que veio
(`status "ok"`, `resultado`, `fonte`, `confianca`) ou o erro (`status "erro"`, `codigo`). Falha de uma fonte não
interrompe as demais, e nada é descartado: quem decide se a investigação segue é o orquestrador.
"""
import asyncio
import os

import mcp_cliente

CONCORRENCIA = int(os.getenv("COLETOR_CONCORRENCIA", "5"))


async def coletar(state: dict) -> dict:
    etapas = (state.get("plan") or {}).get("etapas", [])
    limite = asyncio.Semaphore(CONCORRENCIA)

    async def executar(etapa: dict) -> dict:
        ferramenta = etapa.get("ferramenta") if isinstance(etapa, dict) else None
        parametros = etapa.get("parametros", {}) if isinstance(etapa, dict) else {}
        if not ferramenta or not isinstance(parametros, dict):
            return {"ferramenta": ferramenta, "parametros": parametros, **mcp_cliente._erro(
                "PARAMETRO_INVALIDO", "etapa do plano sem `ferramenta` ou com `parametros` que não é objeto")}
        async with limite:
            resposta = await mcp_cliente.chamar(
                ferramenta, parametros, classificacao=state.get("classification", "confidencial"),
                tenant_id=state["tenant_id"], correlacao=str(state.get("correlation_id", "")),
                user_id=state.get("user_id"), investigacao_id=state.get("investigation_id"))
        return {"ferramenta": ferramenta, "parametros": parametros, **resposta}

    state["collected_data"] = list(await asyncio.gather(*(executar(e) for e in etapas)))
    return state

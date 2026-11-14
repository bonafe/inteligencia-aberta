"""Coletor: executa o plano pelas ferramentas, sem deixar a falha de uma fonte parar as outras."""
import asyncio

import mcp_cliente
from agents import coletor


def estado(etapas, **kw):
    return {"plan": {"etapas": etapas}, "tenant_id": "t1", "classification": "publico", "correlation_id": "c1",
            "user_id": "u1", "investigation_id": "i1", **kw}


async def test_uma_entrada_por_etapa_na_ordem_do_plano_mesmo_com_falhas(monkeypatch):
    async def falso(ferramenta, parametros, **kw):
        if ferramenta == "fora":
            return {"status": "erro", "codigo": "FONTE_INDISPONIVEL", "mensagem": "x", "recuperavel": True}
        return {"status": "ok", "resultado": {"eco": parametros}, "fonte": {"origem": ferramenta}, "confianca": None}

    monkeypatch.setattr(mcp_cliente, "chamar", falso)
    saida = await coletor.coletar(estado([
        {"ferramenta": "a", "parametros": {"n": 1}}, {"ferramenta": "fora", "parametros": {}}, {"ferramenta": "b", "parametros": {}}]))
    dados = saida["collected_data"]
    assert [d["ferramenta"] for d in dados] == ["a", "fora", "b"]
    assert [d["status"] for d in dados] == ["ok", "erro", "ok"]
    assert dados[0]["resultado"] == {"eco": {"n": 1}} and dados[1]["codigo"] == "FONTE_INDISPONIVEL"


async def test_o_contexto_da_investigacao_chega_a_ferramenta(monkeypatch):
    recebido = {}

    async def falso(ferramenta, parametros, **kw):
        recebido.update(kw)
        return {"status": "ok", "resultado": {}}

    monkeypatch.setattr(mcp_cliente, "chamar", falso)
    await coletor.coletar(estado([{"ferramenta": "a", "parametros": {}}]))
    assert recebido == {"classificacao": "publico", "tenant_id": "t1", "correlacao": "c1", "user_id": "u1", "investigacao_id": "i1"}


async def test_sem_classificacao_no_estado_vale_a_mais_restrita(monkeypatch):
    visto = {}

    async def falso(ferramenta, parametros, **kw):
        visto.update(kw)
        return {"status": "ok"}

    monkeypatch.setattr(mcp_cliente, "chamar", falso)
    s = estado([{"ferramenta": "a", "parametros": {}}])
    del s["classification"]
    await coletor.coletar(s)
    assert visto["classificacao"] == "confidencial"


async def test_etapa_malformada_vira_erro_sem_chamar_nada(monkeypatch):
    async def nao_deve(*a, **k):
        raise AssertionError("não deveria chamar")

    monkeypatch.setattr(mcp_cliente, "chamar", nao_deve)
    saida = await coletor.coletar(estado([{"parametros": {}}, "texto solto", {"ferramenta": "a", "parametros": "x"}]))
    assert [d["codigo"] for d in saida["collected_data"]] == ["PARAMETRO_INVALIDO"] * 3


async def test_concorrencia_e_limitada(monkeypatch):
    ativas, maximo = 0, 0

    async def falso(ferramenta, parametros, **kw):
        nonlocal ativas, maximo
        ativas += 1
        maximo = max(maximo, ativas)
        await asyncio.sleep(0.01)
        ativas -= 1
        return {"status": "ok"}

    monkeypatch.setattr(mcp_cliente, "chamar", falso)
    monkeypatch.setattr(coletor, "CONCORRENCIA", 2)
    await coletor.coletar(estado([{"ferramenta": "a", "parametros": {}}] * 8))
    assert maximo == 2


async def test_sem_plano_nao_coleta_nada():
    assert (await coletor.coletar({"tenant_id": "t"}))["collected_data"] == []

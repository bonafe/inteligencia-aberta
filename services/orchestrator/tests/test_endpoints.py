"""Endpoints de ferramentas do orchestrator: identidade das claims, política e mapeamento do erro para HTTP."""
import httpx
import pytest
from fastapi.testclient import TestClient

import main
import mcp_cliente


@pytest.fixture
def cliente(monkeypatch):
    main.app.dependency_overrides[main.require_jwt] = lambda: {"user_id": "u1", "tenant_id": "t1"}
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_listar_exige_jwt():
    assert TestClient(main.app).get("/api/v1/ferramentas").status_code == 401


def test_listar_devolve_o_catalogo_do_mcp(cliente, monkeypatch):
    async def catalogo(c=None):
        return [{"id": "consultar_cnpj"}]

    monkeypatch.setattr(mcp_cliente, "catalogo", catalogo)
    assert cliente.get("/api/v1/ferramentas").json() == {"ferramentas": [{"id": "consultar_cnpj"}]}


def test_listar_com_mcp_fora_e_502(cliente, monkeypatch):
    async def catalogo(c=None):
        raise httpx.ConnectError("x")

    monkeypatch.setattr(mcp_cliente, "catalogo", catalogo)
    assert cliente.get("/api/v1/ferramentas").status_code == 502


def test_chamar_usa_a_identidade_das_claims_e_o_padrao_restrito(cliente, monkeypatch):
    visto = {}

    async def chamar(ferramenta, parametros, **kw):
        visto.update(ferramenta=ferramenta, parametros=parametros, **kw)
        return {"status": "ok", "resultado": {"a": 1}}

    monkeypatch.setattr(mcp_cliente, "chamar", chamar)
    r = cliente.post("/api/v1/ferramentas/consultar_cnpj", json={"parametros": {"cnpj": "1"}})
    assert r.status_code == 200 and r.json()["resultado"] == {"a": 1} and r.json()["correlation_id"]
    assert visto["tenant_id"] == "t1" and visto["user_id"] == "u1" and visto["classificacao"] == "restrito"


def test_tenant_do_corpo_nao_vale_so_o_das_claims(cliente, monkeypatch):
    visto = {}

    async def chamar(ferramenta, parametros, **kw):
        visto.update(kw)
        return {"status": "ok"}

    monkeypatch.setattr(mcp_cliente, "chamar", chamar)
    cliente.post("/api/v1/ferramentas/x", json={"parametros": {}, "tenant_id": "de-outro", "classificacao": "publico"})
    assert visto["tenant_id"] == "t1" and visto["classificacao"] == "publico"


@pytest.mark.parametrize("codigo,http", [("BLOQUEADO_POR_POLITICA", 403), ("PARAMETRO_INVALIDO", 422),
                                         ("FERRAMENTA_DESCONHECIDA", 404), ("TIMEOUT", 504),
                                         ("FONTE_INDISPONIVEL", 502), ("MCP_INDISPONIVEL", 502), ("OUTRO", 502)])
def test_erro_do_contrato_vira_o_status_http_certo(cliente, monkeypatch, codigo, http):
    async def chamar(*a, **kw):
        return {"status": "erro", "codigo": codigo, "mensagem": "m", "recuperavel": False}

    monkeypatch.setattr(mcp_cliente, "chamar", chamar)
    r = cliente.post("/api/v1/ferramentas/x", json={"parametros": {}})
    assert r.status_code == http and r.json()["codigo"] == codigo

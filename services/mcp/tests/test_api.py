"""API do MCP: catálogo, chamada no contrato de docs/componentes/mcp.md, política, erros e eventos."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import main

TOKEN = {"X-Mcp-Token": "token-de-teste"}


@pytest.fixture
def cliente(monkeypatch):
    eventos = []
    monkeypatch.setattr(main, "emitir", lambda stage, status, **kw: eventos.append((stage, status, kw)))

    async def ola(nome):
        return {"ola": nome}

    async def quebra():
        raise ConnectionError("fonte fora do ar")

    async def lenta():
        await asyncio.sleep(1)

    ferramentas = main.carregar([
        {"id": "ola", "descricao": "d", "executar": ola, "fonte": "Local", "confianca": 0.9,
         "parametros": {"nome": {"tipo": "string", "obrigatorio": True}}, "classificacao_maxima": "interno"},
        {"id": "quebra", "descricao": "d", "executar": quebra},
        {"id": "lenta", "descricao": "d", "executar": lenta, "timeout_s": 0.05},
    ])
    monkeypatch.setattr(main, "FERRAMENTAS", ferramentas)
    c = TestClient(main.app)
    c.eventos = eventos
    return c


def chamar(c, id_, parametros=None, classificacao="publico", headers=TOKEN):
    return c.post(f"/ferramentas/{id_}", headers=headers,
                  json={"parametros": parametros or {}, "contexto": {"classificacao": classificacao, "tenant_id": "t1"}})


def test_catalogo_exige_token_e_traz_o_esquema(cliente):
    assert cliente.get("/ferramentas").status_code == 401
    assert cliente.get("/ferramentas", headers={"X-Mcp-Token": "errado"}).status_code == 401
    corpo = cliente.get("/ferramentas", headers=TOKEN).json()["ferramentas"]
    assert {f["id"] for f in corpo} == {"ola", "quebra", "lenta"}
    assert all("executar" not in f for f in corpo)
    assert next(f for f in corpo if f["id"] == "ola")["classificacao_maxima"] == "interno"


def test_chamada_ok_segue_o_contrato_e_registra_o_evento(cliente):
    r = chamar(cliente, "ola", {"nome": "ana"}, classificacao="interno")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "ok" and corpo["resultado"] == {"ola": "ana"} and corpo["confianca"] == 0.9
    assert corpo["fonte"]["origem"] == "Local" and corpo["fonte"]["data_coleta"]
    (stage, status, kw), = cliente.eventos
    assert (stage, status) == ("ferramenta.chamada", "ok") and kw["tenant_id"] == "t1" and kw["payload"]["ferramenta"] == "ola"


def test_chamada_sem_token_e_recusada(cliente):
    assert chamar(cliente, "ola", {"nome": "x"}, headers={}).status_code == 401


def test_ferramenta_desconhecida(cliente):
    r = chamar(cliente, "nada")
    assert r.status_code == 404 and r.json()["codigo"] == "FERRAMENTA_DESCONHECIDA"


def test_parametro_invalido(cliente):
    r = chamar(cliente, "ola", {"nome": 3})
    assert r.status_code == 422 and r.json()["codigo"] == "PARAMETRO_INVALIDO"


@pytest.mark.parametrize("classificacao", ["restrito", "confidencial", None, "inventado"])
def test_dado_mais_restrito_que_a_ferramenta_aceita_e_bloqueado_e_a_ferramenta_nem_roda(cliente, classificacao):
    r = chamar(cliente, "ola", {"nome": "x"}, classificacao=classificacao)
    assert r.status_code == 403 and r.json()["codigo"] == "BLOQUEADO_POR_POLITICA"
    (stage, status, kw), = cliente.eventos
    assert status == "ignorado" and kw["payload"]["decisao"] == "BLOQUEADO"


def test_falha_da_fonte_vira_erro_do_contrato_e_evento_falhou(cliente):
    r = chamar(cliente, "quebra")
    assert r.status_code == 502 and r.json()["codigo"] == "FONTE_INDISPONIVEL" and r.json()["recuperavel"] is True
    assert cliente.eventos[-1][1] == "falhou" and "fonte fora do ar" in cliente.eventos[-1][2]["error"]


def test_timeout_da_ferramenta(cliente):
    r = chamar(cliente, "lenta")
    assert r.status_code == 504 and r.json()["codigo"] == "TIMEOUT"


def test_catalogo_real_traz_as_do_projeto_e_nao_as_rotas_antigas():
    real = TestClient(main.app)
    ids = {f["id"] for f in real.get("/ferramentas", headers=TOKEN).json()["ferramentas"]}
    assert {"consultar_cnpj", "buscar_processos", "buscar_noticias"} <= ids
    assert real.get("/tools/cnpj/123", headers=TOKEN).status_code == 404

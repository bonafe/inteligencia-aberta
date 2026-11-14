"""Cliente do MCP: a política antes de sair, o contrato de volta e nenhuma exceção para quem chama."""
import httpx
import pytest

import mcp_cliente

T = "tenant-1"


@pytest.fixture
def registro(monkeypatch):
    eventos, decisoes = [], []
    monkeypatch.setattr(mcp_cliente, "emitir", lambda stage, status, **kw: eventos.append((stage, status, kw)))
    monkeypatch.setattr(mcp_cliente, "registrar_decisao", lambda decisao, **kw: decisoes.append((decisao, kw)))
    return eventos, decisoes


def cliente(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def chamar(c, ferramenta="consultar_cnpj", classificacao="publico", **kw):
    return await mcp_cliente.chamar(ferramenta, {"cnpj": "1"}, classificacao=classificacao, tenant_id=T,
                                    correlacao="corr-1", user_id="u1", cliente=c, **kw)


async def test_chamada_ok_repassa_o_contrato_e_manda_token_correlacao_e_contexto(registro):
    vistos = []

    def handler(req: httpx.Request):
        vistos.append(req)
        return httpx.Response(200, json={"status": "ok", "resultado": {"razao": "X"}, "fonte": {"origem": "RF"}, "confianca": None})

    resposta = await chamar(cliente(handler), investigacao_id="inv-1")
    assert resposta["status"] == "ok" and resposta["resultado"] == {"razao": "X"}
    (req,) = vistos
    assert req.url.path == "/ferramentas/consultar_cnpj" and req.headers["x-mcp-token"] == "token-de-teste"
    assert req.headers["x-correlation-id"] == "corr-1"
    import json
    corpo = json.loads(req.content)
    assert corpo["parametros"] == {"cnpj": "1"}
    assert corpo["contexto"] == {"classificacao": "publico", "tenant_id": T, "user_id": "u1", "investigacao_id": "inv-1"}
    assert registro[1][0][0]["decision"] == "PERMITIDO"            # a decisão ficou no log


async def test_classificacao_desconhecida_e_bloqueada_antes_de_sair(registro):
    chamadas = []
    resposta = await chamar(cliente(lambda req: chamadas.append(req) or httpx.Response(200, json={})), classificacao="inventado")
    assert resposta["status"] == "erro" and resposta["codigo"] == "BLOQUEADO_POR_POLITICA"
    assert chamadas == [] and registro[1][0][0]["decision"] == "BLOQUEADO"


async def test_erro_do_mcp_passa_como_esta(registro):
    corpo = {"status": "erro", "codigo": "BLOQUEADO_POR_POLITICA", "mensagem": "dado restrito", "recuperavel": False}
    resposta = await chamar(cliente(lambda req: httpx.Response(403, json=corpo)))
    assert resposta == corpo


async def test_mcp_fora_do_ar_nao_levanta_e_vira_erro_recuperavel(registro):
    def cai(req):
        raise httpx.ConnectError("sem rota")

    resposta = await chamar(cliente(cai))
    assert resposta["codigo"] == "MCP_INDISPONIVEL" and resposta["recuperavel"] is True
    assert registro[0][-1][1] == "falhou"


@pytest.mark.parametrize("resposta", [
    httpx.Response(500, text="<html>erro</html>"), httpx.Response(200, json=["fora do contrato"]),
    httpx.Response(401, json={"detail": "Token de ferramenta ausente ou inválido"})])
async def test_resposta_estranha_do_mcp_vira_erro_do_contrato(registro, resposta):
    r = await chamar(cliente(lambda req: resposta))
    assert r["status"] == "erro" and r["codigo"] == "MCP_INDISPONIVEL"


async def test_catalogo_lista_as_ferramentas_do_mcp():
    ferramentas = [{"id": "consultar_cnpj", "parametros": {}}]
    c = cliente(lambda req: httpx.Response(200, json={"ferramentas": ferramentas}) if req.url.path == "/ferramentas" else httpx.Response(404))
    assert await mcp_cliente.catalogo(c) == ferramentas


async def test_catalogo_com_mcp_fora_levanta_para_quem_decide():
    with pytest.raises(httpx.HTTPError):
        await mcp_cliente.catalogo(cliente(lambda req: (_ for _ in ()).throw(httpx.ConnectError("x"))))

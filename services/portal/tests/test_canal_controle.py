"""Canal de controle assinado entre instâncias (ADR 011, Marco B): rotas `ping` e `estado`, autorização por tipo de par,
limite de taxa, recusas uniformes e o cliente `chamar_par`."""
import json
import re
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.cluster import pares
from apps.cluster.models import Maquina, MaquinaModeloOllama, MaquinaStatus
from apps.events.models import PipelineEvent
from apps.federacao import canal
from apps.federacao.canal import ParInacessivel, RespostaInvalida, chamar_par
from apps.federacao.chaves import garantir_chave_ativa
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db

ENDPOINT_LOCAL = "http://10.0.0.5:8000"
PING = "/federacao/controle/v1/ping/"
ESTADO = "/federacao/controle/v1/estado/"


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "controle-testes"}}
    settings.FEDERACAO_ENDPOINT_ANUNCIADO = ENDPOINT_LOCAL
    settings.FEDERACAO_LIMITE_POR_MINUTO = 120
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def local():
    return garantir_chave_ativa()[0]


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def _par(org, remota, *, tipo="proprio", estado="confirmado", **kw):
    return Maquina.objects.create(organizacao=org, dono=org.owner, apelido=f"par-{Maquina.objects.count()}", did=remota.did,
                                  tipo=tipo, estado=estado, endpoint_controle="http://10.0.0.9:8000", **kw)


def _chamar(remota, local, metodo="GET", caminho=PING, corpo=None, client=None, **kw):
    bruto = json.dumps(corpo).encode() if corpo is not None else b""
    headers = canal.assinar_requisicao(metodo, caminho, bruto, local.did, chave=remota, **kw)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items()}
    resp = (client or Client()).generic(metodo, caminho, data=bruto, content_type="application/json", **extra)
    return resp, headers


# ── rotas e autorização por tipo ────────────────────────────────────────────

@pytest.mark.parametrize("tipo", ["proprio", "terceiro"])
def test_ping_serve_qualquer_par_confirmado_e_responde_assinado(org, local, remota, tipo):
    _par(org, remota, tipo=tipo)
    resp, headers = _chamar(remota, local)
    assert resp.status_code == 200 and json.loads(resp.content) == {"ok": True, "v": 1}
    canal.verificar_resposta(resp.content, headers[canal.H_NONCE], local.did, resp[canal.H_ASSINATURA_RESPOSTA])


def test_estado_so_para_par_proprio(org, local, remota):
    par = _par(org, remota, tipo="terceiro")
    resp, _ = _chamar(remota, local, caminho=ESTADO)
    assert resp.status_code == 403 and json.loads(resp.content) == {"erro": "não autorizado"}
    Maquina.objects.filter(pk=par.pk).update(tipo="proprio")
    resp, _ = _chamar(remota, local, caminho=ESTADO)
    assert resp.status_code == 200


def test_estado_traz_recursos_e_modelos_sem_vazar_enderecos_internos(org, local, remota, settings, monkeypatch):
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    settings.OLLAMA_ENDPOINT_ANUNCIADO = "http://segredo-interno:11434"
    _par(org, remota)
    eu = pares.garantir_maquina_local(org)
    MaquinaStatus.objects.create(maquina=eu, cpu_percent=12.5, cpu_count=8, ram_total_mb=16000, ram_disponivel_mb=9000,
                                 disco_disponivel_gb=120.0, ollama_disponivel=True, ollama_versao="0.35.1",
                                 disco_ollama_livre_gb=80.5, ultimo_heartbeat_em=timezone.now())
    MaquinaModeloOllama.objects.create(maquina=eu, nome_modelo="qwen3.5:9b", tokens_por_segundo_medio=31.5,
                                       tamanho_bytes=5_500_000_000, digest="abc", familia="qwen", parametros="9B",
                                       quantizacao="Q4_K_M", carregado=True)
    resp, _ = _chamar(remota, local, caminho=ESTADO)
    corpo = json.loads(resp.content)
    assert corpo["nome"] == "antares" and corpo["recursos"]["cpu_count"] == 8 and corpo["recursos"]["disco_ollama_livre_gb"] == 80.5
    assert corpo["ollama"] == {"configurado": True, "disponivel": True, "versao": "0.35.1"}
    assert corpo["modelos"] == [{"nome": "qwen3.5:9b", "tamanho_bytes": 5_500_000_000, "digest": "abc", "familia": "qwen",
                                 "parametros": "9B", "quantizacao": "Q4_K_M", "carregado": True, "tokens_por_segundo": 31.5}]
    bruto = resp.content.decode()
    assert "segredo-interno" not in bruto and "11434" not in bruto and "ollama_endpoint" not in bruto
    assert not re.search(r'"(token|api_key|secret|senha)"', bruto.lower())


def test_estado_so_anuncia_o_gateway_quando_ele_esta_ligado(org, local, remota, settings):
    _par(org, remota)
    settings.LLM_GATEWAY_ENDPOINT_ANUNCIADO = "http://10.0.0.5:8000"
    settings.LLM_GATEWAY_TOKEN = ""
    assert "gateway_endpoint" not in json.loads(_chamar(remota, local, caminho=ESTADO)[0].content)
    settings.LLM_GATEWAY_TOKEN = "segredo"
    assert json.loads(_chamar(remota, local, caminho=ESTADO)[0].content)["gateway_endpoint"] == "http://10.0.0.5:8000"


def test_estado_de_instancia_sem_maquina_local_ainda_responde(org, local, remota):
    _par(org, remota)
    corpo = json.loads(_chamar(remota, local, caminho=ESTADO)[0].content)
    assert corpo["modelos"] == [] and corpo["recursos"] == {}


# ── recusas uniformes ───────────────────────────────────────────────────────

def test_todas_as_recusas_de_quem_nao_e_par_confirmado_sao_identicas(org, local, remota):
    outro = ChaveOutraInstancia()
    pendente, revogado, inativo = ChaveOutraInstancia(), ChaveOutraInstancia(), ChaveOutraInstancia()
    _par(org, pendente, estado="pendente")
    _par(org, revogado, estado="revogado")
    _par(org, inativo, ativa=False)
    eu = pares.garantir_maquina_local(org)                                    # a própria instância não é par de ninguém

    respostas = [_chamar(chave, local)[0] for chave in (outro, pendente, revogado, inativo)]
    # assinatura inválida: DID de um, assinatura de outro
    h = canal.assinar_requisicao("GET", PING, b"", local.did, chave=outro)
    h[canal.H_DID] = pendente.did
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
    respostas.append(Client().get(PING, **extra))
    respostas.append(Client().get(PING))                                      # sem cabeçalho nenhum
    assert eu.did == local.did
    assert {r.status_code for r in respostas} == {404}
    assert len({r.content for r in respostas}) == 1 and json.loads(respostas[0].content) == {"erro": "não encontrado"}


def test_a_assinatura_cobre_o_metodo_a_rota_e_o_corpo(org, local, remota):
    _par(org, remota)
    corpo = b'{"a":1}'
    h = canal.assinar_requisicao("POST", PING, corpo, local.did, chave=remota)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
    assert Client().generic("GET", PING, data=corpo, content_type="application/json", **extra).status_code == 404   # método
    assert Client().generic("POST", ESTADO, data=corpo, content_type="application/json", **extra).status_code == 404  # rota
    assert Client().generic("POST", PING, data=b'{"a":2}', content_type="application/json", **extra).status_code == 404  # corpo
    assert Client().generic("POST", PING + "?x=1", data=corpo, content_type="application/json", **extra).status_code == 404  # query


def test_replay_e_rejeitado(org, local, remota):
    _par(org, remota)
    assert _chamar(remota, local, nonce="n-unico")[0].status_code == 200
    assert _chamar(remota, local, nonce="n-unico")[0].status_code == 404


def test_requisicao_para_outro_destino_nao_serve_aqui(org, local, remota):
    _par(org, remota)
    outra = ChaveOutraInstancia()
    h = canal.assinar_requisicao("GET", PING, b"", outra.did, chave=remota)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
    assert Client().get(PING, **extra).status_code == 404


def test_cada_recusa_deixa_evento_local_sem_corpo(org, local, remota):
    _par(org, remota, tipo="terceiro")
    _chamar(ChaveOutraInstancia(), local)
    _chamar(remota, local, caminho=ESTADO)
    eventos = list(PipelineEvent.objects.filter(stage="federacao.canal").order_by("sequence"))
    assert [e.payload["codigo"] for e in eventos] == ["par_desconhecido_ou_nao_confirmado", "tipo_sem_permissao"]
    assert all(e.status == "ignorado" and set(e.payload) <= {"rota", "codigo", "par", "tipo"} for e in eventos)


# ── limite de taxa, corpo ───────────────────────────────────────────────────

def test_limite_por_minuto_de_cada_par(org, local, remota, settings):
    settings.FEDERACAO_LIMITE_POR_MINUTO = 3
    _par(org, remota)
    outro = ChaveOutraInstancia()
    _par(org, outro)
    assert [_chamar(remota, local)[0].status_code for _ in range(4)] == [200, 200, 200, 429]
    assert _chamar(outro, local)[0].status_code == 200            # o limite é por par


OPERACOES = "/federacao/controle/v1/ollama/operacoes/"


def test_corpo_invalido_ou_gigante(org, local, remota):
    _par(org, remota)

    def enviar(bruto):
        h = canal.assinar_requisicao("POST", OPERACOES, bruto, local.did, chave=remota)
        extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
        return Client().generic("POST", OPERACOES, data=bruto, content_type="application/json", **extra)

    resp = enviar(b"nao-e-json")
    assert resp.status_code == 400 and json.loads(resp.content) == {"erro": "requisição inválida"}
    assert enviar(b"[1,2]").status_code == 400
    assert enviar(b"{" + b" " * 20000 + b"}").status_code == 404


def test_o_canal_nao_exige_sessao_nem_csrf(org, local, remota):
    _par(org, remota)
    resp = _chamar(remota, local, client=Client(enforce_csrf_checks=True), metodo="POST", caminho=OPERACOES, corpo={})[0]
    # passou do CSRF e da assinatura; quem recusa é a regra de negócio (sem ator administrador), em JSON
    assert resp.status_code == 403 and json.loads(resp.content) == {"erro": "não autorizado"}


def test_cada_rota_so_aceita_o_seu_metodo(org, local, remota):
    _par(org, remota)
    resp, _ = _chamar(remota, local, metodo="POST", caminho=PING, corpo={})
    assert resp.status_code == 405 and json.loads(resp.content) == {"erro": "método não permitido"}
    resp, _ = _chamar(remota, local, metodo="GET", caminho=OPERACOES)
    assert resp.status_code == 405


# ── cliente: chamar_par ─────────────────────────────────────────────────────

def _ponte(client=None):
    """Transporte de teste: leva a chamada do cliente direto ao Django (sem rede)."""
    cliente = client or Client()

    def transporte(metodo, url, corpo, headers, timeout):
        caminho = url[len(ENDPOINT_LOCAL):]
        extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items() if k != "Content-Type"}
        resp = cliente.generic(metodo, caminho, data=corpo, content_type="application/json", **extra)
        return resp.status_code, resp.content, resp.headers
    return transporte


@pytest.fixture
def servidor(local):
    """O par, visto de quem chama: aponta para esta instância."""
    return SimpleNamespace(did=local.did, endpoint_controle=ENDPOINT_LOCAL)


def test_chamar_par_ida_e_volta_com_resposta_verificada(org, local, remota, servidor):
    _par(org, remota)
    resposta = chamar_par(servidor, "GET", PING, chave=remota, transporte=_ponte())
    assert resposta.status == 200 and resposta.assinada and resposta.json == {"ok": True, "v": 1}


def test_chamar_par_leva_o_ator_dentro_do_corpo_assinado(org, local, remota, servidor):
    _par(org, remota)
    visto = {}

    def espiao(metodo, url, corpo, headers, timeout):
        visto.update(corpo=json.loads(corpo))
        return _ponte()(metodo, url, corpo, headers, timeout)

    chamar_par(servidor, "POST", PING, {"x": 1}, ator={"user": "u1", "papel": "admin"}, chave=remota, transporte=espiao)
    assert visto["corpo"] == {"x": 1, "ator": {"user": "u1", "papel": "admin"}}


def test_chamar_par_devolve_a_recusa_sem_fingir_que_e_assinada(org, local, remota, servidor):
    _par(org, remota, tipo="terceiro")
    resposta = chamar_par(servidor, "GET", ESTADO, chave=remota, transporte=_ponte())
    assert resposta.status == 403 and not resposta.assinada and resposta.json == {"erro": "não autorizado"}


def test_resposta_2xx_sem_assinatura_do_par_e_rejeitada(local, remota, servidor):
    nao_assinada = lambda *a: (200, b'{"ok":true}', {})  # noqa: E731
    with pytest.raises(RespostaInvalida):
        chamar_par(servidor, "GET", PING, chave=remota, transporte=nao_assinada)


def test_resposta_assinada_por_outro_e_rejeitada(local, remota, servidor):
    impostor = ChaveOutraInstancia()

    def intermediario(metodo, url, corpo, headers, timeout):
        conteudo = b'{"ok":true}'
        return 200, conteudo, {canal.H_ASSINATURA_RESPOSTA: canal.assinar_resposta(conteudo, headers[canal.H_NONCE], chave=impostor)}

    with pytest.raises(RespostaInvalida):
        chamar_par(servidor, "GET", PING, chave=remota, transporte=intermediario)


def test_resposta_assinada_que_nao_e_json_e_rejeitada(local, remota, servidor):
    def lixo(metodo, url, corpo, headers, timeout):
        return 200, b"<html>", {canal.H_ASSINATURA_RESPOSTA: canal.assinar_resposta(b"<html>", headers[canal.H_NONCE], chave=SimpleNamespace(did=local.did, assinar=local.assinar))}

    with pytest.raises(RespostaInvalida):
        chamar_par(servidor, "GET", PING, chave=remota, transporte=lixo)


@pytest.mark.parametrize("par", [
    SimpleNamespace(did=None, endpoint_controle="http://10.0.0.5:8000"),
    SimpleNamespace(did="did:key:z6Mkx", endpoint_controle=""),
    SimpleNamespace(did="did:key:z6Mkx", endpoint_controle="http://169.254.169.254"),
    SimpleNamespace(did="did:key:z6Mkx", endpoint_controle="http://exemplo.org"),
])
def test_endereco_ruim_nao_chega_a_sair_da_instancia(local, remota, par):
    with pytest.raises(ParInacessivel):
        chamar_par(par, "GET", PING, chave=remota, transporte=lambda *a: pytest.fail("não deveria chamar"))


def test_transporte_real_nao_segue_redirect_e_mapeia_falhas(local, remota, servidor):
    with mock.patch("apps.federacao.canal._requests.request", side_effect=requests.ConnectionError("x")):
        with pytest.raises(ParInacessivel):
            chamar_par(servidor, "GET", PING, chave=remota)
    with mock.patch("apps.federacao.canal._requests.request", side_effect=requests.Timeout("x")):
        with pytest.raises(ParInacessivel):
            chamar_par(servidor, "GET", PING, chave=remota)
    redirecionamento = mock.MagicMock(status_code=302, headers={})
    redirecionamento.raw.read.return_value = b""
    with mock.patch("apps.federacao.canal._requests.request", return_value=redirecionamento) as req:
        resposta = chamar_par(servidor, "GET", PING, chave=remota, timeout=3)
    assert resposta.status == 302 and not resposta.assinada
    assert req.call_args.kwargs["allow_redirects"] is False and req.call_args.kwargs["timeout"] == 3


def test_resposta_gigante_e_rejeitada(local, remota, servidor):
    gigante = mock.MagicMock(status_code=200, headers={})
    gigante.raw.read.return_value = b"x" * (canal.LIMITE_RESPOSTA + 1)
    with mock.patch("apps.federacao.canal._requests.request", return_value=gigante):
        with pytest.raises(RespostaInvalida):
            chamar_par(servidor, "GET", PING, chave=remota)

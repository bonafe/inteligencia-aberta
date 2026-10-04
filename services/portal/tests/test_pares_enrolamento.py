"""Pares e enrolamento por convite (ADR 011, Marco A): serviço, endpoint público, telas e validação de endereço."""
import json
import re
from datetime import timedelta
from unittest import mock

import pytest
import requests
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import AuditLog
from apps.cluster import pares
from apps.cluster.models import ConviteEnrolamento, Maquina
from apps.cluster.pares import ErroEnrolamento
from apps.events.models import PipelineEvent
from apps.federacao import canal
from apps.federacao.chaves import garantir_chave_ativa
from apps.federacao.did import impressao_digital
from apps.federacao.endpoints import validar_endpoint
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db

ENDPOINT_LOCAL = "http://10.0.0.5:8000"
ENDPOINT_REMOTO = "http://10.0.0.9:8000"


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pares-testes"}}
    settings.FEDERACAO_ENDPOINT_ANUNCIADO = ENDPOINT_LOCAL
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def admin(org):
    return org.owner


@pytest.fixture
def membro(org):
    u = User.objects.create_user(username="membro", password="x")
    Membership.objects.create(user=u, organization=org, role="member")
    return u


@pytest.fixture
def local():
    return garantir_chave_ativa()[0]


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def _codigo(remota, token="t" * 24, endpoint=ENDPOINT_REMOTO, nome="Notebook B"):
    return pares._codificar({"v": 1, "token": token, "did": remota.did, "endpoint": endpoint, "nome": nome})


def _enviar_de(remota, *, status=200, corpo=None, assinante=None, capturar=None, assinar=True):
    """Simula a outra instância respondendo ao `POST` de aceitar convite."""
    def enviar(url, corpo_req, headers):
        if capturar is not None:
            capturar.update(url=url, corpo=corpo_req, headers=headers)
        resposta = json.dumps(corpo if corpo is not None else {"did": remota.did, "nome": "Notebook B"}).encode()
        cab = {}
        if assinar:
            cab[canal.H_ASSINATURA_RESPOSTA] = canal.assinar_resposta(resposta, headers[canal.H_NONCE], chave=assinante or remota)
        return status, resposta, cab
    return enviar


# ═══ lado B: aceitar um convite ═════════════════════════════════════════════

def test_aceitar_convite_cria_o_par_pendente_e_assina_a_chamada(org, admin, local, remota):
    visto = {}
    par = pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=_enviar_de(remota, capturar=visto))
    assert par.estado == "pendente" and par.tipo == "terceiro" and par.did == remota.did and not par.eh_local
    assert par.endpoint_controle == ENDPOINT_REMOTO and par.apelido == "Notebook B" and par.dono_id == admin.id
    assert visto["url"] == ENDPOINT_REMOTO + "/federacao/convite/aceitar/"
    # a chamada saiu assinada pela NOSSA chave, destinada ao DID do convite
    assert canal.verificar_requisicao("POST", pares.CAMINHO_ACEITAR, visto["headers"], visto["corpo"], did_local=remota.did) == local.did
    corpo = json.loads(visto["corpo"])
    assert corpo["did"] == local.did and corpo["endpoint"] == ENDPOINT_LOCAL and corpo["token"] == "t" * 24
    log = AuditLog.objects.get(operation="par.convite_aceito")
    assert log.organization_id == org.id and log.user_id == admin.id and "t" * 24 not in json.dumps(log.metadata)


def test_aceitar_como_proprio_exige_confirmacao_explicita(org, admin, local, remota):
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, _codigo(remota), "proprio", enviar=_enviar_de(remota))
    assert not Maquina.objects.filter(did=remota.did).exists()
    par = pares.aceitar_convite(org, admin, _codigo(remota), "proprio", confirmou_proprio=True, enviar=_enviar_de(remota))
    assert par.tipo == "proprio" and par.estado == "pendente"     # próprio, mas ainda não confirmado


def test_so_dono_e_administrador_aceitam(org, membro, local, remota):
    with pytest.raises(PermissionDenied):
        pares.aceitar_convite(org, membro, _codigo(remota), "terceiro", enviar=_enviar_de(remota))
    assert not Maquina.objects.filter(did=remota.did).exists()


@pytest.mark.parametrize("codigo", [
    "", "lixo", "ia1.", "ia1.!!!", "ia1." + "A" * 5000,
    "ia2.eyJ2IjoxfQ", "ia1.e30",                                  # versão errada / vazio
])
def test_codigo_invalido(org, admin, local, codigo):
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, codigo, "terceiro", enviar=lambda *a: pytest.fail("não deveria chamar"))


@pytest.mark.parametrize("mudanca", [
    dict(endpoint="http://169.254.169.254"), dict(endpoint="ftp://10.0.0.9"), dict(endpoint="http://u:p@10.0.0.9"),
    dict(endpoint="http://exemplo.org"),                          # HTTP em endereço público
    dict(token="curto"), dict(nome="   "),
])
def test_codigo_com_campo_perigoso_e_recusado_antes_de_chamar(org, admin, local, remota, mudanca):
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, _codigo(remota, **mudanca), "terceiro", enviar=lambda *a: pytest.fail("não deveria chamar"))


def test_did_invalido_no_codigo(org, admin, local, remota):
    codigo = pares._codificar({"v": 1, "token": "t" * 24, "did": "did:web:exemplo.org", "endpoint": ENDPOINT_REMOTO, "nome": "x"})
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, codigo, "terceiro", enviar=lambda *a: pytest.fail("não deveria chamar"))


def test_convite_da_propria_instancia_e_recusado(org, admin, local):
    codigo = pares._codificar({"v": 1, "token": "t" * 24, "did": local.did, "endpoint": ENDPOINT_REMOTO, "nome": "eu"})
    with pytest.raises(ErroEnrolamento, match="própria instância"):
        pares.aceitar_convite(org, admin, codigo, "terceiro", enviar=lambda *a: pytest.fail("não deveria chamar"))


def test_par_ja_cadastrado_nao_se_cadastra_de_novo(org, admin, local, remota):
    pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=_enviar_de(remota))
    with pytest.raises(ErroEnrolamento, match="já está cadastrada"):
        pares.aceitar_convite(org, admin, _codigo(remota, token="u" * 24), "terceiro", enviar=_enviar_de(remota))


def test_sem_endpoint_anunciado_nao_ha_enrolamento(org, admin, local, remota, settings):
    settings.FEDERACAO_ENDPOINT_ANUNCIADO = ""
    with pytest.raises(ErroEnrolamento, match="FEDERACAO_ENDPOINT_ANUNCIADO"):
        pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=_enviar_de(remota))
    with pytest.raises(ErroEnrolamento, match="FEDERACAO_ENDPOINT_ANUNCIADO"):
        pares.criar_convite(org, admin)


def test_recusa_da_outra_instancia_nao_cria_par(org, admin, local, remota):
    with pytest.raises(ErroEnrolamento, match="HTTP 400"):
        pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=_enviar_de(remota, status=400))
    assert not Maquina.objects.filter(did=remota.did).exists()


@pytest.mark.parametrize("variacao", ["sem_assinatura", "outro_assinante", "did_diferente", "nao_json", "nao_objeto"])
def test_resposta_que_nao_confere_nao_cria_par(org, admin, local, remota, variacao):
    impostor = ChaveOutraInstancia()
    kw = {
        "sem_assinatura": dict(assinar=False),
        "outro_assinante": dict(assinante=impostor),
        "did_diferente": dict(corpo={"did": impostor.did}),
        "nao_json": dict(corpo=None),
        "nao_objeto": dict(corpo=[1, 2]),
    }[variacao]
    if variacao == "nao_json":
        enviar = lambda url, c, h: (200, b"<html>", {canal.H_ASSINATURA_RESPOSTA: canal.assinar_resposta(b"<html>", h[canal.H_NONCE], chave=remota)})  # noqa: E731
    else:
        enviar = _enviar_de(remota, **kw)
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=enviar)
    assert not Maquina.objects.filter(did=remota.did).exists()


def test_resposta_assinada_para_outro_nonce_e_rejeitada(org, admin, local, remota):
    """Uma resposta antiga, assinada para outro pedido, não vale para este."""
    antiga = canal.assinar_resposta(json.dumps({"did": remota.did}).encode(), "nonce-de-outro-pedido", chave=remota)
    enviar = lambda url, c, h: (200, json.dumps({"did": remota.did}).encode(), {canal.H_ASSINATURA_RESPOSTA: antiga})  # noqa: E731
    with pytest.raises(ErroEnrolamento):
        pares.aceitar_convite(org, admin, _codigo(remota), "terceiro", enviar=enviar)


def test_o_transporte_nao_segue_redirect_e_trata_falha_de_rede(org, admin, local, remota):
    with mock.patch("apps.cluster.pares.requests.post", side_effect=requests.ConnectionError("x")):
        with pytest.raises(ErroEnrolamento, match="não consegui falar"):
            pares.aceitar_convite(org, admin, _codigo(remota), "terceiro")
    resposta = mock.MagicMock(status_code=302)
    resposta.raw.read.return_value = b""
    resposta.headers = {}
    with mock.patch("apps.cluster.pares.requests.post", return_value=resposta) as post:
        with pytest.raises(ErroEnrolamento, match="HTTP 302"):
            pares.aceitar_convite(org, admin, _codigo(remota), "terceiro")
    assert post.call_args.kwargs["allow_redirects"] is False and post.call_args.kwargs["timeout"] == 10


def test_resposta_gigante_e_recusada(org, admin, local, remota):
    resposta = mock.MagicMock(status_code=200)
    resposta.raw.read.return_value = b"x" * (pares.LIMITE_RESPOSTA + 1)
    with mock.patch("apps.cluster.pares.requests.post", return_value=resposta):
        with pytest.raises(ErroEnrolamento, match="grande demais"):
            pares.aceitar_convite(org, admin, _codigo(remota), "terceiro")


def test_apelido_unico_e_nome_remoto_sanitizado(org, admin, local, remota):
    Maquina.objects.create(organizacao=org, dono=admin, apelido="Notebook B")
    par = pares.aceitar_convite(org, admin, _codigo(remota, nome="Note\x00book\x07 B"), "terceiro", enviar=_enviar_de(remota))
    assert par.apelido == "Notebook B (2)"


# ═══ lado A: criar convite e receber o aceite ═══════════════════════════════

def test_criar_convite_guarda_so_o_hash_e_o_codigo_traz_o_necessario(org, admin, local):
    convite, codigo = pares.criar_convite(org, admin)
    dados = pares.decodificar_codigo(codigo)
    assert dados["did"] == local.did and dados["endpoint"] == ENDPOINT_LOCAL and dados["nome"]
    assert convite.token_hash == pares._hash(dados["token"]) and dados["token"] not in convite.token_hash
    assert convite.tipo == "terceiro" and convite.usado_em is None
    assert timedelta(hours=23) < convite.expira_em - timezone.now() <= timedelta(hours=24)
    assert pares.criar_convite(org, admin)[0].token_hash != convite.token_hash
    log = AuditLog.objects.filter(operation="par.convite_criado").first()
    assert log and dados["token"] not in json.dumps(log.metadata)


def test_criar_convite_exige_papel_e_confirmacao_de_proprio(org, admin, membro, local):
    with pytest.raises(PermissionDenied):
        pares.criar_convite(org, membro)
    with pytest.raises(ErroEnrolamento):
        pares.criar_convite(org, admin, tipo="proprio")
    with pytest.raises(ErroEnrolamento):
        pares.criar_convite(org, admin, tipo="inventado")
    assert pares.criar_convite(org, admin, tipo="proprio", confirmou_proprio=True)[0].tipo == "proprio"


def _post_aceite(remota, local, token, *, client=None, did_corpo=None, endpoint=ENDPOINT_REMOTO, caminho=pares.CAMINHO_ACEITAR,
                 caminho_assinado=None, **kw):
    corpo = json.dumps({"token": token, "did": did_corpo or remota.did, "endpoint": endpoint, "nome": "Notebook B"}).encode()
    headers = canal.assinar_requisicao("POST", caminho_assinado or caminho, corpo, local.did, chave=remota, **kw)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items()}
    return (client or Client()).post(caminho, data=corpo, content_type="application/json", **extra), headers


def test_aceite_valido_cria_o_par_pendente_com_o_tipo_do_convite_e_responde_assinado(org, admin, local, remota):
    convite, codigo = pares.criar_convite(org, admin, tipo="proprio", confirmou_proprio=True)
    token = pares.decodificar_codigo(codigo)["token"]
    resp, headers = _post_aceite(remota, local, token)       # sem sessão: o canal é público (assinatura + token)
    assert resp.status_code == 200
    corpo = json.loads(resp.content)
    assert corpo["did"] == local.did
    canal.verificar_resposta(resp.content, headers[canal.H_NONCE], local.did, resp["X-IA-Assinatura-Resposta"])
    par = Maquina.objects.get(did=remota.did)
    assert par.estado == "pendente" and par.tipo == "proprio" and par.organizacao_id == org.id
    assert par.endpoint_controle == ENDPOINT_REMOTO and par.dono_id == admin.id
    convite.refresh_from_db()
    assert convite.usado_em and convite.usado_por_did == remota.did
    assert AuditLog.objects.filter(operation="par.convite_recebido", organization=org).exists()


def test_o_token_so_serve_uma_vez(org, admin, local, remota):
    _, codigo = pares.criar_convite(org, admin)
    token = pares.decodificar_codigo(codigo)["token"]
    assert _post_aceite(remota, local, token)[0].status_code == 200
    outra = ChaveOutraInstancia()
    resp, _ = _post_aceite(outra, local, token)
    assert resp.status_code == 400 and not Maquina.objects.filter(did=outra.did).exists()


def test_replay_da_mesma_requisicao_e_recusado(org, admin, local, remota):
    _, codigo = pares.criar_convite(org, admin)
    token = pares.decodificar_codigo(codigo)["token"]
    resp, headers = _post_aceite(remota, local, token, nonce="fixo-1")
    assert resp.status_code == 200
    resp2, _ = _post_aceite(remota, local, token, nonce="fixo-1")
    assert resp2.status_code == 400


def test_todas_as_recusas_dizem_exatamente_a_mesma_coisa(org, admin, local, remota):
    """A resposta não revela se o token existe, expirou, foi usado ou a assinatura falhou."""
    _, codigo = pares.criar_convite(org, admin)
    token = pares.decodificar_codigo(codigo)["token"]
    expirado, cod2 = pares.criar_convite(org, admin)
    ConviteEnrolamento.objects.filter(pk=expirado.pk).update(expira_em=timezone.now() - timedelta(seconds=1))
    token_exp = pares.decodificar_codigo(cod2)["token"]
    outra = ChaveOutraInstancia()

    respostas = [
        _post_aceite(remota, local, "token-inexistente-xxxxxxxxxxxx")[0],                       # token desconhecido
        _post_aceite(remota, local, token_exp)[0],                                              # expirado
        _post_aceite(remota, local, token, did_corpo=outra.did)[0],                             # did do corpo ≠ assinante
        _post_aceite(remota, local, token, endpoint="http://169.254.169.254")[0],               # endereço proibido
        _post_aceite(remota, local, token, caminho_assinado="/federacao/outra/")[0],            # rota assinada diferente
        _post_aceite(remota, local, token, agora=0)[0],                                         # fora da janela de tempo
    ]
    assert {r.status_code for r in respostas} == {400}
    assert len({r.content for r in respostas}) == 1
    assert not Maquina.objects.filter(did__in=[remota.did, outra.did]).exists()


def test_assinatura_de_outra_chave_ou_destino_errado(org, admin, local, remota):
    _, codigo = pares.criar_convite(org, admin)
    token = pares.decodificar_codigo(codigo)["token"]
    corpo = json.dumps({"token": token, "did": remota.did, "endpoint": ENDPOINT_REMOTO, "nome": "x"}).encode()
    impostor = ChaveOutraInstancia()
    # assinada pelo impostor, mas declarando o DID da remota
    h = canal.assinar_requisicao("POST", pares.CAMINHO_ACEITAR, corpo, local.did, chave=impostor)
    h[canal.H_DID] = remota.did
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
    assert Client().post(pares.CAMINHO_ACEITAR, data=corpo, content_type="application/json", **extra).status_code == 400
    # assinada para outro destino
    h2 = canal.assinar_requisicao("POST", pares.CAMINHO_ACEITAR, corpo, impostor.did, chave=remota)
    extra2 = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h2.items()}
    assert Client().post(pares.CAMINHO_ACEITAR, data=corpo, content_type="application/json", **extra2).status_code == 400
    assert not Maquina.objects.filter(did=remota.did).exists()


def test_par_duplicado_recusa_sem_gastar_o_token(org, admin, local, remota):
    convite, codigo = pares.criar_convite(org, admin)
    token = pares.decodificar_codigo(codigo)["token"]
    Maquina.objects.create(organizacao=org, dono=admin, apelido="ja-existe", did=remota.did)
    assert _post_aceite(remota, local, token)[0].status_code == 400
    convite.refresh_from_db()
    assert convite.usado_em is None


def test_corpo_gigante_e_metodo_errado(org, admin, local, remota):
    resp = Client().post(pares.CAMINHO_ACEITAR, data=b"x" * 9000, content_type="application/json")
    assert resp.status_code == 400
    assert Client().get(pares.CAMINHO_ACEITAR).status_code == 405


def test_a_recusa_deixa_evento_local_com_o_codigo_e_sem_conteudo(org, admin, local, remota):
    _post_aceite(remota, local, "token-inexistente-xxxxxxxxxxxx")
    ev = PipelineEvent.objects.get(stage="federacao.canal")
    assert ev.status == "ignorado" and ev.payload == {"rota": "convite/aceitar", "codigo": "convite"}


# ═══ ciclo de vida do par ═══════════════════════════════════════════════════

@pytest.fixture
def par(org, admin, remota):
    return Maquina.objects.create(organizacao=org, dono=admin, apelido="notebook-b-unico", did=remota.did, endpoint_controle=ENDPOINT_REMOTO)


def test_confirmar_leva_a_confirmado_e_registra_quem_conferiu(par, admin):
    pares.confirmar(par, admin)
    par.refresh_from_db()
    assert par.estado == "confirmado" and par.conferida_por_id == admin.id and par.impressao_digital_conferida_em
    quando = par.impressao_digital_conferida_em
    pares.confirmar(par, admin)                                  # idempotente
    par.refresh_from_db()
    assert par.impressao_digital_conferida_em == quando
    assert AuditLog.objects.filter(operation="par.confirmado").count() == 1


def test_so_administrador_confirma_revoga_e_muda_tipo(par, membro):
    for acao in (lambda: pares.confirmar(par, membro), lambda: pares.revogar(par, membro),
                 lambda: pares.definir_tipo(par, membro, "proprio", confirmou_proprio=True)):
        with pytest.raises(PermissionDenied):
            acao()
    par.refresh_from_db()
    assert par.estado == "pendente" and par.tipo == "terceiro"


def test_revogado_nao_volta_nem_muda(par, admin):
    pares.revogar(par, admin)
    pares.revogar(par, admin)                                    # idempotente
    assert AuditLog.objects.filter(operation="par.revogado").count() == 1
    with pytest.raises(ErroEnrolamento):
        pares.confirmar(par, admin)
    with pytest.raises(ErroEnrolamento):
        pares.definir_tipo(par, admin, "proprio", confirmou_proprio=True)


def test_promover_a_proprio_exige_confirmacao_e_audita(par, admin):
    with pytest.raises(ErroEnrolamento):
        pares.definir_tipo(par, admin, "proprio")
    pares.definir_tipo(par, admin, "proprio", confirmou_proprio=True)
    par.refresh_from_db()
    assert par.tipo == "proprio"
    log = AuditLog.objects.get(operation="par.tipo_alterado")
    assert log.metadata["de"] == "terceiro" and log.metadata["para"] == "proprio"
    pares.definir_tipo(par, admin, "terceiro")                   # rebaixar não exige confirmação
    pares.definir_tipo(par, admin, "terceiro")                   # no-op
    assert AuditLog.objects.filter(operation="par.tipo_alterado").count() == 2
    with pytest.raises(ErroEnrolamento):
        pares.definir_tipo(par, admin, "inventado")


def test_a_propria_instancia_nao_e_par(org, admin, local):
    eu = pares.garantir_maquina_local(org)
    for acao in (lambda: pares.confirmar(eu, admin), lambda: pares.revogar(eu, admin),
                 lambda: pares.definir_tipo(eu, admin, "terceiro")):
        with pytest.raises(ErroEnrolamento):
            acao()


# ═══ máquina local ══════════════════════════════════════════════════════════

def test_garantir_maquina_local_e_idempotente_e_usa_o_did_da_chave(org, local, settings, monkeypatch):
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    settings.LLM_GATEWAY_ENDPOINT_ANUNCIADO = "http://10.0.0.5:8000"
    a = pares.garantir_maquina_local(org)
    b = pares.garantir_maquina_local(org)
    assert a.pk == b.pk and Maquina.objects.filter(organizacao=org, eh_local=True).count() == 1
    assert (a.eh_local, a.did, a.tipo, a.estado, a.apelido) == (True, local.did, "proprio", "confirmado", "antares")
    assert a.endpoint_controle == ENDPOINT_LOCAL and a.gateway_endpoint == "http://10.0.0.5:8000"


def test_garantir_maquina_local_adota_a_linha_antiga_pelo_apelido(org, admin, local, monkeypatch):
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    antiga = Maquina.objects.create(organizacao=org, dono=admin, apelido="antares")   # anterior ao Par: pendente, sem did
    adotada = pares.garantir_maquina_local(org)
    assert adotada.pk == antiga.pk and adotada.eh_local and adotada.estado == "confirmado" and adotada.did == local.did


def test_so_pode_haver_uma_maquina_local_por_organizacao(org, admin, local):
    from django.db import IntegrityError, transaction

    pares.garantir_maquina_local(org)
    with pytest.raises(IntegrityError), transaction.atomic():
        Maquina.objects.create(organizacao=org, dono=admin, apelido="outra", eh_local=True)


# ═══ endereço do par (SSRF) ═════════════════════════════════════════════════

@pytest.mark.parametrize("endpoint,esperado", [
    ("http://10.0.0.9:8000", "http://10.0.0.9:8000"), ("http://192.168.1.5/", "http://192.168.1.5"),
    ("http://172.16.0.1:8000", "http://172.16.0.1:8000"), ("http://100.101.102.103:8000", "http://100.101.102.103:8000"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"), ("http://localhost:8000", "http://localhost:8000"),
    ("http://antares:8000", "http://antares:8000"), ("http://mac.ts.net:8000", "http://mac.ts.net:8000"),
    ("http://nas.local", "http://nas.local"), ("https://ia.exemplo.org", "https://ia.exemplo.org"),
    ("https://8.8.8.8:8443", "https://8.8.8.8:8443"), ("http://[fd00::1]:8000", "http://[fd00::1]:8000"),
])
def test_endpoint_valido(endpoint, esperado):
    assert validar_endpoint(endpoint) == esperado


@pytest.mark.parametrize("endpoint", [
    "", None, "10.0.0.9", "ftp://10.0.0.9", "file:///etc/passwd", "gopher://x",
    "http://169.254.169.254", "https://169.254.169.254/latest", "http://[fe80::1]", "http://0.0.0.0",
    "http://224.0.0.1", "http://240.0.0.1", "http://u:p@10.0.0.9", "http://10.0.0.9/caminho",
    "http://10.0.0.9?x=1", "http://10.0.0.9#f", "http://10.0.0.9:99999", "http://8.8.8.8", "http://exemplo.org",
])
def test_endpoint_recusado(endpoint):
    with pytest.raises(ValueError):
        validar_endpoint(endpoint)


def test_http_publico_so_com_a_chave_explicita(settings):
    settings.FEDERACAO_PERMITE_HTTP_PUBLICO = True
    assert validar_endpoint("http://exemplo.org") == "http://exemplo.org"
    with pytest.raises(ValueError):                              # link-local continua proibido sempre
        validar_endpoint("http://169.254.169.254")


# ═══ telas ═════════════════════════════════════════════════════════════════

@pytest.fixture
def logado(client, admin):
    client.force_login(admin)
    return client


def test_lista_mostra_a_impressao_digital_do_par(logado, par, local):
    html = logado.get("/cluster/pares/").content.decode()
    assert impressao_digital(par.did) in html and impressao_digital(local.did) in html
    assert "Convidar outra instância" in html and "Confirmar" in html


def test_membro_ve_a_lista_mas_nao_as_acoes(client, membro, par):
    client.force_login(membro)
    html = client.get("/cluster/pares/").content.decode()
    assert par.apelido in html and "Convidar outra instância" not in html and "Revogar" not in html
    assert "Só dono e administrador" in html


def test_criar_convite_mostra_o_codigo_uma_unica_vez(logado, org, local):
    resp = logado.post("/cluster/pares/convite/", {"organizacao": str(org.id), "tipo": "terceiro"})
    html = resp.content.decode()
    assert resp.status_code == 200 and "Convite criado" in html
    codigo = re.search(r"ia1\.[A-Za-z0-9_-]{40,}", html).group(0)
    assert pares.decodificar_codigo(codigo)["did"]
    assert codigo not in logado.get("/cluster/pares/").content.decode()       # não é guardado nem reexibido


def test_proprio_sem_confirmar_volta_com_erro(logado, org, local):
    resp = logado.post("/cluster/pares/convite/", {"organizacao": str(org.id), "tipo": "proprio"})
    assert resp.status_code == 400 and "confirmação explícita" in resp.content.decode()
    assert not ConviteEnrolamento.objects.exists()


def test_previsualizar_mostra_a_impressao_digital_do_convite(logado, org, local, remota):
    resp = logado.post("/cluster/pares/convite/previsualizar/", {"organizacao": str(org.id), "codigo": _codigo(remota)})
    html = resp.content.decode()
    assert resp.status_code == 200 and impressao_digital(remota.did) in html and "Notebook B" in html
    assert "“Próprio” dá poder" in html


def test_previsualizar_codigo_ruim(logado, org, local):
    resp = logado.post("/cluster/pares/convite/previsualizar/", {"organizacao": str(org.id), "codigo": "lixo"})
    assert resp.status_code == 400 and "código de convite inválido" in resp.content.decode()


def test_aceitar_pela_tela_cadastra_o_par_e_volta_para_a_lista(logado, org, local, remota):
    with mock.patch("apps.cluster.pares._enviar", side_effect=_enviar_de(remota)):
        resp = logado.post("/cluster/pares/convite/aceitar/", {"organizacao": str(org.id), "codigo": _codigo(remota), "tipo": "terceiro"})
    assert resp.status_code == 302 and resp["Location"] == "/cluster/pares/"
    assert Maquina.objects.get(did=remota.did).estado == "pendente"


def test_confirmar_pela_tela_exige_o_aceite_da_conferencia(logado, par):
    resp = logado.post(f"/cluster/pares/{par.id}/confirmar/", {})
    assert resp.status_code == 400
    par.refresh_from_db()
    assert par.estado == "pendente"
    assert logado.post(f"/cluster/pares/{par.id}/confirmar/", {"conferi": "on"}).status_code == 302
    par.refresh_from_db()
    assert par.estado == "confirmado"


def test_revogar_e_mudar_tipo_pela_tela(logado, par):
    assert logado.post(f"/cluster/pares/{par.id}/tipo/", {"tipo": "proprio"}).status_code == 400
    assert logado.post(f"/cluster/pares/{par.id}/tipo/", {"tipo": "proprio", "confirmo_proprio": "on"}).status_code == 302
    assert logado.post(f"/cluster/pares/{par.id}/revogar/").status_code == 302
    par.refresh_from_db()
    assert (par.tipo, par.estado) == ("proprio", "revogado")


def test_membro_recebe_403_nas_acoes(client, membro, par, org):
    client.force_login(membro)
    for url, dados in ((f"/cluster/pares/{par.id}/confirmar/", {"conferi": "on"}), (f"/cluster/pares/{par.id}/revogar/", {}),
                       (f"/cluster/pares/{par.id}/tipo/", {"tipo": "proprio", "confirmo_proprio": "on"}),
                       ("/cluster/pares/convite/", {"organizacao": str(org.id)}),
                       ("/cluster/pares/convite/previsualizar/", {"organizacao": str(org.id), "codigo": "x"})):
        assert client.post(url, dados).status_code == 403
    par.refresh_from_db()
    assert (par.estado, par.tipo) == ("pendente", "terceiro")


def test_par_de_outra_organizacao_e_404(client, par):
    outro = User.objects.create_user(username="estranho", password="x")
    outra = Organization.objects.create(name="X", slug="x", org_type="individual", owner=outro)
    Membership.objects.create(user=outro, organization=outra, role="owner")
    client.force_login(outro)
    assert client.post(f"/cluster/pares/{par.id}/revogar/").status_code == 404
    assert client.post(f"/cluster/pares/{par.id}/confirmar/", {"conferi": "on"}).status_code == 404
    assert par.apelido not in client.get("/cluster/pares/").content.decode()
    assert client.post("/cluster/pares/convite/", {"organizacao": str(par.organizacao_id)}).status_code == 404


def test_as_acoes_exigem_csrf(admin, par):
    c = Client(enforce_csrf_checks=True)
    c.force_login(admin)
    assert c.post(f"/cluster/pares/{par.id}/revogar/").status_code == 403
    par.refresh_from_db()
    assert par.estado == "pendente"


def test_sem_sessao_vai_para_o_login(client, par):
    resp = client.get("/cluster/pares/")
    assert resp.status_code in (301, 302) and "/entrar" in resp["Location"]

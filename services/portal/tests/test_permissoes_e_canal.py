"""Permissões por papel, impressão digital do DID e requisições assinadas entre instâncias."""
import re
import time
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.accounts.permissoes import eh_admin, exige_admin, orgs_onde_e_admin, papel_do_usuario
from apps.federacao import canal
from apps.federacao.canal import AssinaturaInvalida, assinar_requisicao, verificar_requisicao
from apps.federacao.chaves import garantir_chave_ativa
from apps.federacao.did import did_key_de_publica, impressao_digital

pytestmark = pytest.mark.django_db


# ── permissões ──────────────────────────────────────────────────────────────

@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)


def _membro(org, papel, **kw):
    u = User.objects.create_user(username=f"u-{papel}-{User.objects.count()}", password="x")
    Membership.objects.create(user=u, organization=org, role=papel, **kw)
    return u


@pytest.mark.parametrize("papel,esperado", [("owner", True), ("admin", True), ("member", False), ("guest", False)])
def test_so_dono_e_administrador_administram(org, papel, esperado):
    u = _membro(org, papel)
    assert eh_admin(u, org) is esperado
    assert papel_do_usuario(u, org) == papel


def test_quem_nao_e_membro_nao_administra(org):
    outro = User.objects.create_user(username="fora", password="x")
    assert not eh_admin(outro, org) and papel_do_usuario(outro, org) is None


def test_papel_expirado_nao_vale(org):
    u = _membro(org, "admin", expires_at=timezone.now() - timedelta(minutes=1))
    assert not eh_admin(u, org) and papel_do_usuario(u, org) is None
    v = _membro(org, "admin", expires_at=timezone.now() + timedelta(days=1))
    assert eh_admin(v, org)


def test_superusuario_nao_administra_organizacao_alheia(org):
    su = User.objects.create_superuser(username="root", password="x", email="r@x.com")
    assert not eh_admin(su, org)


def test_anonimo_e_none_nao_administram(org):
    from django.contrib.auth.models import AnonymousUser

    assert not eh_admin(AnonymousUser(), org) and not eh_admin(None, org)
    assert list(orgs_onde_e_admin(None)) == []


def test_orgs_onde_e_admin_so_traz_as_que_administra(org):
    outra = Organization.objects.create(name="P", slug="p", org_type="team", owner=org.owner)
    u = _membro(org, "admin")
    Membership.objects.create(user=u, organization=outra, role="member")
    assert list(orgs_onde_e_admin(u)) == [org]


def test_exige_admin_levanta_403(org):
    exige_admin(_membro(org, "owner"), org)
    with pytest.raises(PermissionDenied):
        exige_admin(_membro(org, "member"), org)


# ── impressão digital ───────────────────────────────────────────────────────

def test_impressao_digital_formato_e_estabilidade():
    did = did_key_de_publica(b"\x01" * 32)
    ip = impressao_digital(did)
    assert re.fullmatch(r"[0-9a-f]{4}(-[0-9a-f]{4}){4}", ip)
    assert ip == impressao_digital(did)
    assert ip != impressao_digital(did_key_de_publica(b"\x02" * 32))


# ── requisições assinadas ───────────────────────────────────────────────────

class ChaveOutraInstancia:
    """Chave de uma 'outra instância' (a local é a `ChaveInstancia` do banco)."""

    def __init__(self):
        self._privada = Ed25519PrivateKey.generate()
        publica = self._privada.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.did = did_key_de_publica(publica)

    def assinar(self, mensagem: bytes) -> bytes:
        return self._privada.sign(mensagem)


@pytest.fixture(autouse=True)
def cache_local(settings):
    """Nonces em memória: a suíte não depende do Redis para provar o anti-replay."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "canal-testes"}}
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def local():
    return garantir_chave_ativa()[0]


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def _assinar(remota, local, metodo="POST", caminho="/federacao/controle/v1/x?a=1", corpo=b'{"k":1}', **kw):
    return assinar_requisicao(metodo, caminho, corpo, local.did, chave=remota, **kw)


def _verificar(local, headers, metodo="POST", caminho="/federacao/controle/v1/x?a=1", corpo=b'{"k":1}', **kw):
    return verificar_requisicao(metodo, caminho, headers, corpo, did_local=local.did, **kw)


def test_requisicao_assinada_passa_e_devolve_o_did_de_origem(local, remota):
    assert _verificar(local, _assinar(remota, local)) == remota.did


def test_cabecalhos_em_dict_minusculo_tambem_funcionam(local, remota):
    h = {k.lower(): v for k, v in _assinar(remota, local).items()}
    assert _verificar(local, h) == remota.did


@pytest.mark.parametrize("mudanca", [
    dict(corpo=b'{"k":2}'), dict(metodo="GET"), dict(caminho="/federacao/controle/v1/y?a=1"),
    dict(caminho="/federacao/controle/v1/x?a=2"),
])
def test_adulterar_corpo_metodo_ou_rota_invalida_a_assinatura(local, remota, mudanca):
    h = _assinar(remota, local)
    with pytest.raises(AssinaturaInvalida) as erro:
        _verificar(local, h, **mudanca)
    assert erro.value.codigo == "assinatura"


def test_janela_de_tempo(local, remota, settings):
    settings.FEDERACAO_JANELA_RELOGIO_S = 60
    agora = time.time()
    _verificar(local, _assinar(remota, local, agora=agora - 59), agora=agora)
    for desvio in (-61, 61):
        with pytest.raises(AssinaturaInvalida) as erro:
            _verificar(local, _assinar(remota, local, agora=agora + desvio), agora=agora)
        assert erro.value.codigo == "janela"


def test_destino_errado_impede_reaproveitar_em_outra_instancia(local, remota):
    outra_instancia = ChaveOutraInstancia()
    h = assinar_requisicao("POST", "/x", b"", outra_instancia.did, chave=remota)   # destinada a C, não a A
    with pytest.raises(AssinaturaInvalida) as erro:
        verificar_requisicao("POST", "/x", h, b"", did_local=local.did)
    assert erro.value.codigo == "destino"


def test_o_destino_nao_pode_ser_trocado_depois_de_assinar(local, remota):
    outra = ChaveOutraInstancia()
    h = assinar_requisicao("POST", "/x", b"", outra.did, chave=remota)
    h[canal.H_DESTINO] = local.did                       # atacante troca o cabeçalho para o destino real
    with pytest.raises(AssinaturaInvalida) as erro:
        verificar_requisicao("POST", "/x", h, b"", did_local=local.did)
    assert erro.value.codigo == "assinatura"


def test_replay_do_mesmo_nonce_e_rejeitado(local, remota):
    h = _assinar(remota, local)
    _verificar(local, h)
    with pytest.raises(AssinaturaInvalida) as erro:
        _verificar(local, h)
    assert erro.value.codigo == "replay"


def test_nonce_so_e_queimado_por_requisicao_autentica(local, remota):
    """Um atacante sem a chave não consegue 'gastar' o nonce de uma requisição legítima."""
    legitima = _assinar(remota, local, nonce="n1")
    forjada = dict(legitima, **{canal.H_ASSINATURA: "A" * 86})
    with pytest.raises(AssinaturaInvalida):
        _verificar(local, forjada)
    assert _verificar(local, legitima) == remota.did


def test_assinatura_de_outra_chave_e_rejeitada(local, remota):
    h = _assinar(remota, local)
    impostor = ChaveOutraInstancia()
    h[canal.H_DID] = impostor.did
    with pytest.raises(AssinaturaInvalida) as erro:
        _verificar(local, h)
    assert erro.value.codigo == "assinatura"


@pytest.mark.parametrize("campo", [canal.H_DID, canal.H_TIMESTAMP, canal.H_NONCE, canal.H_DESTINO, canal.H_ASSINATURA])
def test_cabecalho_ausente(local, remota, campo):
    h = _assinar(remota, local)
    h.pop(campo)
    with pytest.raises(AssinaturaInvalida) as erro:
        _verificar(local, h)
    assert erro.value.codigo == "cabecalhos"


@pytest.mark.parametrize("campo,valor,codigo", [
    (canal.H_TIMESTAMP, "agora", "timestamp"),
    (canal.H_DID, "did:web:exemplo.org", "did"),
    (canal.H_ASSINATURA, "!!!não-base64!!!", "did"),
    (canal.H_NONCE, "x" * 200, "cabecalhos"),
])
def test_valores_malformados(local, remota, campo, valor, codigo):
    h = _assinar(remota, local)
    h[campo] = valor
    with pytest.raises(AssinaturaInvalida) as erro:
        _verificar(local, h)
    assert erro.value.codigo == codigo


def test_nao_se_assina_sem_chave(db, remota):
    from apps.federacao.models import ChaveInstancia

    ChaveInstancia.objects.all().delete()
    with pytest.raises(RuntimeError):
        assinar_requisicao("GET", "/x", b"", remota.did)


# ── respostas assinadas ─────────────────────────────────────────────────────

def test_resposta_assinada_vincula_corpo_nonce_e_autor(local, remota):
    corpo = b'{"ok":true}'
    ass = canal.assinar_resposta(corpo, "nonce-1", chave=remota)
    canal.verificar_resposta(corpo, "nonce-1", remota.did, ass)
    for corpo2, nonce2, did2 in ((b'{"ok":false}', "nonce-1", remota.did),
                                  (corpo, "nonce-2", remota.did),
                                  (corpo, "nonce-1", local.did)):
        with pytest.raises(AssinaturaInvalida):
            canal.verificar_resposta(corpo2, nonce2, did2, ass)
    with pytest.raises(AssinaturaInvalida):
        canal.verificar_resposta(corpo, "nonce-1", remota.did, "")

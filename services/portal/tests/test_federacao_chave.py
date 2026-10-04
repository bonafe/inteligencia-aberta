"""Chave Ed25519 da instância e `did:key` — F0 da ADR 010."""
import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from apps.federacao import chaves
from apps.federacao.did import did_key_de_publica, publica_de_did_key
from apps.federacao.models import ChaveInstancia
from apps.infrastructure.crypto import decifrar

pytestmark = pytest.mark.django_db

# Chave pública do vetor 1 da RFC 8032 e o did:key conhecido dela.
PUBLICA_RFC8032 = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
DID_RFC8032 = "did:key:z6MktwupdmLXVVqTzCw4i46r4uGyosGXRnR3XjN4Zq7oMMsw"
# Exemplo do próprio W3C did:key.
DID_W3C = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"


# ── did:key ─────────────────────────────────────────────────────────────────

def test_did_key_bate_com_o_vetor_conhecido():
    assert did_key_de_publica(PUBLICA_RFC8032) == DID_RFC8032
    assert publica_de_did_key(DID_RFC8032) == PUBLICA_RFC8032


def test_exemplo_do_w3c_decodifica_para_32_bytes():
    assert len(publica_de_did_key(DID_W3C)) == 32


def test_ida_e_volta_e_prefixo_z6mk():
    for semente in (b"\x00" * 32, b"\xff" * 32, bytes(range(32))):
        did = did_key_de_publica(semente)
        assert did.startswith("did:key:z6Mk")
        assert publica_de_did_key(did) == semente


@pytest.mark.parametrize("did", [
    "", "did:web:exemplo.org", "did:key:f6Mk", "did:key:z0OIl",
    "did:key:z" + "1" * 10,
    # multicodec de outra curva (secp256k1 = 0xe7 0x01) com 33 bytes de chave
    "did:key:zQ3shokFTS3brHcDQrn82RUDfCZESWL1ZdCEJwekUDPQiYBme",
])
def test_did_invalido_levanta_valueerror(did):
    with pytest.raises(ValueError):
        publica_de_did_key(did)


def test_publica_com_tamanho_errado_e_recusada():
    with pytest.raises(ValueError):
        did_key_de_publica(b"curta")


# ── chave da instância ──────────────────────────────────────────────────────

def test_garantir_cria_uma_vez_e_e_idempotente():
    primeira, criada1 = chaves.garantir_chave_ativa()
    segunda, criada2 = chaves.garantir_chave_ativa()
    assert criada1 and not criada2
    assert primeira.pk == segunda.pk
    assert ChaveInstancia.objects.count() == 1
    assert primeira.did.startswith("did:key:z6Mk")


def test_assinatura_confere_so_com_o_did():
    chave, _ = chaves.garantir_chave_ativa()
    assinatura = chave.assinar(b"evento")
    assert len(assinatura) == 64
    assert chaves.verificar_assinatura(chave.did, b"evento", assinatura)


def test_assinatura_nao_confere_se_mensagem_ou_autor_mudam():
    chave, _ = chaves.garantir_chave_ativa()
    assinatura = chave.assinar(b"evento")
    assert not chaves.verificar_assinatura(chave.did, b"evento adulterado", assinatura)
    assert not chaves.verificar_assinatura(DID_RFC8032, b"evento", assinatura)


@pytest.mark.parametrize("did,assinatura", [
    ("lixo", b"x" * 64), (DID_RFC8032, b""), (DID_RFC8032, b"x" * 10), (None, b"x" * 64),
])
def test_verificar_nunca_levanta(did, assinatura):
    assert chaves.verificar_assinatura(did, b"m", assinatura) is False


def test_privada_fica_cifrada_e_nao_aparece_em_str_nem_repr():
    chave, _ = chaves.garantir_chave_ativa()
    semente_b64 = decifrar(chave.privada_cifrada)
    assert semente_b64 and semente_b64 not in chave.privada_cifrada
    assert semente_b64 not in str(chave) and semente_b64 not in repr(chave)


def test_so_pode_haver_uma_chave_ativa():
    chaves.garantir_chave_ativa()
    with pytest.raises(IntegrityError), transaction.atomic():
        ChaveInstancia.objects.create(did="did:key:z6Mkoutra", privada_cifrada="x")


def test_chave_aposentada_nao_conta_como_ativa_e_uma_nova_e_criada():
    antiga, _ = chaves.garantir_chave_ativa()
    antiga.estado = ChaveInstancia.Estado.APOSENTADA
    antiga.save()
    nova, criada = chaves.garantir_chave_ativa()
    assert criada and nova.pk != antiga.pk
    assert ChaveInstancia.objects.count() == 2


def test_corrida_na_criacao_devolve_a_chave_vencedora():
    """Se outro processo cria a ativa entre a leitura e o INSERT, o perdedor relê."""
    vencedora = ChaveInstancia.objects.create(did="did:key:z6Mkvencedora", privada_cifrada="x")
    leituras = iter([None, vencedora])
    with mock.patch.object(chaves, "chave_ativa", side_effect=lambda: next(leituras)):
        chave, criada = chaves.garantir_chave_ativa()
    assert chave.pk == vencedora.pk and not criada


# ── comando e bootstrap ─────────────────────────────────────────────────────

def _comando(*args):
    saida = io.StringIO()
    call_command("chave_instancia", *args, stdout=saida)
    return saida.getvalue()


def test_comando_sem_chave_falha_e_com_criar_cria():
    with pytest.raises(CommandError):
        _comando()
    saida = _comando("--criar")
    assert "did:key:z6Mk" in saida and "criada agora" in saida
    assert "criada agora" not in _comando("--criar")
    assert _comando().strip() == ChaveInstancia.objects.get().did


def test_comando_nao_imprime_a_privada():
    _comando("--criar")
    chave = ChaveInstancia.objects.get()
    assert decifrar(chave.privada_cifrada) not in _comando()


def test_bootstrap_cria_a_chave_da_instancia_uma_vez(monkeypatch):
    with mock.patch("apps.accounts.management.commands.bootstrap_instancia.Minio") as m:
        m.return_value.bucket_exists.return_value = True
        for _ in range(2):
            call_command("bootstrap_instancia", stdout=mock.MagicMock(), stderr=mock.MagicMock())
    assert ChaveInstancia.objects.filter(estado="ativa").count() == 1

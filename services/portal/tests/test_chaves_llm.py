"""Chave de LLM por organização: cifra em repouso, admin write-only, resolução
da chave (organização antes do `.env`) e a invariante do provider externo."""
from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import Organization
from apps.artifacts.extractors.llm_common import _chave_anthropic, gerar_texto
from apps.infrastructure import crypto
from apps.infrastructure.admin import LLMProviderForm
from apps.infrastructure.models import LLMProvider
from apps.events.models import Finalidade


@pytest.fixture
def org(db, django_user_model):
    dono = django_user_model.objects.create_user(username="dono", email="d@x.com", password="x")
    return Organization.objects.create(name="Org", slug="org", owner=dono)


def _provider(org, chave="sk-ant-org", **kw):
    p = LLMProvider(
        organization=org, name="Claude", provider_type="external", model_name="claude-sonnet-5",
        allowed_classifications=["publico", "interno"], **kw,
    )
    p.set_api_key(chave)
    p.save()
    return p


def test_chave_fica_cifrada_e_volta_em_claro_so_pelo_metodo(org):
    p = _provider(org)
    p.refresh_from_db()
    assert "sk-ant-org" not in p.api_key_encrypted
    assert p.get_api_key() == "sk-ant-org"
    assert p.tem_api_key


def test_set_api_key_vazio_apaga(org):
    p = _provider(org)
    p.set_api_key("  ")
    assert p.api_key_encrypted is None and p.get_api_key() == ""


def test_chave_de_cifra_trocada_vira_chave_ilegivel(org, settings):
    p = _provider(org)
    settings.FIELD_ENCRYPTION_KEY = "yeb6Zk5oEFTtfNRsx0-Lx8Lg3vJfYdXk0V0f8Kk3j7c="
    with pytest.raises(crypto.ChaveIlegivel):
        p.get_api_key()


def test_externo_nao_aceita_classificacao_restrita(org):
    p = LLMProvider(
        organization=org, name="x", provider_type="external", model_name="m",
        allowed_classifications=["publico", "confidencial"],
    )
    with pytest.raises(ValidationError):
        p.full_clean()


def test_local_aceita_todas_as_classificacoes(org):
    p = LLMProvider(
        organization=org, name="x", provider_type="local", model_name="m",
        allowed_classifications=["publico", "restrito", "confidencial"],
    )
    p.full_clean()


def test_form_admin_nao_expoe_o_campo_cifrado_e_grava_cifrado(org):
    assert "api_key_encrypted" not in LLMProviderForm().fields
    form = LLMProviderForm(data={
        "organization": org.pk, "name": "Claude", "provider_type": "external", "vendor": "anthropic",
        "model_name": "claude-sonnet-5", "allowed_classifications": '["publico"]', "is_active": True,
        "nova_api_key": "sk-ant-nova",
    })
    assert form.is_valid(), form.errors
    p = form.save()
    assert p.get_api_key() == "sk-ant-nova"


def test_form_admin_em_branco_mantem_e_remover_apaga(org):
    p = _provider(org)
    dados = {
        "organization": org.pk, "name": "Claude", "provider_type": "external", "vendor": "anthropic",
        "model_name": "claude-sonnet-5", "allowed_classifications": '["publico"]', "is_active": True,
    }
    form = LLMProviderForm(data=dados, instance=p)
    assert form.is_valid(), form.errors
    assert form.save().get_api_key() == "sk-ant-org"
    form = LLMProviderForm(data={**dados, "remover_api_key": True}, instance=p)
    assert form.is_valid(), form.errors
    assert form.save().get_api_key() == ""


def test_chave_da_organizacao_vence_a_do_env(org, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-env"
    _provider(org)
    assert _chave_anthropic(org.id) == "sk-ant-org"


def test_sem_provider_da_organizacao_cai_na_do_env(org, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-env"
    assert _chave_anthropic(org.id) == "sk-ant-env"
    assert _chave_anthropic(None) == "sk-ant-env"


def test_provider_inativo_ou_de_outra_organizacao_nao_vale(org, settings, django_user_model):
    settings.ANTHROPIC_API_KEY = ""
    _provider(org, is_active=False)
    outra = Organization.objects.create(name="Outra", slug="outra", owner=org.owner)
    _provider(outra, chave="sk-ant-outra")
    assert _chave_anthropic(org.id) == ""


def test_chave_ilegivel_da_organizacao_cai_na_do_env(org, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-env"
    p = _provider(org)
    LLMProvider.objects.filter(pk=p.pk).update(api_key_encrypted="lixo-nao-cifrado")
    assert _chave_anthropic(org.id) == "sk-ant-env"


def test_gerar_texto_usa_a_chave_da_organizacao(org, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-env"
    _provider(org)
    mensagem = mock.Mock(model="m", stop_reason="end_turn", id="r1")
    mensagem.content = [mock.Mock(text="ok")]
    mensagem.usage = mock.Mock(input_tokens=1, output_tokens=1)
    with mock.patch("anthropic.Anthropic") as cliente:
        cliente.return_value.messages.create.return_value = mensagem
        texto, _ = gerar_texto(
            "anthropic", "claude-sonnet-5", "s", "p",
            finalidade=Finalidade.ESTRUTURACAO_MANUAL, tenant_id=org.id,
        )
    assert texto == "ok"
    cliente.assert_called_once_with(api_key="sk-ant-org")


def test_gerar_texto_sem_nenhuma_chave_levanta(org, settings):
    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(RuntimeError, match="Chave Anthropic"):
        gerar_texto("anthropic", "m", "s", "p", finalidade=Finalidade.ESTRUTURACAO_MANUAL, tenant_id=org.id)

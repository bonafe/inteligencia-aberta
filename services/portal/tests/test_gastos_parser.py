"""Gastos lendo o parser de página: extração válida → alegações → lançamentos, sem LLM."""
import copy
import json
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extracao_pagina import extrair_com_parser
from apps.artifacts.extractors import parser_pagina as P
from apps.artifacts.models import (
    Artifact, Claim, DocumentText, EstruturacaoLLM, ExtracaoPagina, SchemaPagina, URLPatternCache,
)
from apps.events.models import PipelineEvent
from apps.financeiro import lancamentos as L
from apps.financeiro.models import Conta, Movimento
from tests.test_parser_pagina import AMOSTRA, OUTRO_MES, SCHEMA

pytestmark = pytest.mark.django_db
HASH = "ni:///sha-256;" + "A" * 43


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


@pytest.fixture
def padrao(org):
    return URLPatternCache.objects.create(tenant=org, domain="banco.example", path_pattern="/fatura",
                                          structure_fingerprint="f", page_type="tabular_generico", confidence=0.9)


def _schema(org, padrao, alimenta=False, versao=1, status=SchemaPagina.Status.APROVADO):
    parser, _ = P.montar_parser(SCHEMA, AMOSTRA)
    return SchemaPagina.objects.create(
        tenant=org, padrao=padrao, dominio=padrao.domain, caminho=padrao.path_pattern, versao=versao, status=status, schema=SCHEMA, parser=parser,
        classification_level="restrito", provider="ollama", model_name="qwen2.5:7b", alimenta_gastos=alimenta,
    )


def _captura(org, padrao, blob_hash=HASH):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://banco.example/fatura", "mhtml_path": "x"},
        blob_hash=blob_hash,
    )
    return DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", title="Fatura",
                                       url_pattern_cache=padrao)


def test_extracao_valida_vira_lancamentos_com_secao_e_evidencia(org, padrao):
    s = _schema(org, padrao)
    doc = _captura(org, padrao)
    e = extrair_com_parser(doc, OUTRO_MES)
    r = L.registrar_extracao(e)
    assert r.lancamentos == 5
    ls = list(Movimento.objects.order_by("indice"))
    assert {l.fonte for l in ls} == {"parser"} and {l.fonte_id for l in ls} == {s.id}
    assert [l.secao for l in ls] == ["Restaurantes"] * 3 + ["Supermercados"] * 2
    assert str(ls[0].data) == "2026-10-01"                     # ano pelo vencimento 28/11/2026
    c = Claim.objects.filter(produtor="parser-pagina", predicado="ia:valor").first()
    assert c.produtor_versao == "v1" and c.modelo == "" and c.autor_ref == "dominio:banco.example"
    assert c.evidencias.get().localizador["extracao"] == str(e.id)


def test_rodar_o_parser_de_novo_nao_duplica(org, padrao):
    _schema(org, padrao)
    doc = _captura(org, padrao)
    L.registrar_extracao(extrair_com_parser(doc, OUTRO_MES))
    alegacoes = Claim.objects.count()
    r = L.registrar_extracao(extrair_com_parser(doc, OUTRO_MES))   # outra execução, mesmo schema
    assert r.alegacoes_criadas == 0 and not r.substituiu
    assert Claim.objects.count() == alegacoes and Movimento.objects.count() == 5


def test_trocar_a_leitura_do_llm_pelo_parser_nao_soma_duas_vezes(org, padrao):
    _schema(org, padrao)
    doc = _captura(org, padrao)
    llm = EstruturacaoLLM.objects.create(
        document_text=doc, tenant=org, provider="ollama", model_name="qwen2.5:7b",
        status=EstruturacaoLLM.Status.CONCLUIDO,
        structured_data={"lancamentos": [{"data": "01/10", "valor": "50,00", "descricao": "BAR CINCO"}]},
    )
    L.registrar(llm)
    assert Movimento.objects.get().fonte == "estruturacao"
    r = L.registrar_extracao(extrair_com_parser(doc, OUTRO_MES))
    assert r.substituiu
    assert Claim.objects.filter(produtor="estruturacao-llm", estado=Claim.Estado.ATIVA).exists()
    assert set(Movimento.objects.values_list("fonte", flat=True)) == {"parser"}
    L.registrar(llm)                                                # e de volta
    assert set(Movimento.objects.values_list("fonte", flat=True)) == {"estruturacao"}
    assert L.reconstruir(org) == 1


def test_extracao_que_nao_validou_nao_vira_lancamento(org, padrao):
    _schema(org, padrao)
    doc = _captura(org, padrao)
    e = extrair_com_parser(doc, OUTRO_MES.replace("Vencimento", "Data limite"))
    with pytest.raises(ValueError):
        L.registrar_extracao(e)


# ── automático, pela chave do schema ────────────────────────────────────────

def test_com_a_chave_ligada_a_captura_alimenta_os_gastos_sozinha(org, padrao):
    _schema(org, padrao, alimenta=True)
    doc = _captura(org, padrao)
    extrair_com_parser(doc, OUTRO_MES)
    assert Movimento.objects.count() == 5
    assert PipelineEvent.objects.filter(stage="financeiro.lancamentos", status="ok").exists()


def test_com_a_chave_desligada_nada_entra(org, padrao):
    _schema(org, padrao, alimenta=False)
    extrair_com_parser(_captura(org, padrao), OUTRO_MES)
    assert not Movimento.objects.exists()


def test_extracao_invalida_nao_entra_mesmo_com_a_chave(org, padrao):
    _schema(org, padrao, alimenta=True)
    extrair_com_parser(_captura(org, padrao), OUTRO_MES.replace("Vencimento", "Data limite"))
    assert not Movimento.objects.exists()


def test_falha_no_registro_automatico_nao_derruba_a_extracao(org, padrao):
    _schema(org, padrao, alimenta=True)
    doc = _captura(org, padrao, blob_hash="")                       # sem hash não há evidência
    e = extrair_com_parser(doc, OUTRO_MES)
    assert e.status == ExtracaoPagina.Status.VALIDO and not Movimento.objects.exists()
    assert PipelineEvent.objects.filter(stage="financeiro.lancamentos", status="falhou").exists()


# ── telas ───────────────────────────────────────────────────────────────────

def _post(client, url, corpo=None):
    return client.post(url, json.dumps(corpo) if corpo is not None else "", content_type="application/json")


def test_chave_e_de_dono_ou_admin_e_a_nova_versao_herda(client, org, padrao):
    s = _schema(org, padrao)
    membro = User.objects.create_user(username="m", password="x")
    Membership.objects.create(user=membro, organization=org, role=Membership.Role.MEMBER)
    client.force_login(membro)
    assert _post(client, f"/artifacts/schemas/{s.id}/gastos/", {"ativo": True}).status_code == 403
    client.force_login(org.owner)
    assert _post(client, f"/artifacts/schemas/{s.id}/gastos/", {"ativo": True}).json() == {"alimenta_gastos": True}
    nova = SchemaPagina.objects.get(id=_post(client, f"/artifacts/schemas/{s.id}/nova-versao/").json()["id"])
    assert nova.alimenta_gastos


def test_aprovar_versao_nova_herda_a_chave_da_substituida(client, org, padrao):
    client.force_login(org.owner)
    anterior = _schema(org, padrao, alimenta=True)
    rascunho = _schema(org, padrao, versao=2, status=SchemaPagina.Status.RASCUNHO)
    with mock.patch("apps.artifacts.tasks._ler_mhtml", return_value=b""), \
         mock.patch("apps.artifacts.tasks._html_do_mhtml", return_value=(AMOSTRA, "utf-8")):
        rascunho.amostra = Artifact.objects.create(
            artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
            classification_level="restrito", content={"mhtml_path": "x"})
        rascunho.save()
        assert _post(client, f"/artifacts/schemas/{rascunho.id}/aprovar/",
                     {"substitui": str(anterior.id)}).status_code == 200
    rascunho.refresh_from_db()
    assert rascunho.alimenta_gastos


def test_botao_registrar_extracao(client, org, padrao):
    client.force_login(org.owner)
    _schema(org, padrao)
    e = extrair_com_parser(_captura(org, padrao), OUTRO_MES)
    resp = _post(client, f"/gastos/registrar-extracao/{e.id}/")
    assert resp.status_code == 200 and resp.json()["lancamentos"] == 5
    invalida = extrair_com_parser(_captura(org, padrao), OUTRO_MES.replace("Vencimento", "Data limite"))
    assert _post(client, f"/gastos/registrar-extracao/{invalida.id}/").status_code == 400


def test_isolamento(client, org, padrao):
    _schema(org, padrao)
    e = extrair_com_parser(_captura(org, padrao), OUTRO_MES)
    estranho = User.objects.create_user(username="e", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert _post(client, f"/gastos/registrar-extracao/{e.id}/").status_code == 404
    assert _post(client, f"/artifacts/schemas/{e.schema_id}/gastos/", {"ativo": True}).status_code == 404

"""Execução do parser aprovado do tipo de página: na captura e sob demanda, sempre validada."""
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extracao_pagina import extrair_com_parser
from apps.artifacts.extractors import parser_pagina as P
from apps.artifacts.models import Artifact, DocumentText, ExtracaoPagina, SchemaPagina, URLPatternCache
from apps.events.models import PipelineEvent
from tests.test_parser_pagina import AMOSTRA, OUTRO_MES, SCHEMA

pytestmark = pytest.mark.django_db

PROSA = "<p>" + "Esta é a fatura do seu cartão de crédito com os lançamentos do período. " * 8 + "</p>"


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


def _aprovar(org, padrao, schema=SCHEMA, html=AMOSTRA):
    parser, _ = P.montar_parser(schema, html)
    return SchemaPagina.objects.create(
        tenant=org, padrao=padrao, versao=1, status=SchemaPagina.Status.APROVADO, schema=schema, parser=parser,
        classification_level="restrito", provider="ollama", model_name="qwen2.5:7b",
    )


def _captura(org, padrao, nivel="restrito"):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level=nivel, content={"url": "https://banco.example/fatura", "mhtml_path": "x"},
    )
    doc = DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", title="Fatura",
                                      url_pattern_cache=padrao)
    return artefato, doc


# ── serviço ─────────────────────────────────────────────────────────────────

def test_sem_schema_aprovado_nao_faz_nada(org, padrao):
    _, doc = _captura(org, padrao)
    assert extrair_com_parser(doc, OUTRO_MES) is None
    assert not ExtracaoPagina.objects.exists()
    assert not PipelineEvent.objects.filter(stage="extracao.parser_pagina").exists()


def test_rascunho_nao_roda(org, padrao):
    s = _aprovar(org, padrao)
    s.status = SchemaPagina.Status.RASCUNHO
    s.save()
    _, doc = _captura(org, padrao)
    assert extrair_com_parser(doc, OUTRO_MES) is None


def test_mes_seguinte_valida_sem_llm(org, padrao):
    _aprovar(org, padrao)
    _, doc = _captura(org, padrao)
    e = extrair_com_parser(doc, OUTRO_MES)
    assert e.status == ExtracaoPagina.Status.VALIDO and e.origem == "captura"
    assert e.dados["campos"]["vencimento"] == "28/11/2026"
    assert len(e.dados["listas"]["lancamentos"]["itens"]) == 5
    assert e.classification_level == "restrito" and e.invariantes[0]["ok"]
    ev = PipelineEvent.objects.get(stage="extracao.parser_pagina")
    assert ev.status == "ok" and ev.payload["itens"] == 5


def test_pagina_mudada_nao_valida_e_diz_por_que(org, padrao):
    _aprovar(org, padrao)
    _, doc = _captura(org, padrao)
    mudada = OUTRO_MES.replace("Vencimento", "Data limite")
    e = extrair_com_parser(doc, mudada)
    assert e.status == ExtracaoPagina.Status.INVALIDO
    assert "campo obrigatório 'vencimento' vazio" in e.problemas
    ev = PipelineEvent.objects.get(stage="extracao.parser_pagina")
    assert ev.status == "falhou" and "vencimento" in ev.message


def test_parser_corrompido_vira_erro_e_nao_levanta(org, padrao):
    s = _aprovar(org, padrao)
    s.parser = {"formato": 1, "spec": {"records": [], "scalars": [{"name": "x"}]}, "listas": []}
    s.save()
    _, doc = _captura(org, padrao)
    e = extrair_com_parser(doc, OUTRO_MES)
    assert e.status == ExtracaoPagina.Status.ERRO and e.problemas
    assert PipelineEvent.objects.get(stage="extracao.parser_pagina").status == "falhou"


# ── na captura, de ponta a ponta ────────────────────────────────────────────

def test_a_captura_roda_o_parser_aprovado(org, padrao):
    from apps.artifacts import tasks

    _aprovar(org, padrao)
    msg = MIMEMultipart("related")
    msg.attach(MIMEText(OUTRO_MES.replace("<body>", "<body>" + PROSA), "html", "utf-8"))
    resposta = mock.MagicMock()
    resposta.read.return_value = msg.as_bytes()
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://banco.example/fatura", "mhtml_path": "x"},
    )
    with mock.patch.object(tasks, "Minio") as minio, \
            mock.patch("apps.artifacts.extractors.detect_page_type",
                       return_value=("tabular_generico", 0.9, "teste", padrao.id, padrao)), \
            mock.patch.object(tasks.fragment_text, "delay"):
        minio.return_value.get_object.return_value = resposta
        resultado = tasks.extract_text_from_mhtml.run(str(artefato.id))
    assert resultado["status"] == "success"
    e = ExtracaoPagina.objects.get(document_text__document=artefato)
    assert e.status == ExtracaoPagina.Status.VALIDO and e.origem == "captura"
    assert e.dados["campos"]["saldo_anterior"] == "200,00"


def test_um_parser_que_levanta_nao_derruba_a_captura(org, padrao):
    from apps.artifacts import tasks

    _aprovar(org, padrao)
    msg = MIMEMultipart("related")
    msg.attach(MIMEText(OUTRO_MES.replace("<body>", "<body>" + PROSA), "html", "utf-8"))
    resposta = mock.MagicMock()
    resposta.read.return_value = msg.as_bytes()
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://banco.example/fatura", "mhtml_path": "x"},
    )
    with mock.patch.object(tasks, "Minio") as minio, \
            mock.patch("apps.artifacts.extractors.detect_page_type",
                       return_value=("tabular_generico", 0.9, "teste", padrao.id, padrao)), \
            mock.patch.object(tasks.fragment_text, "delay"), \
            mock.patch("apps.artifacts.extracao_pagina.schema_aprovado", side_effect=RuntimeError("banco fora")):
        minio.return_value.get_object.return_value = resposta
        assert tasks.extract_text_from_mhtml.run(str(artefato.id))["status"] == "success"
    assert DocumentText.objects.filter(document=artefato).exists()


# ── views ───────────────────────────────────────────────────────────────────

@pytest.fixture
def html_da_captura():
    with mock.patch("apps.artifacts.tasks._ler_mhtml", return_value=b""), \
         mock.patch("apps.artifacts.tasks._html_do_mhtml", return_value=(OUTRO_MES, "utf-8")):
        yield


def test_rodar_parser_sob_demanda_e_listar(client, org, padrao, html_da_captura):
    client.force_login(org.owner)
    s = _aprovar(org, padrao)
    artefato, _ = _captura(org, padrao)
    resp = client.post(f"/artifacts/{artefato.id}/extracoes-pagina/")
    assert resp.status_code == 200 and resp.json()["status"] == "valido" and resp.json()["origem"] == "manual"
    lista = client.get(f"/artifacts/{artefato.id}/extracoes-pagina/").json()
    assert lista["schema_aprovado"]["versao"] == 1 and len(lista["extracoes"]) == 1
    pagina = client.get(f"/artifacts/schemas/{s.id}/").content.decode()
    assert "Uso nas capturas" in pagina and "1 execução(ões): 1 válida(s)" in pagina


def test_rodar_parser_sem_schema_aprovado_e_400(client, org, padrao, html_da_captura):
    client.force_login(org.owner)
    artefato, _ = _captura(org, padrao)
    assert client.post(f"/artifacts/{artefato.id}/extracoes-pagina/").status_code == 400
    assert client.get(f"/artifacts/{artefato.id}/extracoes-pagina/").json() == {"schema_aprovado": None, "extracoes": []}


def test_tela_do_schema_avisa_quando_a_ultima_nao_validou(client, org, padrao):
    client.force_login(org.owner)
    s = _aprovar(org, padrao)
    _, doc = _captura(org, padrao)
    extrair_com_parser(doc, OUTRO_MES.replace("Vencimento", "Data limite"))
    pagina = client.get(f"/artifacts/schemas/{s.id}/").content.decode()
    assert "não validou" in pagina and "campo obrigatório &#x27;vencimento&#x27; vazio" in pagina


def test_isolamento(client, org, padrao, html_da_captura):
    _aprovar(org, padrao)
    artefato, _ = _captura(org, padrao)
    estranho = User.objects.create_user(username="e", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert client.get(f"/artifacts/{artefato.id}/extracoes-pagina/").status_code == 404
    assert client.post(f"/artifacts/{artefato.id}/extracoes-pagina/").status_code == 404

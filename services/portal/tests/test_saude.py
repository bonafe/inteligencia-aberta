"""Saúde, a segunda extensão (ADR 022, etapa 5): laudo → alegações `saude:` (piso confidencial,
o laudo sobe junto e auditado) → resultados; o núcleo entrega a extração ao consumidor."""
from decimal import Decimal

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao
from apps.artifacts.models import (
    Artifact, AuditLog, Claim, DocumentText, EstruturacaoLLM, ExtracaoPagina, SchemaPagina, URLPatternCache,
)
from apps.events.models import PipelineEvent
from apps.extensoes import registro
from apps.saude import exames as X
from apps.saude.models import ResultadoExame

pytestmark = pytest.mark.django_db
HASH = "ni:///sha-256;" + "B" * 43

LAUDO = {
    "laboratorio": "Laboratório Exemplo",
    "data_coleta": "12/03/2026",
    "exames": [
        {"exame": "Hemoglobina glicada", "resultado": "5,4", "unidade": "%", "valores_de_referencia": "até 5,6"},
        {"exame": "Glicose", "resultado": "92", "unidade": "mg/dL", "referencia": "70 a 99", "material": "Soro"},
        {"exame": "HIV 1 e 2", "resultado": "Não reagente"},
        {"observacao": "Liberado por Dra. X"},
    ],
}


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _laudo(org, nivel="interno", dados=LAUDO, allow_llm=True):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level=nivel, allow_external_llm=allow_llm,
        content={"url": "https://lab.example/laudo"}, blob_hash=HASH,
    )
    doc = DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", title="Laudo")
    return EstruturacaoLLM.objects.create(document_text=doc, tenant=org, provider="ollama", model_name="qwen2.5:7b",
                                          status=EstruturacaoLLM.Status.CONCLUIDO, structured_data=dados)


# ── parsing (puro) ──────────────────────────────────────────────────────────

def test_extrair_resultados_por_forma():
    resultados, documento = X.extrair_resultados(LAUDO)
    assert [r.campos["saude:analito"] for r in resultados] == ["Hemoglobina glicada", "Glicose", "HIV 1 e 2"]
    assert resultados[0].campos["saude:referencia"] == "até 5,6" and resultados[1].campos["saude:material"] == "Soro"
    assert documento == {"saude:laboratorio": "Laboratório Exemplo", "saude:dataColeta": "12/03/2026"}
    assert X.chave_analito("Hemoglobina  glicada!") == "HEMOGLOBINA GLICADA"
    assert str(X.parse_data("coleta 12/03/26")) == "2026-03-12"


# ── registro e piso ─────────────────────────────────────────────────────────

def test_registrar_vira_resultados_confidenciais_e_eleva_o_laudo(org):
    e = _laudo(org)
    r = X.registrar(e, user_id=org.owner.id)
    assert r.resultados == 3
    artefato = Artifact.objects.get(pk=e.document_text.document_id)
    assert artefato.classification_level == "confidencial" and not artefato.allow_external_llm
    (auditoria,) = AuditLog.objects.filter(operation="classificacao.elevada")
    assert auditoria.artifact == artefato and auditoria.metadata == {"de": "interno", "para": "confidencial"}
    assert set(Claim.objects.filter(predicado__startswith="saude:").values_list("classification_level", flat=True)) \
        == {"confidencial"}
    rs = {x.analito_chave: x for x in ResultadoExame.objects.all()}
    assert rs["HEMOGLOBINA GLICADA"].valor == Decimal("5.4") and rs["HIV 1 E 2"].valor is None
    assert {x.classification_level for x in rs.values()} == {"confidencial"}
    assert str(rs["GLICOSE"].data) == "2026-03-12" and rs["GLICOSE"].laboratorio == "Laboratório Exemplo"
    assert PipelineEvent.objects.filter(stage="saude.exames", status="ok").exists()

    # De novo: nada duplica, e o laudo já confidencial não gera outra auditoria de elevação.
    assert X.registrar(e).alegacoes_criadas == 0
    assert AuditLog.objects.filter(operation="classificacao.elevada").count() == 1


def test_elevar_nunca_rebaixa_e_entrada_invalida_nao_eleva(org):
    e = _laudo(org)
    artefato = e.document_text.document
    with pytest.raises(ValueError):            # evidência inválida: recusada antes de elevar
        registrar_alegacao(artefato=artefato, sujeito_ref=f"mencao:{HASH}#x/1", predicado="saude:analito",
                           objeto_literal="Glicose", autor_ref="dominio:lab.example", produtor="t", produtor_versao="1",
                           evidencias=[EvidenciaEntrada(blob_hash="ruim", localizador_tipo="t")])
    artefato.refresh_from_db()
    assert artefato.classification_level == "interno"
    from apps.artifacts.elevacao import elevar
    assert elevar(artefato, "confidencial", motivo="teste") and not elevar(artefato, "restrito", motivo="teste")
    assert Artifact.objects.get(pk=artefato.pk).classification_level == "confidencial"


def test_historico_junta_laudos_pelo_analito(org):
    X.registrar(_laudo(org))
    outro = dict(LAUDO, data_coleta="10/09/2026", exames=[{"exame": "Hemoglobina Glicada", "resultado": "5,9"}])
    e2 = _laudo(org, dados=outro)
    Artifact.objects.filter(pk=e2.document_text.document_id).update(blob_hash="ni:///sha-256;" + "C" * 43)
    e2.document_text.document.refresh_from_db()
    X.registrar(e2)
    assert list(ResultadoExame.objects.filter(analito_chave="HEMOGLOBINA GLICADA").values_list("resultado", flat=True)) \
        == ["5,4", "5,9"]


def test_leitura_sem_exames_e_vazio(org):
    assert X.registrar(_laudo(org, dados={"texto": "nada"})).resultados == 0
    assert PipelineEvent.objects.filter(stage="saude.exames", status="vazio").exists()
    assert Artifact.objects.get().classification_level == "interno"       # nada registrado, nada elevado


# ── o núcleo entrega a extração ao consumidor ───────────────────────────────

def _extracao(org, consumidores, dados=LAUDO):
    e = _laudo(org)
    padrao = URLPatternCache.objects.create(tenant=org, domain="lab.example", path_pattern="/laudo",
                                            structure_fingerprint="f", page_type="tabular_generico", confidence=0.9)
    schema = SchemaPagina.objects.create(
        tenant=org, padrao=padrao, dominio="lab.example", caminho="/laudo", versao=1,
        status=SchemaPagina.Status.APROVADO, schema={}, parser={}, classification_level="interno",
        provider="ollama", model_name="qwen2.5:7b", consumidores=consumidores)
    return ExtracaoPagina.objects.create(tenant=org, document_text=e.document_text, schema=schema, dados=dados,
                                         status=ExtracaoPagina.Status.VALIDO, origem=ExtracaoPagina.Origem.CAPTURA,
                                         classification_level="interno")


def test_consumidor_ligado_recebe_a_extracao_valida(org):
    extracao = _extracao(org, ["saude.exames"])
    assert ResultadoExame.objects.count() == 3
    extracao.refresh_from_db()
    assert extracao.classification_level == "confidencial"               # a extração do laudo subiu junto
    c = Claim.objects.filter(predicado="saude:resultado").first()
    assert c.produtor == "parser-pagina" and c.evidencias.get().localizador["extracao"] == str(extracao.id)


def test_consumidor_desligado_ou_desconhecido_nao_faz_nada(org):
    _extracao(org, ["outra.coisa"])
    assert not ResultadoExame.objects.exists()


def test_falha_de_um_consumidor_nao_impede_o_outro_nem_a_extracao(org):
    from unittest import mock

    with mock.patch("apps.financeiro.lancamentos.registrar_extracao", side_effect=RuntimeError("quebrou")):
        extracao = _extracao(org, ["fin.movimentos", "saude.exames"])
    assert extracao.status == ExtracaoPagina.Status.VALIDO
    assert ResultadoExame.objects.count() == 3
    (falha,) = PipelineEvent.objects.filter(stage="extensao.consumidor", status="falhou")
    assert falha.payload["consumidor"] == "fin.movimentos" and "quebrou" in falha.message


# ── tela ────────────────────────────────────────────────────────────────────

def test_tela_lista_audita_e_isola(client, org):
    X.registrar(_laudo(org))
    client.force_login(org.owner)
    resp = client.get("/saude/")
    assert resp.status_code == 200 and len(resp.context["analitos"]) == 3
    assert AuditLog.objects.filter(operation="saude.ver_resultados", user=org.owner).count() == 1

    estranho = User.objects.create_user(username="estranho", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert client.get(f"/saude/?organizacao={org.pk}").status_code == 404
    assert client.get("/saude/").context["analitos"] == []
    e = EstruturacaoLLM.objects.first()
    assert client.post(f"/saude/registrar/{e.id}/").status_code == 404


def test_acao_de_leitura_registra_pelo_visualizador(client, org):
    e = _laudo(org)
    client.force_login(org.owner)
    resp = client.post(f"/saude/registrar/{e.id}/")
    assert resp.status_code == 200 and resp.json()["resultados"] == 3 and resp.json()["link"]["url"] == "/saude/"
    rotulos = [a["rotulo"] for a in registro.acoes_leitura()]
    assert "Registrar exames" in rotulos and "Registrar lançamentos" in rotulos
    assert {i.rotulo for i in registro.menu()} >= {"Saúde", "Gastos"}

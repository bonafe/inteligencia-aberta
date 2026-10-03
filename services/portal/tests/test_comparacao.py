"""Comparacao mistura fontes legadas com EstruturacaoLLM e é autocontida —
sobrevive à exclusão do DocumentText de origem (snapshot embutido em resultado).
"""
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact, Comparacao, DocumentText, EstruturacaoLLM
from apps.artifacts.tasks import comparar_llm

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="Org de Teste", slug="org-teste", org_type="individual", owner=dono)


def _artifact(tenant, classification=Artifact.ClassificationLevel.PUBLIC):
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT,
        content={"url": "https://exemplo.org/pagina"},
        classification_level=classification,
        tenant=tenant,
        info_type=Artifact.InfoType.FACT,
    )


def _document_text(artifact, **kw):
    kw.setdefault("text", "texto extraído")
    kw.setdefault("dom_representation", "<esqueleto/>")
    return DocumentText.objects.create(document=artifact, **kw)


def test_secoes_mistura_campo_legado_e_estruturacao_llm(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact, dados_estruturados_dom2parser={"cnpj": "111"})
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.CONCLUIDO, structured_data={"cnpj": "222"},
    )
    comparacao = Comparacao.objects.create(
        artifact=artifact, tenant=tenant,
        referencias=[
            {"tipo": "campo_legado", "campo": "dados_estruturados_dom2parser", "label": "dom2parser"},
            {"tipo": "estruturacao_llm", "id": str(execucao.id), "label": "ollama:llama3.1:8b"},
        ],
        modelo_juiz_provider="ollama", modelo_juiz_model_name="llama3.1:8b",
    )

    with mock.patch(
        "apps.artifacts.extractors.comparador.julgar_comparacao",
        return_value={"resumo": "ok", "mais_completo": "ollama:llama3.1:8b", "completude": {}, "discrepancias": []},
    ) as m:
        comparar_llm(str(comparacao.id))

    m.assert_called_once()
    secoes_enviadas = m.call_args[0][2]
    assert {s["label"] for s in secoes_enviadas} == {"dom2parser", "ollama:llama3.1:8b"}

    comparacao.refresh_from_db()
    assert comparacao.status == Comparacao.Status.CONCLUIDO
    assert len(comparacao.resultado["secoes"]) == 2


def test_referencia_a_estruturacao_nao_concluida_e_ignorada(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact, dados_estruturados_dom2parser={"cnpj": "111"})
    execucao_pendente = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.PENDENTE,
    )
    comparacao = Comparacao.objects.create(
        artifact=artifact, tenant=tenant,
        referencias=[
            {"tipo": "campo_legado", "campo": "dados_estruturados_dom2parser", "label": "dom2parser"},
            {"tipo": "estruturacao_llm", "id": str(execucao_pendente.id), "label": "ollama"},
        ],
        modelo_juiz_provider="ollama", modelo_juiz_model_name="llama3.1:8b",
    )

    comparar_llm(str(comparacao.id))

    comparacao.refresh_from_db()
    assert comparacao.status == Comparacao.Status.FALHOU  # só 1 seção válida


def test_comparacao_sobrevive_a_exclusao_do_document_text(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact, dados_estruturados_dom2parser={"cnpj": "111"}, structured_data={"cnpj": "222"})
    comparacao = Comparacao.objects.create(
        artifact=artifact, tenant=tenant,
        referencias=[
            {"tipo": "campo_legado", "campo": "dados_estruturados_dom2parser", "label": "dom2parser"},
            {"tipo": "campo_legado", "campo": "structured_data", "label": "cascata"},
        ],
        modelo_juiz_provider="ollama", modelo_juiz_model_name="llama3.1:8b",
    )

    with mock.patch(
        "apps.artifacts.extractors.comparador.julgar_comparacao",
        return_value={"resumo": "ok"},
    ):
        comparar_llm(str(comparacao.id))

    doc_text.delete()

    persistida = Comparacao.objects.get(id=comparacao.id)
    assert persistida.status == Comparacao.Status.CONCLUIDO
    assert len(persistida.resultado["secoes"]) == 2
    assert persistida.resultado["secoes"][0]["dados"] == {"cnpj": "111"}


def test_isolamento_de_tenant_em_comparacoes_list(client, tenant):
    dono = User.objects.get(username="dono")
    Membership.objects.create(user=dono, organization=tenant, role="owner")
    outro_tenant_dono = User.objects.create_user(username="outro", password="x")
    outra_org = Organization.objects.create(name="Outra Org", slug="outra-org", org_type="individual", owner=outro_tenant_dono)
    Membership.objects.create(user=outro_tenant_dono, organization=outra_org, role="owner")

    artifact = _artifact(tenant)

    client.force_login(outro_tenant_dono)
    resp = client.get(f"/artifacts/{artifact.id}/comparacoes/")
    assert resp.status_code == 404

"""EstruturacaoLLM é estritamente aditiva e respeita a política de classificação.

O teste mais importante da lista: um artefato restrito/confidencial nunca pode
disparar o provider externo (Claude) — nem via view, nem via task (defesa em
profundidade), mesmo sem tocar em policy_engine.py.
"""
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact, DocumentText, EstruturacaoLLM
from apps.artifacts.tasks import estruturar_llm_manual

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


def _document_text(artifact):
    return DocumentText.objects.create(
        document=artifact, text="texto extraído", dom_representation="<esqueleto/>",
        page_type="artigo",
    )


def test_task_grava_concluido_quando_llm_retorna_dado(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
    )
    anterior = doc_text.structured_data

    with mock.patch(
        "apps.artifacts.extractors.estruturacao_manual.estruturar_manual",
        return_value={"categoria": "teste", "page_type": "artigo", "structured_data": {"campo": "valor"}},
    ) as m:
        estruturar_llm_manual(str(execucao.id))

    m.assert_called_once()
    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.CONCLUIDO
    assert execucao.structured_data == {"campo": "valor"}

    doc_text.refresh_from_db()
    assert doc_text.structured_data == anterior  # nunca sobrescreve DocumentText


def test_task_grava_vazio_quando_llm_nao_produz_dado(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
    )

    with mock.patch(
        "apps.artifacts.extractors.estruturacao_manual.estruturar_manual",
        return_value={"categoria": "teste", "page_type": "artigo", "structured_data": None},
    ):
        estruturar_llm_manual(str(execucao.id))

    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.VAZIO


def test_task_grava_falhou_quando_llm_levanta(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
    )

    with mock.patch(
        "apps.artifacts.extractors.estruturacao_manual.estruturar_manual",
        side_effect=RuntimeError("Ollama indisponível"),
    ):
        estruturar_llm_manual(str(execucao.id))

    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.FALHOU
    assert "indisponível" in execucao.error_message


def test_task_recusa_provider_externo_para_artefato_restrito(tenant):
    artifact = _artifact(tenant, classification=Artifact.ClassificationLevel.RESTRICTED)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="anthropic", model_name="claude-sonnet-5",
    )

    with mock.patch("apps.artifacts.extractors.estruturacao_manual.estruturar_manual") as m:
        estruturar_llm_manual(str(execucao.id))
        m.assert_not_called()

    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.FALHOU
    assert "não permitido" in execucao.error_message


def test_task_nao_sobrescreve_cancelamento_que_chegou_primeiro(tenant):
    """Simula o cancelamento vencendo a corrida contra o worker: a task não
    deve chamar o LLM nem sobrescrever o status quando já está cancelado."""
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.CANCELADO, error_message="cancelado por dono",
    )

    with mock.patch("apps.artifacts.extractors.estruturacao_manual.estruturar_manual") as m:
        estruturar_llm_manual(str(execucao.id))
        m.assert_not_called()

    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.CANCELADO
    assert execucao.error_message == "cancelado por dono"


def test_view_recusa_provider_externo_para_artefato_confidencial(client, tenant):
    user = User.objects.get(username="dono")
    Membership.objects.create(user=user, organization=tenant, role="owner")

    artifact = _artifact(tenant, classification=Artifact.ClassificationLevel.CONFIDENTIAL)
    _document_text(artifact)

    client.force_login(user)
    resp = client.post(
        f"/artifacts/{artifact.id}/estruturar-llm/",
        data={"provider": "anthropic", "model_name": "claude-sonnet-5"},
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert not EstruturacaoLLM.objects.filter(document_text__document=artifact).exists()


def test_view_cancelar_revoga_task_e_marca_cancelado(client, tenant):
    user = User.objects.get(username="dono")
    Membership.objects.create(user=user, organization=tenant, role="owner")

    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.EXECUTANDO, celery_task_id="fake-task-id",
    )

    client.force_login(user)
    with mock.patch("celery.current_app.control.revoke") as m:
        resp = client.post(f"/artifacts/{artifact.id}/estruturacoes/{execucao.id}/cancelar/")

    assert resp.status_code == 200
    m.assert_called_once_with("fake-task-id", terminate=True, signal="SIGKILL")
    execucao.refresh_from_db()
    assert execucao.status == EstruturacaoLLM.Status.CANCELADO


def test_view_cancelar_recusa_execucao_ja_finalizada(client, tenant):
    user = User.objects.get(username="dono")
    Membership.objects.create(user=user, organization=tenant, role="owner")

    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.CONCLUIDO,
    )

    client.force_login(user)
    resp = client.post(f"/artifacts/{artifact.id}/estruturacoes/{execucao.id}/cancelar/")
    assert resp.status_code == 400

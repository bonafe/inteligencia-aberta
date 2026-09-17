"""ArtifactFaviconView usa o favicon de outra captura do mesmo domínio
quando o artefato pedido não tem o dele próprio — mesma correção aplicada em
apps.artifacts.graph.montar_grafo (carga inicial do Mapa Vivo), aqui pro
caminho ao vivo (hidratação via WebSocket).
"""
import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="Org de Teste", slug="org-teste", org_type="individual", owner=dono)


@pytest.fixture
def logado(client, tenant):
    user = User.objects.get(username="dono")
    Membership.objects.create(user=user, organization=tenant, role="owner")
    client.force_login(user)
    return client


def _artifact(tenant, **kw):
    content = {"url": "https://exemplo.org/pagina", "mhtml_path": "captura.mhtml"}
    content.update(kw.pop("content", {}))
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, content=content, tenant=tenant,
        info_type=Artifact.InfoType.FACT, **kw,
    )


def test_devolve_favicon_proprio_quando_existe(tenant, logado):
    favicon = "data:image/png;base64,aGVsbG8="
    artifact = _artifact(tenant, content={"favicon_data_uri": favicon})

    resp = logado.get(f"/artifacts/{artifact.id}/favicon/")

    assert resp.status_code == 200
    assert resp.json()["favicon_data_uri"] == favicon


def test_cai_para_favicon_de_outra_captura_do_mesmo_dominio(tenant, logado):
    favicon = "data:image/png;base64,aGVsbG8="
    _artifact(tenant, content={"url": "https://exemplo.org/com-favicon", "favicon_data_uri": favicon})
    sem_favicon = _artifact(tenant, content={"url": "https://exemplo.org/sem-favicon"})

    resp = logado.get(f"/artifacts/{sem_favicon.id}/favicon/")

    assert resp.status_code == 200
    assert resp.json()["favicon_data_uri"] == favicon


def test_404_quando_nenhuma_captura_do_dominio_tem_favicon(tenant, logado):
    sem_favicon = _artifact(tenant)

    resp = logado.get(f"/artifacts/{sem_favicon.id}/favicon/")

    assert resp.status_code == 404


def test_nao_usa_favicon_de_dominio_diferente(tenant, logado):
    _artifact(tenant, content={"url": "https://outro.org/pagina",
                               "favicon_data_uri": "data:image/png;base64,aGVsbG8="})
    sem_favicon = _artifact(tenant, content={"url": "https://exemplo.org/sem-favicon"})

    resp = logado.get(f"/artifacts/{sem_favicon.id}/favicon/")

    assert resp.status_code == 404


def test_nao_vaza_favicon_de_outro_tenant(tenant, logado):
    outro_dono = User.objects.create_user(username="outro", password="x")
    outra_org = Organization.objects.create(
        name="Outra Org", slug="outra-org", org_type="individual", owner=outro_dono,
    )
    _artifact(outra_org, content={"url": "https://exemplo.org/com-favicon",
                                  "favicon_data_uri": "data:image/png;base64,aGVsbG8="})
    sem_favicon = _artifact(tenant, content={"url": "https://exemplo.org/sem-favicon"})

    resp = logado.get(f"/artifacts/{sem_favicon.id}/favicon/")

    assert resp.status_code == 404

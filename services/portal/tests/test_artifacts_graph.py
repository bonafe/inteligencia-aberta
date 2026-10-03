"""montar_grafo sintetiza nós/arestas do Mapa Vivo a partir das FKs já
existentes (Artifact→DocumentText→DocumentFragment/EstruturacaoLLM,
Artifact→Comparacao) — sem depender de ArtifactLineage nem de um banco de
grafo. Testa a função pura, sem view/HTTP.
"""
import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.graph import artifacts_para_mapa, montar_grafo
from apps.artifacts.models import (
    Artifact,
    Comparacao,
    DocumentFragment,
    DocumentText,
    EstruturacaoLLM,
)
from apps.cluster.models import Maquina
from apps.events.emit import emit

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="Org de Teste", slug="org-teste", org_type="individual", owner=dono)


def _artifact(tenant, **kw):
    content = {"url": "https://exemplo.org/pagina", "mhtml_path": "captura.mhtml", "title": "Página de Teste"}
    content.update(kw.pop("content", {}))
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT,
        content=content,
        tenant=tenant,
        info_type=Artifact.InfoType.FACT,
        **kw,
    )


def _document_text(artifact, **kw):
    kw.setdefault("text", "texto extraído")
    kw.setdefault("dom_representation", "<esqueleto/>")
    return DocumentText.objects.create(document=artifact, **kw)


def _grafo_para(tenant):
    artifacts = artifacts_para_mapa(Organization.objects.filter(id=tenant.id))
    return montar_grafo(artifacts)


def test_artefato_sem_document_text_pendura_do_navegador_via_dominio(tenant):
    artifact = _artifact(tenant)
    grafo = _grafo_para(tenant)
    assert [n["tipo"] for n in grafo["nodes"]] == ["navegador", "dominio", "artifact"]
    dominio_id = "dominio:exemplo.org"
    assert {"origem": "navegador", "destino": dominio_id, "tipo": "agrupamento"} in grafo["edges"]
    assert {"origem": dominio_id, "destino": str(artifact.id), "tipo": "captura"} in grafo["edges"]


def test_duas_capturas_do_mesmo_dominio_compartilham_o_no_dominio(tenant):
    _artifact(tenant)
    _artifact(tenant, content={"url": "https://exemplo.org/outra-pagina"})
    grafo = _grafo_para(tenant)

    dominio_nodes = [n for n in grafo["nodes"] if n["tipo"] == "dominio"]
    assert len(dominio_nodes) == 1
    assert dominio_nodes[0]["id"] == "dominio:exemplo.org"


def test_favicon_do_artefato_vira_meta_do_no_e_do_dominio(tenant):
    favicon = "data:image/png;base64,aGVsbG8="
    artifact = _artifact(tenant, content={"favicon_data_uri": favicon})
    grafo = _grafo_para(tenant)

    no_artifact = next(n for n in grafo["nodes"] if n["tipo"] == "artifact")
    no_dominio = next(n for n in grafo["nodes"] if n["tipo"] == "dominio")
    assert no_artifact["meta"]["favicon"] == favicon
    assert no_dominio["meta"]["favicon"] == favicon


def test_dominio_usa_favicon_de_qualquer_captura_do_dominio(tenant):
    # Uma captura específica pode não ter favicon próprio (download falhou no
    # orchestrator naquele momento) mesmo com outra captura do MESMO domínio
    # tendo — o nó de domínio não pode depender de qual captura "nasceu"
    # primeiro na iteração.
    favicon = "data:image/png;base64,aGVsbG8="
    _artifact(tenant, content={"url": "https://exemplo.org/com-favicon", "favicon_data_uri": favicon})
    _artifact(tenant, content={"url": "https://exemplo.org/sem-favicon"})
    grafo = _grafo_para(tenant)

    no_dominio = next(n for n in grafo["nodes"] if n["tipo"] == "dominio")
    assert no_dominio["meta"]["favicon"] == favicon


def test_artefato_sem_favicon_proprio_usa_favicon_do_dominio(tenant):
    """A captura sem favicon próprio não fica com o nó "pelado" (dot+emoji)
    se outra captura do mesmo domínio já revelou um favicon — mesma correção
    do teste acima, aplicada ao nó do artefato em vez do nó do domínio."""
    favicon = "data:image/png;base64,aGVsbG8="
    _artifact(tenant, content={"url": "https://exemplo.org/com-favicon", "favicon_data_uri": favicon})
    sem_favicon = _artifact(tenant, content={"url": "https://exemplo.org/sem-favicon"})
    grafo = _grafo_para(tenant)

    no_artifact = next(n for n in grafo["nodes"] if n["id"] == str(sem_favicon.id))
    assert no_artifact["meta"]["favicon"] == favicon


def test_artefato_sem_mhtml_e_ignorado(tenant):
    _artifact(tenant, content={"mhtml_path": ""})
    grafo = _grafo_para(tenant)
    assert grafo["nodes"] == []


def test_document_text_sem_fragmentos(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    grafo = _grafo_para(tenant)

    tipos = {n["tipo"] for n in grafo["nodes"]}
    assert tipos == {"navegador", "dominio", "artifact", "document_text"}
    assert {"origem": "dominio:exemplo.org", "destino": str(artifact.id), "tipo": "captura"} in grafo["edges"]
    assert {"origem": str(artifact.id), "destino": str(doc_text.id), "tipo": "extracao"} in grafo["edges"]


def test_document_text_com_fragmentos_gera_no_agregado(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    DocumentFragment.objects.create(document_text=doc_text, text="a", fragment_index=0, total_fragments=2)
    DocumentFragment.objects.create(document_text=doc_text, text="b", fragment_index=1, total_fragments=2)

    grafo = _grafo_para(tenant)

    frag_nodes = [n for n in grafo["nodes"] if n["tipo"] == "fragmentos"]
    assert len(frag_nodes) == 1
    assert frag_nodes[0]["id"] == f"frag:{doc_text.id}"
    assert frag_nodes[0]["meta"]["count"] == 2
    assert {"origem": str(doc_text.id), "destino": frag_nodes[0]["id"], "tipo": "fragmentacao"} in grafo["edges"]


def test_estruturacao_llm_vira_no_com_status_normalizado(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.CONCLUIDO,
    )

    grafo = _grafo_para(tenant)

    no = next(n for n in grafo["nodes"] if n["tipo"] == "estruturacao_llm")
    assert no["id"] == str(execucao.id)
    assert no["status"] == "ok"
    assert no["meta"]["document_text_id"] == str(doc_text.id)
    assert {"origem": str(doc_text.id), "destino": str(execucao.id), "tipo": "estruturacao"} in grafo["edges"]


def test_comparacao_referenciando_estruturacao_gera_aresta_de_referencia(tenant):
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
        status=Comparacao.Status.CONCLUIDO,
    )

    grafo = _grafo_para(tenant)

    no = next(n for n in grafo["nodes"] if n["tipo"] == "comparacao")
    assert no["id"] == str(comparacao.id)
    assert no["status"] == "ok"
    assert {"origem": str(artifact.id), "destino": str(comparacao.id), "tipo": "comparacao"} in grafo["edges"]
    assert {"origem": str(comparacao.id), "destino": str(execucao.id), "tipo": "referencia"} in grafo["edges"]


def test_isolamento_de_tenant_no_grafo(tenant):
    outro_dono = User.objects.create_user(username="outro", password="x")
    outra_org = Organization.objects.create(name="Outra Org", slug="outra-org", org_type="individual", owner=outro_dono)
    _artifact(outra_org)

    grafo = _grafo_para(tenant)

    assert grafo["nodes"] == []


# ─── Proveniência: em que máquina a captura/execução aconteceu ─────────────

def _correlacao(artifact):
    from apps.events.context import correlacao_de_artefato
    return correlacao_de_artefato(artifact.id)


def _apelido_deste_processo():
    """O hostname/apelido que `emit()` grava neste processo de teste — não é
    fixo (depende de CLUSTER_LOCAL_APELIDO/CLUSTER_MACHINE_ID, ausentes em
    teste), então o teste lê o valor real em vez de supor uma string."""
    from apps.events.context import identidade_execucao
    return identidade_execucao()[0]


def test_maquina_de_captura_vira_no_com_aresta_pro_artefato(tenant):
    artifact = _artifact(tenant)
    emit("captura.recebida", "ok", correlation_id=_correlacao(artifact),
         subject_type="artifact", subject_id=artifact.id, tenant_id=tenant.id,
         payload={"url": artifact.content["url"]})

    grafo = _grafo_para(tenant)

    apelido = _apelido_deste_processo()
    no_maquina = next(n for n in grafo["nodes"] if n["tipo"] == "maquina")
    assert no_maquina["id"] == f"maquina:{apelido}"
    assert no_maquina["label"] == apelido
    assert {"origem": f"maquina:{apelido}", "destino": str(artifact.id), "tipo": "captura_em"} in grafo["edges"]


def test_maquina_de_extracao_vira_no_com_aresta_pro_document_text(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    emit("extracao.concluida", "ok", correlation_id=_correlacao(artifact),
         subject_type="artifact", subject_id=artifact.id, tenant_id=tenant.id)

    grafo = _grafo_para(tenant)

    apelido = _apelido_deste_processo()
    assert {"origem": f"maquina:{apelido}", "destino": str(doc_text.id), "tipo": "execucao_em"} in grafo["edges"]


def test_maquina_de_captura_e_extracao_reaproveitam_o_mesmo_no_quando_iguais(tenant):
    """Mesma máquina em dois papéis (capturou E processou) — um nó só, não dois."""
    artifact = _artifact(tenant)
    _document_text(artifact)
    correlacao = _correlacao(artifact)
    emit("captura.recebida", "ok", correlation_id=correlacao, subject_type="artifact",
         subject_id=artifact.id, tenant_id=tenant.id)
    emit("extracao.concluida", "ok", correlation_id=correlacao, subject_type="artifact",
         subject_id=artifact.id, tenant_id=tenant.id)

    grafo = _grafo_para(tenant)

    nos_maquina = [n for n in grafo["nodes"] if n["tipo"] == "maquina"]
    assert len(nos_maquina) == 1


def test_maquina_de_estruturacao_llm_vem_da_fk_nao_do_log(tenant):
    artifact = _artifact(tenant)
    doc_text = _document_text(artifact)
    maquina = Maquina.objects.create(
        apelido="antares", organizacao=tenant, dono=tenant.owner, modo="compute", token_hash="x",
    )
    execucao = EstruturacaoLLM.objects.create(
        document_text=doc_text, tenant=tenant, provider="ollama", model_name="llama3.1:8b",
        status=EstruturacaoLLM.Status.CONCLUIDO, structured_data={"a": 1}, maquina=maquina,
    )

    grafo = _grafo_para(tenant)

    assert {"origem": "maquina:antares", "destino": str(execucao.id), "tipo": "execucao_em"} in grafo["edges"]
    no_maquina = next(n for n in grafo["nodes"] if n["id"] == "maquina:antares")
    assert no_maquina["label"] == "antares"


def test_sem_evento_e_sem_maquina_fk_nao_cria_no_de_maquina(tenant):
    artifact = _artifact(tenant)
    _document_text(artifact)

    grafo = _grafo_para(tenant)

    assert not [n for n in grafo["nodes"] if n["tipo"] == "maquina"]

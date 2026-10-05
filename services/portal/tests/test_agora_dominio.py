"""Leitura de domínio para os componentes ia-* do Ultima Agora: isolamento por organização e alegações com fonte."""
import uuid

import pytest
from django.test import Client

from apps.accounts.models import Membership, Organization, User
from apps.agora.models import Workspace
from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao
from apps.artifacts.models import Artifact, ArtifactLineage

pytestmark = pytest.mark.django_db
HASH = "ni:///sha-256;" + "A" * 43


def _org(slug):
    dono = User.objects.create_user(username=f"dono-{slug}", password="x")
    org = Organization.objects.create(name=slug.upper(), slug=slug, org_type="team", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org, dono


def _cliente(usuario):
    cliente = Client()
    cliente.force_login(usuario)
    return cliente


def _artefato(org, tipo=Artifact.Type.COMPANY, nivel="restrito", **conteudo):
    return Artifact.objects.create(
        artifact_type=tipo, tenant=org, info_type=Artifact.InfoType.FACT, classification_level=nivel,
        content=conteudo or {"razao_social": "Empresa Alfa"}, sources=[{"origem": "Receita Federal", "confianca": 0.95}],
    )


@pytest.fixture
def mundo():
    org_a, dono_a = _org("a")
    org_b, dono_b = _org("b")
    return org_a, dono_a, org_b, dono_b


def test_o_usuario_so_ve_artefatos_da_propria_organizacao(mundo):
    org_a, dono_a, org_b, _ = mundo
    meu, alheio = _artefato(org_a), _artefato(org_b, razao_social="Empresa Beta")
    cliente = _cliente(dono_a)
    resultados = cliente.get("/agora/api/v1/dominio/artefatos/").json()["results"]
    assert [r["id"] for r in resultados] == [str(meu.pk)]
    assert cliente.get(f"/agora/api/v1/dominio/artefatos/{alheio.pk}/").status_code == 404       # como se não existisse
    assert cliente.get(f"/agora/api/v1/dominio/artefatos/{alheio.pk}/relacoes/").status_code == 404


def test_busca_por_texto_no_conteudo_e_limite(mundo):
    org_a, dono_a, *_ = mundo
    for nome in ("Empresa Alfa", "Empresa Beta", "Pessoa Gama"):
        _artefato(org_a, razao_social=nome)
    cliente = _cliente(dono_a)
    assert {r["label"] for r in cliente.get("/agora/api/v1/dominio/artefatos/?q=empresa").json()["results"]} == {"Empresa Alfa", "Empresa Beta"}
    assert len(cliente.get("/agora/api/v1/dominio/artefatos/?limite=1").json()["results"]) == 1
    assert len(cliente.get("/agora/api/v1/dominio/artefatos/?limite=abc").json()["results"]) == 3     # valor ruim não quebra


def test_o_dto_nao_vaza_o_conteudo_completo_na_listagem(mundo):
    org_a, dono_a, *_ = mundo
    _artefato(org_a, cnpj="11222333000181", razao_social="Empresa Alfa", segredo_interno="X")
    item = _cliente(dono_a).get("/agora/api/v1/dominio/artefatos/").json()["results"][0]
    assert set(item) == {"id", "kind", "label", "classification", "info_type"}
    assert item["label"] == "Empresa Alfa"


def test_detalhe_traz_alegacoes_com_produtor_e_confianca_e_fontes(mundo):
    org_a, dono_a, *_ = mundo
    artefato = _artefato(org_a, tipo=Artifact.Type.DOCUMENT, url="https://jornal.example/a", mhtml_path="x")
    artefato.blob_hash = HASH
    artefato.save()
    registrar_alegacao(
        artefato=artefato, sujeito_ref="cnpj:11222333000181", predicado="schema:name", objeto_literal="Empresa X",
        autor_ref="dominio:jornal.example", produtor="extruct", produtor_versao="1", confianca=0.7,
        evidencias=[EvidenciaEntrada(blob_hash=HASH, localizador_tipo="extruct.json-ld", localizador={"item": "json-ld[0]"}, trecho="TRECHO SECRETO")],
    )
    dto = _cliente(dono_a).get(f"/agora/api/v1/dominio/artefatos/{artefato.pk}/").json()
    (alegacao,) = dto["claims"]
    assert (alegacao["producer"], alegacao["confidence"], alegacao["object"], alegacao["state"]) == ("extruct", 0.7, "Empresa X", "ativa")
    assert "TRECHO SECRETO" not in str(dto), "o trecho da evidência é conteúdo classificado e não vai no DTO"
    assert dto["sources"] == [{"origem": "Receita Federal", "confianca": 0.95}]


def test_relacoes_usa_linhagem_e_vinculos_so_dentro_da_organizacao(mundo):
    org_a, dono_a, org_b, _ = mundo
    empresa, socio, alheio = _artefato(org_a), _artefato(org_a, tipo=Artifact.Type.PERSON, nome="Pessoa A"), _artefato(org_b, razao_social="De fora")
    empresa.content = {"razao_social": "Empresa Alfa", "vinculos": [str(socio.pk), str(alheio.pk), "nao-e-uuid"]}
    empresa.save()
    derivado = _artefato(org_a, tipo=Artifact.Type.EVENT, titulo="Evento derivado")
    ArtifactLineage.objects.create(parent=empresa, child=derivado, transformation="extracao", processor="teste")
    grafo = _cliente(dono_a).get(f"/agora/api/v1/dominio/artefatos/{empresa.pk}/relacoes/").json()
    assert {n["label"] for n in grafo["nodes"]} == {"Empresa Alfa", "Pessoa A", "Evento derivado"}, "o artefato de outra organização não aparece"
    assert {(e["label"]) for e in grafo["edges"]} == {"extracao", "vínculo"}
    ids = {n["id"] for n in grafo["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in grafo["edges"])
    assert all(n["classification"] in {"publico", "interno", "restrito", "confidencial"} for n in grafo["nodes"]), "o cliente aplica a política offline por nó"



def test_sinaliza_o_que_e_mais_restrito_que_o_workspace_sem_copiar_nada(mundo):
    org_a, dono_a, *_ = mundo
    secreto, aberto = _artefato(org_a, nivel="confidencial", razao_social="Secreta"), _artefato(org_a, nivel="interno", razao_social="Aberta")
    workspace = Workspace.objects.create(title="W", organization=org_a, owner=dono_a, classification="interno")
    cliente = _cliente(dono_a)
    por_rotulo = {r["label"]: r for r in cliente.get(f"/agora/api/v1/dominio/artefatos/?workspace={workspace.pk}").json()["results"]}
    assert por_rotulo["Secreta"]["above_workspace"] is True and por_rotulo["Aberta"]["above_workspace"] is False
    assert "above_workspace" not in cliente.get("/agora/api/v1/dominio/artefatos/").json()["results"][0]


def test_workspace_de_outra_organizacao_no_parametro_e_ignorado(mundo):
    org_a, dono_a, org_b, dono_b = mundo
    _artefato(org_a)
    alheio = Workspace.objects.create(title="W", organization=org_b, owner=dono_b, classification="publico")
    item = _cliente(dono_a).get(f"/agora/api/v1/dominio/artefatos/?workspace={alheio.pk}").json()["results"][0]
    assert "above_workspace" not in item, "não deve nem confirmar que o workspace existe"
    assert _cliente(dono_a).get(f"/agora/api/v1/dominio/artefatos/?workspace={uuid.uuid4()}").status_code == 200


def test_exige_login(mundo):
    assert Client().get("/agora/api/v1/dominio/artefatos/").status_code == 302


def test_filtro_por_tipo_aceita_varios_e_tipo_desconhecido_nao_devolve_nada(mundo):
    org_a, dono_a, *_ = mundo
    _artefato(org_a, tipo=Artifact.Type.DOCUMENT, titulo="Notícia sobre a Empresa Alfa")
    _artefato(org_a, tipo=Artifact.Type.PERSON, nome="Pessoa A")
    cliente = _cliente(dono_a)
    url = "/agora/api/v1/dominio/artefatos/"
    assert [r["kind"] for r in cliente.get(f"{url}?tipo=documento").json()["results"]] == ["documento"]
    assert {r["kind"] for r in cliente.get(f"{url}?tipo=documento,pessoa").json()["results"]} == {"documento", "pessoa"}
    assert cliente.get(f"{url}?tipo=inexistente").json()["results"] == []
    assert len(cliente.get(f"{url}?tipo=").json()["results"]) == 2, "tipo vazio = sem filtro"
    assert [r["label"] for r in cliente.get(f"{url}?tipo=documento&q=alfa").json()["results"]] == ["Notícia sobre a Empresa Alfa"]

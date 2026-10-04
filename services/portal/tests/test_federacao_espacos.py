"""`Space` e o espaço padrão implícito — F1 (etapa 1) da ADR 010."""
import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact
from apps.federacao import espacos
from apps.federacao.espacos import (
    EspacoPadrao, arquivar, artefatos_do_espaco, criar_espaco, desarquivar, espaco_padrao,
    espacos_do_artefato, incluir_artefato, remover_artefato, resolver_urn,
)
from apps.federacao.ids import eh_urn_valida
from apps.federacao.models import EspacoArtefato, Space

pytestmark = pytest.mark.django_db


def _org(slug):
    dono = User.objects.create_user(username=f"dono-{slug}", password="x")
    return Organization.objects.create(name=slug.upper(), slug=slug, org_type="individual", owner=dono)


@pytest.fixture
def org_a():
    return _org("a")


@pytest.fixture
def org_b():
    return _org("b")


def _artefato(org, content=None, **kw):
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        content=content or {"url": "https://exemplo.org/", "mhtml_path": "x"}, **kw,
    )


# ── criar ───────────────────────────────────────────────────────────────────

def test_criar_espaco_normaliza_o_nome_e_tem_urn(org_a):
    e = criar_espaco(organizacao=org_a, nome="  Pesquisa   2026 ", descricao="d")
    assert e.nome == "Pesquisa 2026" and e.organizacao_id == org_a.id and not e.arquivado
    assert e.urn == f"urn:uuid:{e.id}" and eh_urn_valida(e.urn)


@pytest.mark.parametrize("nome", ["", "   ", None, "x" * 121])
def test_nome_invalido(org_a, nome):
    with pytest.raises(ValueError):
        criar_espaco(organizacao=org_a, nome=nome)
    assert Space.objects.count() == 0


def test_nome_unico_por_organizacao_mas_livre_entre_organizacoes(org_a, org_b):
    criar_espaco(organizacao=org_a, nome="Familia")
    with pytest.raises(ValueError):
        criar_espaco(organizacao=org_a, nome="Familia")
    with pytest.raises(ValueError):
        criar_espaco(organizacao=org_a, nome=" Familia ")      # mesmo nome depois de normalizar
    criar_espaco(organizacao=org_b, nome="Familia")
    assert Space.objects.count() == 2


def test_criado_por_e_guardado(org_a):
    e = criar_espaco(organizacao=org_a, nome="X", criado_por=org_a.owner)
    assert e.criado_por_id == org_a.owner_id


# ── incluir e remover ───────────────────────────────────────────────────────

def test_incluir_artefato_da_mesma_organizacao_e_idempotente(org_a):
    e = criar_espaco(organizacao=org_a, nome="X")
    a = _artefato(org_a)
    item, criado = incluir_artefato(e, a, usuario=org_a.owner)
    outro, criado2 = incluir_artefato(e, a)
    assert criado and not criado2 and item.pk == outro.pk
    assert item.incluido_por_id == org_a.owner_id and EspacoArtefato.objects.count() == 1


def test_artefato_de_outra_organizacao_e_recusado(org_a, org_b):
    e = criar_espaco(organizacao=org_a, nome="X")
    with pytest.raises(ValueError):
        incluir_artefato(e, _artefato(org_b))
    assert EspacoArtefato.objects.count() == 0


def test_objeto_importado_mantem_o_tenant_e_ganha_a_associacao(org_a, org_b):
    e = criar_espaco(organizacao=org_a, nome="Recebidos")
    de_b = _artefato(org_b)
    incluir_artefato(e, de_b, importado=True)
    de_b.refresh_from_db()
    assert de_b.tenant_id == org_b.id                       # não mudou de organização
    assert list(artefatos_do_espaco(e)) == [de_b]


def test_espaco_arquivado_nao_recebe_e_desarquivar_reabre(org_a):
    e = criar_espaco(organizacao=org_a, nome="X")
    a = _artefato(org_a)
    arquivar(e)
    arquivar(e)                                              # idempotente
    with pytest.raises(ValueError):
        incluir_artefato(e, a)
    desarquivar(e)
    assert incluir_artefato(e, a)[1] is True


def test_arquivar_nao_apaga_o_que_ja_estava(org_a):
    e = criar_espaco(organizacao=org_a, nome="X")
    a = _artefato(org_a)
    incluir_artefato(e, a)
    arquivar(e)
    assert list(artefatos_do_espaco(e)) == [a]


def test_remover_tira_do_espaco_e_nao_toca_no_artefato(org_a):
    e = criar_espaco(organizacao=org_a, nome="X")
    a = _artefato(org_a)
    incluir_artefato(e, a)
    assert remover_artefato(e, a) is True
    assert remover_artefato(e, a) is False
    assert Artifact.objects.filter(pk=a.pk).exists() and list(artefatos_do_espaco(e)) == []


def test_apagar_o_artefato_tira_o_vinculo_mas_nao_o_espaco(org_a):
    e = criar_espaco(organizacao=org_a, nome="X")
    a = _artefato(org_a)
    incluir_artefato(e, a)
    a.delete()
    assert Space.objects.filter(pk=e.pk).exists() and EspacoArtefato.objects.count() == 0


def test_o_espaco_padrao_nao_aceita_inclusao_nem_remocao(org_a):
    with pytest.raises(ValueError):
        incluir_artefato(espaco_padrao(org_a), _artefato(org_a))
    with pytest.raises(ValueError):
        remover_artefato(espaco_padrao(org_a), _artefato(org_a))


# ── vários espaços ──────────────────────────────────────────────────────────

def test_um_artefato_pode_estar_em_varios_espacos(org_a):
    a = _artefato(org_a)
    e1 = criar_espaco(organizacao=org_a, nome="Pesquisa")
    e2 = criar_espaco(organizacao=org_a, nome="Familia")
    incluir_artefato(e1, a)
    incluir_artefato(e2, a)
    assert list(artefatos_do_espaco(e1)) == [a] and list(artefatos_do_espaco(e2)) == [a]
    assert [e.nome for e in espacos_do_artefato(a)] == ["Padrão", "Familia", "Pesquisa"]


# ── espaço padrão implícito ─────────────────────────────────────────────────

def test_espaco_padrao_nao_tem_linha_e_tem_id_estavel_por_organizacao(org_a, org_b):
    p1, p2 = espaco_padrao(org_a), espaco_padrao(org_a)
    assert p1.id == p2.id and p1.urn == p2.urn and eh_urn_valida(p1.urn)
    assert espaco_padrao(org_b).id != p1.id
    assert Space.objects.count() == 0
    assert isinstance(p1, EspacoPadrao) and p1.nome == "Padrão" and not p1.arquivado


def test_id_do_espaco_padrao_e_derivado_da_organizacao_e_nao_muda():
    """Fixado: mudar a derivação mudaria o ID do espaço padrão de todas as organizações."""
    org = Organization(id=uuid.UUID("3f2504e0-4f89-41d3-9a0c-0305e82c3301"))
    assert espaco_padrao(org).id == uuid.uuid5(espacos.NAMESPACE_ESPACO_PADRAO, str(org.id))
    assert espaco_padrao(org).urn == "urn:uuid:" + str(uuid.uuid5(espacos.NAMESPACE_ESPACO_PADRAO, str(org.id)))


def test_espaco_padrao_contem_todos_os_artefatos_da_organizacao_sem_linhas(org_a, org_b):
    a1, a2 = _artefato(org_a), _artefato(org_a)
    de_b = _artefato(org_b)
    assert set(artefatos_do_espaco(espaco_padrao(org_a))) == {a1, a2}
    assert set(artefatos_do_espaco(espaco_padrao(org_b))) == {de_b}
    assert EspacoArtefato.objects.count() == 0


def test_artefato_novo_ja_esta_no_espaco_padrao(org_a):
    a = _artefato(org_a)
    [padrao] = espacos_do_artefato(a)
    assert isinstance(padrao, EspacoPadrao) and padrao.organizacao_id == org_a.id


# ── resolver urn ────────────────────────────────────────────────────────────

def test_resolver_urn_de_espaco_explicito_e_padrao(org_a, org_b):
    e = criar_espaco(organizacao=org_a, nome="X")
    assert resolver_urn(e.urn) == e
    padrao = resolver_urn(espaco_padrao(org_b).urn)
    assert isinstance(padrao, EspacoPadrao) and padrao.organizacao_id == org_b.id


@pytest.mark.parametrize("urn", ["", "lixo", None, f"urn:uuid:{uuid.uuid4()}"])
def test_resolver_urn_desconhecida_devolve_none(org_a, urn):
    assert resolver_urn(urn) is None


# ── o espaço não concede acesso ─────────────────────────────────────────────

def test_estar_num_espaco_nao_da_acesso_a_ninguem(org_a, org_b, client):
    """O acesso segue por organização: o dono de B não vê o artefato de A, mesmo
    que ele esteja num espaço (importado) de B. Contraste: o dono de A vê."""
    de_a = _artefato(org_a, content={"url": "https://exemplo.org/", "mhtml_path": "x",
                                     "favicon_data_uri": "data:image/png;base64,aGVsbG8="})
    incluir_artefato(criar_espaco(organizacao=org_b, nome="Recebidos"), de_a, importado=True)
    Membership.objects.create(user=org_a.owner, organization=org_a, role="owner")
    Membership.objects.create(user=org_b.owner, organization=org_b, role="owner")
    url = f"/artifacts/{de_a.id}/favicon/"

    client.force_login(org_a.owner)
    assert client.get(url).status_code == 200
    client.force_login(org_b.owner)
    assert client.get(url).status_code == 404


# ── admin ───────────────────────────────────────────────────────────────────

def test_clean_do_admin_recusa_artefato_de_outra_organizacao(org_a, org_b):
    e = criar_espaco(organizacao=org_a, nome="X")
    with pytest.raises(ValidationError):
        EspacoArtefato(espaco=e, artefato=_artefato(org_b)).full_clean()
    EspacoArtefato(espaco=e, artefato=_artefato(org_a)).full_clean()   # mesma organização: ok

"""API de workspaces do Ultima Agora: sessão, papéis, tokens para o agora-sync, eventos, reclassificação."""
import json
import time
from unittest import mock

import jwt
import pytest
from django.test import Client

from apps.accounts.models import Membership, Organization, User
from apps.agora import sync
from apps.agora.models import Workspace, WorkspaceMember
from apps.events.models import PipelineEvent

pytestmark = pytest.mark.django_db
SEGREDO = "um-segredo-de-teste-com-mais-de-trinta-e-dois-caracteres"


@pytest.fixture(autouse=True)
def _segredo(settings):
    settings.AGORA_SYNC_SECRET = SEGREDO
    settings.AGORA_SYNC_ADMIN_URL = ""
    settings.AGORA_SYNC_ADMIN_TOKEN = ""


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    organizacao = Organization.objects.create(name="Org", slug="org", org_type="team", owner=dono)
    Membership.objects.create(user=dono, organization=organizacao, role=Membership.Role.OWNER)
    return organizacao


def _cliente(org, nome, papel_org=Membership.Role.MEMBER):
    pessoa = User.objects.filter(username=nome).first() or User.objects.create_user(username=nome, password="x")
    if papel_org and not Membership.objects.filter(user=pessoa, organization=org).exists():
        Membership.objects.create(user=pessoa, organization=org, role=papel_org)
    cliente = Client()
    cliente.force_login(pessoa)
    return cliente, pessoa


def _post(cliente, url, corpo=None):
    return cliente.post(url, data=json.dumps(corpo or {}), content_type="application/json")


def _patch(cliente, url, corpo):
    return cliente.patch(url, data=json.dumps(corpo), content_type="application/json")


@pytest.fixture
def dono_e_workspace(org):
    cliente, dono = _cliente(org, "dono", Membership.Role.OWNER)
    resposta = _post(cliente, "/agora/api/v1/workspaces/", {"title": "Investigação Alfa"})
    assert resposta.status_code == 201
    return cliente, dono, Workspace.objects.get(pk=resposta.json()["id"])


def test_a_pagina_exige_login_e_entrega_o_cookie_csrf(org):
    assert Client().get("/agora/").status_code == 302
    cliente, _ = _cliente(org, "dono", Membership.Role.OWNER)
    resposta = cliente.get("/agora/")
    assert resposta.status_code == 200 and "csrftoken" in resposta.cookies
    assert b'id="agora-config"' in resposta.content


def test_a_api_exige_login(org):
    assert Client().get("/agora/api/v1/workspaces/").status_code == 302


def test_criar_define_dono_organizacao_e_classificacao_restrita_por_padrao(dono_e_workspace, org):
    _, dono, workspace = dono_e_workspace
    assert (workspace.owner, workspace.organization, workspace.classification) == (dono, org, "restrito")


def test_convidado_nao_cria_workspace(org):
    cliente, _ = _cliente(org, "conv", Membership.Role.GUEST)
    assert _post(cliente, "/agora/api/v1/workspaces/", {"title": "x"}).status_code == 403


def test_classificacao_invalida_e_recusada(org):
    cliente, _ = _cliente(org, "dono", Membership.Role.OWNER)
    assert _post(cliente, "/agora/api/v1/workspaces/", {"title": "x", "classification": "secretissimo"}).status_code == 400


def test_a_lista_traz_so_o_que_o_usuario_pode_ver_com_o_papel_dele(dono_e_workspace, org):
    cliente, _ = _cliente(org, "membro", Membership.Role.MEMBER)
    itens = cliente.get("/agora/api/v1/workspaces/").json()["workspaces"]
    assert [(i["title"], i["role"]) for i in itens] == [("Investigação Alfa", "participant")]
    estranho, _ = _cliente(org, "estranho", None)
    assert estranho.get("/agora/api/v1/workspaces/").json()["workspaces"] == []


def test_workspace_de_outra_organizacao_e_404_e_nao_403(dono_e_workspace, org):
    outra = Organization.objects.create(name="Outra", slug="outra", org_type="team", owner=User.objects.get(username="dono"))
    cliente, pessoa = _cliente(outra, "de-fora", Membership.Role.OWNER)
    ws = dono_e_workspace[2]
    assert cliente.get(f"/agora/api/v1/workspaces/{ws.pk}/").status_code == 404
    assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/token/").status_code == 404


def test_o_token_carrega_o_papel_e_so_o_agora_sync_o_valida(dono_e_workspace, org):
    ws = dono_e_workspace[2]
    cliente, pessoa = _cliente(org, "membro", Membership.Role.MEMBER)
    dados = _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/token/").json()
    claims = jwt.decode(dados["token"], SEGREDO, algorithms=["HS256"])
    assert (claims["role"], claims["workspace"], claims["sub"]) == ("participant", str(ws.pk), str(pessoa.pk))
    assert 0 < claims["exp"] - time.time() <= sync.TOKEN_TTL_S
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(dados["token"], "outro-segredo-completamente-diferente-123456", algorithms=["HS256"])


def test_sem_segredo_nao_ha_token(dono_e_workspace, settings):
    settings.AGORA_SYNC_SECRET = ""
    cliente, _, ws = dono_e_workspace
    assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/token/").status_code == 503


def test_so_o_dono_altera_titulo_e_classificacao(dono_e_workspace, org):
    cliente, _, ws = dono_e_workspace
    editor, _ = _cliente(org, "adm", Membership.Role.ADMIN)
    assert _patch(editor, f"/agora/api/v1/workspaces/{ws.pk}/", {"title": "x"}).status_code == 403
    assert _patch(cliente, f"/agora/api/v1/workspaces/{ws.pk}/", {"title": "Novo título", "classification": "confidencial"}).status_code == 200
    ws.refresh_from_db()
    assert (ws.title, ws.classification) == ("Novo título", "confidencial")


def test_reclassificar_gera_evento_de_seguranca_sem_conteudo(dono_e_workspace):
    cliente, _, ws = dono_e_workspace
    _patch(cliente, f"/agora/api/v1/workspaces/{ws.pk}/", {"classification": "publico"})
    evento = PipelineEvent.objects.filter(stage="agora.workspace.reclassificado").get()
    assert evento.payload["de"] == "restrito" and evento.payload["para"] == "publico"
    assert "title" not in evento.payload and "texto" not in evento.payload


def test_criar_arquivar_e_reclassificar_deixam_trilha(dono_e_workspace):
    cliente, _, ws = dono_e_workspace
    _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/archive/")
    estagios = set(PipelineEvent.objects.values_list("stage", flat=True))
    assert {"agora.workspace.criado", "agora.workspace.arquivado"} <= estagios
    assert cliente.get("/agora/api/v1/workspaces/").json()["workspaces"] == []


def test_membros_so_o_dono_gere_e_o_papel_fica_no_teto(dono_e_workspace, org):
    cliente, _, ws = dono_e_workspace
    _cliente(org, "convidada", Membership.Role.GUEST)
    _cliente(org, "membro", Membership.Role.MEMBER)
    resposta = _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "convidada", "role": "editor"})
    assert resposta.json() == {"user": str(User.objects.get(username="convidada").pk), "role": "viewer", "requested": "editor"}
    outro, _ = _cliente(org, "membro")
    assert _post(outro, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "membro", "role": "owner"}).status_code == 403
    listagem = cliente.get(f"/agora/api/v1/workspaces/{ws.pk}/members/").json()["members"]
    assert {m["username"]: m["role"] for m in listagem}["convidada"] == "viewer"


def test_so_membro_da_organizacao_recebe_papel_e_o_dono_nao_muda(dono_e_workspace, org):
    cliente, _, ws = dono_e_workspace
    User.objects.create_user(username="fora", password="x")
    assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "fora", "role": "viewer"}).status_code == 400
    assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "dono", "role": "viewer"}).status_code == 400
    assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "dono", "role": "chefe"}).status_code == 400


def test_mudar_o_papel_avisa_o_agora_sync_para_valer_na_hora(dono_e_workspace, org, settings):
    settings.AGORA_SYNC_ADMIN_URL, settings.AGORA_SYNC_ADMIN_TOKEN = "http://sync:8787", "token-admin"
    cliente, _, ws = dono_e_workspace
    _, membro = _cliente(org, "membro", Membership.Role.MEMBER)
    with mock.patch("apps.agora.sync.requests.post") as post:
        post.return_value.ok = True
        _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "membro", "role": "viewer"})
    (url,), argumentos = post.call_args
    assert url == "http://sync:8787/admin/role"
    assert argumentos["json"] == {"workspace": str(ws.pk), "user": str(membro.pk), "role": "viewer"}
    assert argumentos["headers"]["Authorization"] == "Bearer token-admin"


def test_o_sync_fora_do_ar_nao_derruba_a_api(dono_e_workspace, org, settings):
    import requests
    settings.AGORA_SYNC_ADMIN_URL, settings.AGORA_SYNC_ADMIN_TOKEN = "http://sync:8787", "t"
    cliente, _, ws = dono_e_workspace
    _cliente(org, "membro", Membership.Role.MEMBER)
    with mock.patch("apps.agora.sync.requests.post", side_effect=requests.ConnectionError("fora")):
        assert _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "membro", "role": "viewer"}).status_code == 200
    assert WorkspaceMember.objects.filter(workspace=ws, role="viewer").exists()


def test_remover_o_papel_explicito_volta_ao_padrao(dono_e_workspace, org):
    cliente, _, ws = dono_e_workspace
    _cliente(org, "membro", Membership.Role.MEMBER)
    _post(cliente, f"/agora/api/v1/workspaces/{ws.pk}/members/", {"username": "membro", "role": "viewer"})
    resposta = cliente.delete(f"/agora/api/v1/workspaces/{ws.pk}/members/", data=json.dumps({"username": "membro"}), content_type="application/json")
    assert resposta.json() == {"removed": True}
    assert not WorkspaceMember.objects.filter(workspace=ws).exists()


# ── offline-first ───────────────────────────────────────────────────────────

def test_o_service_worker_e_servido_de_dentro_de_agora_sem_login_e_sem_cache_http(org):
    resposta = Client().get("/agora/sw.js")
    assert resposta.status_code == 200
    assert resposta["Content-Type"].startswith("text/javascript")
    assert resposta["Service-Worker-Allowed"] == "/agora/" and resposta["Cache-Control"] == "no-cache"
    assert b"agora-shell-" in b"".join(resposta.streaming_content if resposta.streaming else [resposta.content])


def test_a_pagina_informa_o_teto_de_cache_offline_da_instancia(org, settings):
    settings.AGORA_OFFLINE_CACHE_NIVEL = "interno"
    cliente, _ = _cliente(org, "dono", Membership.Role.OWNER)
    configuracao = json.loads(cliente.get("/agora/").content.decode().split('id="agora-config" type="application/json">')[1].split("</script>")[0])
    assert configuracao["offline_cache_max"] == "interno"


def test_criar_com_id_do_cliente_e_idempotente_para_o_dono(org):
    cliente, dono = _cliente(org, "dono", Membership.Role.OWNER)
    identificador = "6f0f7e7c-1d0b-4a35-9a3a-0b5e2f4c9a11"
    primeira = _post(cliente, "/agora/api/v1/workspaces/", {"id": identificador, "title": "Criado offline"})
    segunda = _post(cliente, "/agora/api/v1/workspaces/", {"id": identificador, "title": "Criado offline"})
    assert (primeira.status_code, segunda.status_code) == (201, 200)
    assert primeira.json()["id"] == segunda.json()["id"] == identificador
    assert Workspace.objects.filter(pk=identificador).count() == 1


def test_id_do_cliente_ja_usado_por_outra_pessoa_e_409_e_nao_revela_o_dono(org):
    dono_cliente, _ = _cliente(org, "dono", Membership.Role.OWNER)
    identificador = _post(dono_cliente, "/agora/api/v1/workspaces/", {"title": "Do dono"}).json()["id"]
    outro, _ = _cliente(org, "membro", Membership.Role.MEMBER)
    resposta = _post(outro, "/agora/api/v1/workspaces/", {"id": identificador, "title": "Tentativa"})
    assert resposta.status_code == 409 and "dono" not in resposta.content.decode().lower()
    assert Workspace.objects.get(pk=identificador).title == "Do dono"


def test_id_invalido_e_400(org):
    cliente, _ = _cliente(org, "dono", Membership.Role.OWNER)
    assert _post(cliente, "/agora/api/v1/workspaces/", {"id": "../../etc", "title": "x"}).status_code == 400


def test_nivel_de_cache_offline_invalido_impede_a_subida():
    import importlib
    import os
    from unittest import mock

    from config.settings import base
    with mock.patch.dict(os.environ, {"AGORA_OFFLINE_CACHE_NIVEL": "tudo"}):
        with pytest.raises(RuntimeError, match="AGORA_OFFLINE_CACHE_NIVEL"):
            importlib.reload(base)
    importlib.reload(base)

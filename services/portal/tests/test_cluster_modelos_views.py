"""Marco E: a tela do cluster controla os modelos do Ollama (inventário, instalar, remover, cancelar)."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.cluster import operacoes, pares
from apps.cluster.catalogo_modelos import CATALOGO
from apps.cluster.models import Maquina, MaquinaModeloOllama, MaquinaStatus, OperacaoModeloOllama as Op
from apps.cluster.views_modelos import avaliar_acoes, estado_do_ollama
from apps.federacao.canal import ParInacessivel, RespostaPar
from apps.federacao.chaves import garantir_chave_ativa
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.OLLAMA_HOST = "http://ollama:11434"
    settings.OLLAMA_RESERVA_DISCO_GB = 10


@pytest.fixture(autouse=True)
def sem_broker():
    with mock.patch("apps.cluster.tasks.executar_operacao_modelo.apply_async", return_value=SimpleNamespace(id="t-1")) as m:
        yield m


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def admin(org):
    return org.owner


@pytest.fixture
def membro(org):
    u = User.objects.create_user(username="membro", password="x")
    Membership.objects.create(user=u, organization=org, role="member")
    return u


@pytest.fixture
def local(org, monkeypatch):
    garantir_chave_ativa()
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    m = pares.garantir_maquina_local(org)
    MaquinaStatus.objects.create(maquina=m, ollama_disponivel=True, ollama_versao="0.35.1", ultimo_heartbeat_em=timezone.now())
    return m


def _par(org, nome="notebook-b", online=True, **kw):
    base = dict(organizacao=org, dono=org.owner, apelido=nome, did=ChaveOutraInstancia().did, tipo="proprio", estado="confirmado",
                endpoint_controle="http://10.0.0.9:8000", capacidades_json={"ollama_configurado": True})
    base.update(kw)
    par = Maquina.objects.create(**base)
    MaquinaStatus.objects.create(maquina=par, ollama_disponivel=True,
                                 ultimo_heartbeat_em=timezone.now() if online else timezone.now() - timedelta(minutes=10))
    return par


def _logar(client, usuario):
    client.force_login(usuario)
    return client


def _json(resp):
    return json.loads(resp.content)


def _url(nome, **kw):
    from django.urls import reverse

    return reverse(f"cluster:{nome}", kwargs=kw)


# ═══ a tela ═════════════════════════════════════════════════════════════════

def test_painel_mostra_a_secao_de_modelos_o_catalogo_e_o_offline(client, admin, local, org):
    off = _par(org, "notebook-off", online=False)
    html = _logar(client, admin).get("/cluster/").content.decode()
    assert html.count('class="ollama"') == 2
    assert _url("modelos_inventario", maquina_id=local.id) in html and _url("modelos_inventario", maquina_id=off.id) in html
    assert 'id="catalogo-modelos"' in html and all(m["nome"] in html for m in CATALOGO)
    assert "selo-offline" in html and "Offline" in html
    assert "podem estar desatualizadas" in html                    # honesto sobre o catálogo curado
    assert "innerHTML" not in html                                  # nomes de modelo nunca entram como HTML
    assert "http://ollama:11434" not in html and off.endpoint_controle not in html


def test_painel_marca_a_propria_instancia_e_o_tipo_do_par(client, admin, local, org):
    _par(org, "par-terceiro", tipo="terceiro", estado="pendente")
    html = _logar(client, admin).get("/cluster/").content.decode()
    assert "esta instância" in html and "Terceiro" in html and "Pendente de conferência" in html


def test_painel_exige_login(client, local):
    assert client.get("/cluster/").status_code in (301, 302)


# ═══ inventário (JSON) ══════════════════════════════════════════════════════

def test_inventario_traz_modelos_operacoes_e_estado(client, admin, local):
    MaquinaModeloOllama.objects.create(maquina=local, nome_modelo="qwen3.5:9b", tamanho_bytes=5_500_000_000, familia="qwen",
                                       parametros="9B", quantizacao="Q4", carregado=True, tokens_por_segundo_medio=31.0)
    Op.objects.create(maquina=local, tipo="pull", modelo="llama3.1:8b", status="executando", bytes_total=100, bytes_concluidos=40)
    resp = _logar(client, admin).get(_url("modelos_inventario", maquina_id=local.id))
    d = _json(resp)
    assert resp.status_code == 200 and resp["Content-Type"] == "application/json"
    assert d["maquina"]["local"] and d["maquina"]["online"] and d["maquina"]["ollama"] == "ok" and d["maquina"]["acoes_habilitadas"]
    assert d["maquina"]["ollama_versao"] == "0.35.1"
    assert d["modelos"][0]["nome"] == "qwen3.5:9b" and d["modelos"][0]["carregado"] and d["modelos"][0]["tokens_por_segundo"] == 31.0
    assert any("carregado" in a for a in d["modelos"][0]["avisos_remocao"])
    assert d["operacoes"][0]["modelo"] == "llama3.1:8b" and d["operacoes"][0]["percentual"] == 40
    assert "celery_task_id" not in resp.content.decode() and "ator" not in d["operacoes"][0]


def test_operacoes_ativas_vem_primeiro_e_as_recentes_sao_limitadas(client, admin, local):
    for n in range(20):
        Op.objects.create(maquina=local, tipo="pull", modelo=f"antigo{n}:1", status="concluida")
    ativa = Op.objects.create(maquina=local, tipo="pull", modelo="ativa:1", status="pendente")
    ops = _json(_logar(client, admin).get(_url("modelos_inventario", maquina_id=local.id)))["operacoes"]
    assert ops[0]["id"] == str(ativa.id) and len(ops) == 1 + 12


def test_membro_ve_mas_nao_pode_agir_e_sabe_por_que(client, membro, local):
    m = _json(_logar(client, membro).get(_url("modelos_inventario", maquina_id=local.id)))["maquina"]
    assert not m["acoes_habilitadas"] and "dono ou administrador" in m["motivo_desabilitado"]


def test_par_offline_tem_acoes_desabilitadas_com_o_motivo(client, admin, org, local):
    off = _par(org, "off", online=False)
    m = _json(_logar(client, admin).get(_url("modelos_inventario", maquina_id=off.id)))["maquina"]
    assert not m["online"] and not m["acoes_habilitadas"] and m["motivo_desabilitado"] == "Máquina offline."


@pytest.mark.parametrize("mudanca,motivo", [
    (dict(tipo="terceiro"), "pares próprios e confirmados"), (dict(estado="pendente"), "pares próprios e confirmados"),
    (dict(estado="revogado"), "pares próprios e confirmados"),
])
def test_so_se_controla_par_proprio_e_confirmado(client, admin, org, local, mudanca, motivo):
    par = _par(org, **mudanca)
    m = _json(_logar(client, admin).get(_url("modelos_inventario", maquina_id=par.id)))["maquina"]
    assert not m["acoes_habilitadas"] and motivo in m["motivo_desabilitado"]


def test_ollama_indisponivel_ou_ausente_desabilita_com_o_motivo(admin, org, local, settings):
    MaquinaStatus.objects.filter(maquina=local).update(ollama_disponivel=False)
    local = Maquina.objects.get(pk=local.pk)
    assert estado_do_ollama(local) == "indisponivel"
    assert avaliar_acoes(local, admin) == (False, "O Ollama desta máquina não está respondendo.")
    settings.OLLAMA_HOST = ""
    MaquinaStatus.objects.filter(maquina=local).update(ollama_disponivel=None)
    local = Maquina.objects.get(pk=local.pk)
    assert estado_do_ollama(local) == "nao_configurado"
    assert avaliar_acoes(local, admin) == (False, "O Ollama não está configurado nesta máquina.")


def test_par_que_disse_nao_ter_ollama(admin, org):
    par = _par(org, capacidades_json={"ollama_configurado": False})
    MaquinaStatus.objects.filter(maquina=par).update(ollama_disponivel=None)
    par = Maquina.objects.get(pk=par.pk)
    assert estado_do_ollama(par) == "nao_configurado" and not avaliar_acoes(par, admin)[0]


def test_estado_desconhecido_ainda_deixa_tentar(admin, org):
    par = _par(org)
    MaquinaStatus.objects.filter(maquina=par).update(ollama_disponivel=None)
    par = Maquina.objects.get(pk=par.pk)
    assert estado_do_ollama(par) == "desconhecido" and avaliar_acoes(par, admin) == (True, "")


# ═══ isolamento por organização ═════════════════════════════════════════════

@pytest.fixture
def estranho():
    u = User.objects.create_user(username="estranho", password="x")
    o = Organization.objects.create(name="X", slug="x", org_type="individual", owner=u)
    Membership.objects.create(user=u, organization=o, role="owner")
    return u


def test_maquina_e_operacao_de_outra_organizacao_sao_404(client, estranho, local):
    op = Op.objects.create(maquina=local, tipo="pull", modelo="a:1")
    _logar(client, estranho)
    assert client.get(_url("modelos_inventario", maquina_id=local.id)).status_code == 404
    assert client.post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "a:1"}).status_code == 404
    assert client.post(_url("modelos_remover", maquina_id=local.id), {"modelo": "a:1"}).status_code == 404
    assert client.get(_url("operacao", operacao_id=op.id)).status_code == 404
    assert client.post(_url("operacao_cancelar", operacao_id=op.id)).status_code == 404
    op.refresh_from_db()
    assert op.status == "pendente"
    assert client.get(_url("modelos_inventario", maquina_id=uuid.uuid4())).status_code == 404


# ═══ instalar e remover ═════════════════════════════════════════════════════

def test_admin_instala_pela_tela_e_a_operacao_e_enfileirada(client, admin, local, sem_broker):
    resp = _logar(client, admin).post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "llama3.1:8b"})
    assert resp.status_code == 202
    d = _json(resp)["operacao"]
    assert d["tipo"] == "pull" and d["modelo"] == "llama3.1:8b" and d["status"] == "pendente"
    op = Op.objects.get(pk=d["id"])
    assert op.solicitado_por_id == admin.id and op.maquina_id == local.id
    assert sem_broker.call_args.kwargs["queue"] == "ollama_admin"


def test_admin_remove_pela_tela(client, admin, local):
    resp = _logar(client, admin).post(_url("modelos_remover", maquina_id=local.id), {"modelo": "llama3.1:8b"})
    assert resp.status_code == 202 and _json(resp)["operacao"]["tipo"] == "delete"


@pytest.mark.parametrize("nome", ["modelos_instalar", "modelos_remover"])
def test_membro_recebe_403_em_json_e_nada_e_criado(client, membro, local, nome):
    resp = _logar(client, membro).post(_url(nome, maquina_id=local.id), {"modelo": "llama3.1:8b"})
    assert resp.status_code == 403 and "dono ou administrador" in _json(resp)["erro"]
    assert not Op.objects.exists()


@pytest.mark.parametrize("nome", ["modelos_instalar", "modelos_remover"])
def test_so_aceita_post_e_exige_csrf(admin, local, nome):
    c = Client(enforce_csrf_checks=True)
    c.force_login(admin)
    assert c.post(_url(nome, maquina_id=local.id), {"modelo": "a:1"}).status_code == 403     # sem token CSRF
    assert c.get(_url(nome, maquina_id=local.id)).status_code == 405
    assert not Op.objects.exists()


@pytest.mark.parametrize("modelo", ["", "../x", "Nome Ruim", "registry.exemplo.com/a/b:1", "a" * 300])
def test_nome_invalido_e_400_com_erro_em_json(client, admin, local, modelo):
    resp = _logar(client, admin).post(_url("modelos_instalar", maquina_id=local.id), {"modelo": modelo})
    assert resp.status_code == 400 and "erro" in _json(resp) and not Op.objects.exists()


def test_conflito_e_409(client, admin, local):
    _logar(client, admin)
    assert client.post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "a:1"}).status_code == 202
    resp = client.post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "a:1"})
    assert resp.status_code == 409 and "em andamento" in _json(resp)["erro"]
    assert client.post(_url("modelos_remover", maquina_id=local.id), {"modelo": "a:1"}).status_code == 409


def test_ollama_fora_do_ar_e_503_e_sem_ollama_e_409(client, admin, local, settings):
    _logar(client, admin)
    MaquinaStatus.objects.filter(maquina=local).update(ollama_disponivel=False)
    assert client.post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "a:1"}).status_code == 503
    settings.OLLAMA_HOST = ""
    assert client.post(_url("modelos_instalar", maquina_id=local.id), {"modelo": "a:1"}).status_code == 409


# ═══ par: online funciona, offline falha na hora ═══════════════════════════

def test_par_offline_falha_na_hora_pela_tela_sem_chamar_ninguem(client, admin, org, local):
    off = _par(org, "notebook-off", online=False)
    with mock.patch.object(operacoes, "chamar_par", side_effect=AssertionError("não deveria chamar")):
        resp = _logar(client, admin).post(_url("modelos_instalar", maquina_id=off.id), {"modelo": "a:1"})
    assert resp.status_code == 503 and "offline" in _json(resp)["erro"]
    assert not Op.objects.filter(maquina=off).exists()


def test_par_online_recebe_o_comando_pela_tela(client, admin, org, local):
    par = _par(org)
    remoto = uuid.uuid4()
    with mock.patch.object(operacoes, "chamar_par", return_value=RespostaPar(200, {"id": str(remoto), "status": "pendente"}, True)):
        resp = _logar(client, admin).post(_url("modelos_instalar", maquina_id=par.id), {"modelo": "a:1"})
    assert resp.status_code == 202
    assert Op.objects.get(maquina=par).operacao_remota_id == remoto


def test_par_que_nao_responde_e_503(client, admin, org, local):
    par = _par(org)
    with mock.patch.object(operacoes, "chamar_par", side_effect=ParInacessivel("x")):
        resp = _logar(client, admin).post(_url("modelos_instalar", maquina_id=par.id), {"modelo": "a:1"})
    assert resp.status_code == 503 and not Op.objects.exists()


# ═══ operação e cancelar ════════════════════════════════════════════════════

def test_consultar_e_cancelar_uma_operacao(client, admin, local):
    op = Op.objects.create(maquina=local, tipo="pull", modelo="a:1", status="executando", celery_task_id="t-9")
    _logar(client, admin)
    assert _json(client.get(_url("operacao", operacao_id=op.id)))["operacao"]["status"] == "executando"
    with mock.patch("celery.current_app.control.revoke") as revoke:
        resp = client.post(_url("operacao_cancelar", operacao_id=op.id))
    assert resp.status_code == 200 and _json(resp)["operacao"]["status"] == "cancelada"
    revoke.assert_called_once()


def test_membro_nao_cancela(client, membro, local):
    op = Op.objects.create(maquina=local, tipo="pull", modelo="a:1", status="executando")
    resp = _logar(client, membro).post(_url("operacao_cancelar", operacao_id=op.id))
    assert resp.status_code == 403
    op.refresh_from_db()
    assert op.status == "executando"


def test_cancelar_operacao_de_par_offline_falha_na_hora(client, admin, org, local):
    par = _par(org, online=False)
    op = Op.objects.create(maquina=par, tipo="pull", modelo="a:1", status="executando", operacao_remota_id=uuid.uuid4())
    resp = _logar(client, admin).post(_url("operacao_cancelar", operacao_id=op.id))
    assert resp.status_code == 503
    op.refresh_from_db()
    assert op.status == "executando"

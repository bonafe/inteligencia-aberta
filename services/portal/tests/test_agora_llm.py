"""Chat com os modelos do cluster no Ultima Agora: isolamento por organização, só máquinas online e recentes."""
import json
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extractors.ollama_client import OllamaIndisponivel
from apps.artifacts.models import AuditLog
from apps.cluster.models import Maquina, MaquinaModeloOllama, MaquinaStatus

pytestmark = pytest.mark.django_db
MODELO = "qwen3.5:9b"
URL_MODELOS, URL_CHAT = "/agora/api/v1/dominio/llm/modelos/", "/agora/api/v1/dominio/llm/chat/"


def _org(slug):
    dono = User.objects.create_user(username=f"dono-{slug}", password="x")
    org = Organization.objects.create(name=slug.upper(), slug=slug, org_type="team", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org, dono


def _maquina(org, dono, apelido, *, modelo=MODELO, online=True, visto=None, estado="confirmado", tps=None, endpoint="http://10.0.0.1:11434"):
    m = Maquina.objects.create(organizacao=org, dono=dono, apelido=apelido, estado=estado, ollama_endpoint=endpoint)
    MaquinaStatus.objects.create(
        maquina=m, cpu_count=4,
        ultimo_heartbeat_em=timezone.now() if online else timezone.now() - timezone.timedelta(hours=1),
    )
    MaquinaModeloOllama.objects.create(
        maquina=m, nome_modelo=modelo, visto_pela_ultima_vez=visto or timezone.now(),
        tokens_por_segundo_medio=tps, amostras_n=1 if tps else 0,
    )
    return m


def _cliente(usuario):
    c = Client()
    c.force_login(usuario)
    return c


def _post(cliente, **corpo):
    corpo.setdefault("modelo", MODELO)
    corpo.setdefault("mensagens", [{"role": "user", "content": "oi"}])
    return cliente.post(URL_CHAT, data=json.dumps(corpo), content_type="application/json")


def _resposta(texto="olá!"):
    return {"message": {"role": "assistant", "content": texto}, "prompt_eval_count": 5, "eval_count": 20, "eval_duration": 2_000_000_000}


@pytest.fixture
def mundo():
    org_a, dono_a = _org("a")
    org_b, dono_b = _org("b")
    return org_a, dono_a, org_b, dono_b


def test_lista_so_modelos_de_maquinas_da_propria_organizacao_online_e_recentes(mundo):
    org_a, dono_a, org_b, dono_b = mundo
    _maquina(org_a, dono_a, "celtas", tps=12.34)
    _maquina(org_a, dono_a, "persas", modelo="llama3:8b")
    _maquina(org_a, dono_a, "offline", modelo="x:1", online=False)
    _maquina(org_a, dono_a, "velha", modelo="y:1", visto=timezone.now() - timezone.timedelta(hours=1))
    _maquina(org_a, dono_a, "pendente", modelo="z:1", estado="pendente")
    _maquina(org_b, dono_b, "alheia", modelo="w:1")
    dados = _cliente(dono_a).get(URL_MODELOS).json()["modelos"]
    assert {m["modelo"] for m in dados} == {MODELO, "llama3:8b"}
    qwen = next(m for m in dados if m["modelo"] == MODELO)
    assert [(x["apelido"], x["tokens_por_segundo"]) for x in qwen["maquinas"]] == [("celtas", 12.3)]


def test_exige_sessao():
    assert Client().get(URL_MODELOS).status_code in (301, 302, 401, 403)
    assert Client().post(URL_CHAT, data="{}", content_type="application/json").status_code in (301, 302, 401, 403)


def test_chat_devolve_a_resposta_e_a_maquina_que_atendeu(mundo):
    org_a, dono_a, *_ = mundo
    celtas = _maquina(org_a, dono_a, "celtas")
    with mock.patch("apps.agora.views_llm.gerar_chat", return_value=_resposta()) as gerar:
        resp = _post(_cliente(dono_a))
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["conteudo"] == "olá!" and dados["maquina"] == {"id": str(celtas.id), "apelido": "celtas"}
    assert dados["tokens"] == {"entrada": 5, "saida": 20} and dados["tokens_por_segundo"] == 10.0
    assert gerar.call_args.kwargs["host"] == "http://10.0.0.1:11434" and gerar.call_args.kwargs["finalidade"] == "chat"
    log = AuditLog.objects.get(operation="llm.chat")
    assert log.organization_id == org_a.id and log.user_id == dono_a.id and "oi" not in json.dumps(log.metadata)


def test_pessoa_pode_fixar_a_maquina(mundo):
    org_a, dono_a, *_ = mundo
    _maquina(org_a, dono_a, "celtas", tps=50.0)
    persas = _maquina(org_a, dono_a, "persas", tps=1.0, endpoint="http://10.0.0.2:11434")
    with mock.patch("apps.agora.views_llm.gerar_chat", return_value=_resposta()) as gerar:
        resp = _post(_cliente(dono_a), maquina=str(persas.id))
    assert resp.json()["maquina"]["apelido"] == "persas" and gerar.call_args.kwargs["host"] == "http://10.0.0.2:11434"


def test_maquina_de_outra_organizacao_ou_sem_o_modelo_e_404(mundo):
    org_a, dono_a, org_b, dono_b = mundo
    _maquina(org_a, dono_a, "celtas")
    alheia = _maquina(org_b, dono_b, "alheia")
    with mock.patch("apps.agora.views_llm.gerar_chat") as gerar:
        assert _post(_cliente(dono_a), maquina=str(alheia.id)).status_code == 404
        assert _post(_cliente(dono_a), modelo="nao-existe:1").status_code == 404
    gerar.assert_not_called()


def test_maquina_com_id_invalido_e_400(mundo):
    org_a, dono_a, *_ = mundo
    _maquina(org_a, dono_a, "celtas")
    assert _post(_cliente(dono_a), maquina="isso-nao-e-uuid").status_code == 400


@pytest.mark.parametrize("corpo", [
    {"modelo": ""}, {"modelo": 5}, {"mensagens": []}, {"mensagens": "oi"},
    {"mensagens": [{"role": "robo", "content": "x"}]}, {"mensagens": [{"role": "user", "content": 1}]},
    {"mensagens": [{"role": "assistant", "content": "x"}]},            # a última tem de ser do usuário
    {"mensagens": [{"role": "user", "content": "   "}]},
    {"mensagens": [{"role": "user", "content": "x" * 8001}]},
    {"mensagens": [{"role": "user", "content": "x"}] * 51},
])
def test_entrada_invalida_e_400(mundo, corpo):
    org_a, dono_a, *_ = mundo
    _maquina(org_a, dono_a, "celtas")
    with mock.patch("apps.agora.views_llm.gerar_chat") as gerar:
        assert _post(_cliente(dono_a), **corpo).status_code == 400
    gerar.assert_not_called()


def test_corpo_que_nao_e_json_e_400(mundo):
    _, dono_a, *_ = mundo
    assert _cliente(dono_a).post(URL_CHAT, data="{", content_type="application/json").status_code == 400
    assert _cliente(dono_a).post(URL_CHAT, data="[1]", content_type="application/json").status_code == 400


def test_ollama_indisponivel_vira_424_sem_vazar_detalhe(mundo):
    org_a, dono_a, *_ = mundo
    _maquina(org_a, dono_a, "celtas")
    with mock.patch("apps.agora.views_llm.gerar_chat", side_effect=OllamaIndisponivel("Ollama indisponível em http://10.0.0.1:11434: x")):
        resp = _post(_cliente(dono_a))
    assert resp.status_code == 424 and "10.0.0.1" not in resp.content.decode()
    assert not AuditLog.objects.filter(operation="llm.chat").exists()


def test_chat_exige_csrf(mundo):
    org_a, dono_a, *_ = mundo
    _maquina(org_a, dono_a, "celtas")
    c = Client(enforce_csrf_checks=True)
    c.force_login(dono_a)
    assert c.post(URL_CHAT, data="{}", content_type="application/json").status_code == 403

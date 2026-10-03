"""ollama_client degrada graciosamente quando o Ollama (nativo no host) está
offline — listar_modelos nunca levanta; gerar levanta OllamaIndisponivel."""
from unittest import mock

import pytest
import requests

from apps.artifacts.extractors.ollama_client import OllamaIndisponivel, gerar, gerar_chat, listar_modelos


def test_listar_modelos_retorna_vazio_sem_levantar_quando_offline():
    with mock.patch("requests.get", side_effect=requests.ConnectionError("recusado")):
        assert listar_modelos() == []


def test_listar_modelos_retorna_nomes():
    resp = mock.Mock()
    resp.json.return_value = {"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]}
    resp.raise_for_status = mock.Mock()
    with mock.patch("requests.get", return_value=resp):
        assert listar_modelos() == ["llama3.1:8b", "mistral:7b"]


def test_gerar_levanta_ollama_indisponivel_em_timeout():
    with mock.patch("requests.post", side_effect=requests.Timeout("timeout")):
        with pytest.raises(OllamaIndisponivel):
            gerar("llama3.1:8b", "system", "prompt")


def test_gerar_retorna_texto_da_resposta():
    resp = mock.Mock()
    resp.json.return_value = {"message": {"content": "{\"ok\": true}"}}
    resp.raise_for_status = mock.Mock()
    with mock.patch("requests.post", return_value=resp):
        assert gerar("llama3.1:8b", "system", "prompt") == '{"ok": true}'


def test_gerar_envia_num_thread_para_qualquer_modelo(settings):
    """num_thread vai em toda chamada — não depende de o modelo ter sido
    criado com um Modelfile próprio que já fixe isso."""
    settings.OLLAMA_NUM_THREAD = 24
    resp = mock.Mock()
    resp.json.return_value = {"message": {"content": "ok"}}
    resp.raise_for_status = mock.Mock()
    with mock.patch("requests.post", return_value=resp) as post:
        gerar("llama3.1:8b", "system", "prompt")
    assert post.call_args.kwargs["json"]["options"]["num_thread"] == 24


def test_gerar_usa_host_e_num_thread_informados_em_vez_do_settings(settings):
    """O roteador (apps.cluster.llm_router) passa host/num_thread por
    máquina — isso tem que vencer o valor global de settings."""
    settings.OLLAMA_HOST = "http://local:11434"
    settings.OLLAMA_NUM_THREAD = 24
    resp = mock.Mock()
    resp.json.return_value = {"message": {"content": "ok"}}
    resp.raise_for_status = mock.Mock()
    with mock.patch("requests.post", return_value=resp) as post:
        gerar("qwen3.5:9b", "system", "prompt", host="http://outra-maquina:11434", num_thread=4)
    assert post.call_args.args[0] == "http://outra-maquina:11434/api/chat"
    assert post.call_args.kwargs["json"]["options"]["num_thread"] == 4


def test_gerar_chat_devolve_resposta_crua_com_messages_multi_turno():
    resp = mock.Mock()
    resp.json.return_value = {"message": {"content": "ok"}, "eval_count": 5, "eval_duration": 500_000_000}
    resp.raise_for_status = mock.Mock()
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    with mock.patch("requests.post", return_value=resp) as post:
        data = gerar_chat("qwen3.5:9b", messages)
    assert data["message"]["content"] == "ok"
    assert post.call_args.kwargs["json"]["messages"] == messages


@pytest.mark.django_db
def test_gerar_com_maquina_id_registra_telemetria(settings):
    """Uma chamada bem-sucedida com maquina_id vira evento llm.chamada_ollama
    e atualiza MaquinaModeloOllama — é assim que o roteador aprende qual
    máquina é mais rápida (e com que contexto/threads), sem rodar benchmark
    separado."""
    from apps.accounts.models import Organization, User
    from apps.cluster.models import Maquina, MaquinaModeloOllama

    settings.OLLAMA_NUM_CTX = 16384
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="Org", slug="org", org_type="individual", owner=dono)
    maquina = Maquina.objects.create(
        apelido="notebook", organizacao=org, dono=dono, modo="compute", token_hash="x",
    )

    resp = mock.Mock()
    resp.json.return_value = {"message": {"content": "ok"}, "eval_count": 10, "eval_duration": 1_000_000_000}
    resp.raise_for_status = mock.Mock()
    with mock.patch("requests.post", return_value=resp):
        gerar("qwen3.5:9b", "s", "p", num_thread=8, maquina_id=str(maquina.id))

    capacidade = MaquinaModeloOllama.objects.get(maquina=maquina, nome_modelo="qwen3.5:9b")
    assert capacidade.amostras_n == 1
    assert capacidade.tokens_por_segundo_medio == pytest.approx(10.0)  # 10 tokens em 1s
    assert capacidade.num_thread_observado == 8
    assert capacidade.num_ctx_observado == 16384

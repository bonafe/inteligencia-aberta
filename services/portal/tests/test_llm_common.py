"""gerar_texto devolve (texto, maquina_id) — é o maquina_id que
EstruturacaoLLM/Comparacao gravam pra mostrar proveniência no Mapa Vivo.
"""
from unittest import mock

from apps.artifacts.extractors.llm_common import gerar_texto


def test_anthropic_nunca_tem_maquina_id():
    resposta = mock.Mock()
    resposta.content = [mock.Mock(text="ok")]
    cliente = mock.Mock()
    cliente.messages.create.return_value = resposta

    with mock.patch("apps.artifacts.extractors.llm_common._get_client", return_value=cliente):
        texto, maquina_id = gerar_texto("anthropic", "claude-sonnet-5", "sistema", "prompt")

    assert texto == "ok"
    assert maquina_id is None


def test_ollama_sem_cluster_nao_tem_maquina_id():
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=None), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar_mock:
        texto, maquina_id = gerar_texto("ollama", "qwen3.5:9b", "sistema", "prompt")

    assert texto == "ok"
    assert maquina_id is None
    gerar_mock.assert_called_once_with("qwen3.5:9b", "sistema", "prompt")


def test_ollama_com_cluster_devolve_maquina_do_roteador():
    from apps.cluster.llm_router import ExecucaoOllama

    execucao = ExecucaoOllama(maquina_id="abc-123", host="http://antares:11434", num_thread=16)
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=execucao), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar_mock:
        texto, maquina_id = gerar_texto("ollama", "qwen3.5:9b", "sistema", "prompt")

    assert texto == "ok"
    assert maquina_id == "abc-123"
    gerar_mock.assert_called_once_with(
        "qwen3.5:9b", "sistema", "prompt", host="http://antares:11434", num_thread=16, maquina_id="abc-123",
    )

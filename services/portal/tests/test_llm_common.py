"""gerar_texto devolve (texto, maquina_id) — é o maquina_id que
EstruturacaoLLM/Comparacao gravam pra mostrar proveniência no Mapa Vivo.
"""
from unittest import mock

import pytest

from apps.artifacts.extractors.llm_common import gerar_texto
from apps.events.models import Finalidade


@pytest.mark.django_db
def test_anthropic_nunca_tem_maquina_id():
    resposta = mock.Mock()
    resposta.content = [mock.Mock(text="ok")]
    cliente = mock.Mock()
    cliente.messages.create.return_value = resposta

    with mock.patch("apps.artifacts.extractors.llm_common._get_client", return_value=cliente):
        texto, maquina_id = gerar_texto(
            "anthropic", "claude-sonnet-5", "sistema", "prompt",
            finalidade=Finalidade.ESTRUTURACAO_MANUAL, classificacao="interno",
        )

    assert texto == "ok"
    assert maquina_id is None


@pytest.mark.django_db
def test_ollama_sem_cluster_nao_tem_maquina_id():
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=None), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar_mock:
        texto, maquina_id = gerar_texto(
            "ollama", "qwen3.5:9b", "sistema", "prompt",
            finalidade=Finalidade.ESTRUTURACAO_MANUAL, classificacao="interno",
        )

    assert texto == "ok"
    assert maquina_id is None
    gerar_mock.assert_called_once_with(
        "qwen3.5:9b", "sistema", "prompt", num_ctx=None,
        finalidade=Finalidade.ESTRUTURACAO_MANUAL, subject_id=None, tenant_id=None,
    )


@pytest.mark.django_db
def test_ollama_com_cluster_devolve_maquina_do_roteador():
    from apps.cluster.llm_router import ExecucaoOllama

    execucao = ExecucaoOllama(maquina_id="abc-123", host="http://antares:11434", num_thread=16)
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=execucao), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar_mock:
        texto, maquina_id = gerar_texto(
            "ollama", "qwen3.5:9b", "sistema", "prompt",
            finalidade=Finalidade.ESTRUTURACAO_MANUAL, classificacao="interno",
        )

    assert texto == "ok"
    assert maquina_id == "abc-123"
    gerar_mock.assert_called_once_with(
        "qwen3.5:9b", "sistema", "prompt", host="http://antares:11434", num_thread=16, num_ctx=None, maquina_id="abc-123",
        finalidade=Finalidade.ESTRUTURACAO_MANUAL, subject_id=None, tenant_id=None,
    )


@pytest.mark.django_db
def test_ollama_com_peer_com_gateway_chama_pelo_gateway():
    from apps.cluster.llm_router import ExecucaoOllama

    execucao = ExecucaoOllama(
        maquina_id="abc-123", host="http://antares:11434", num_thread=16, gateway="http://antares:8000",
    )
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=execucao), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar_mock:
        texto, maquina_id = gerar_texto(
            "ollama", "qwen3.5:9b", "sistema", "prompt", finalidade=Finalidade.ESTRUTURACAO_MANUAL, classificacao="interno",
        )

    assert (texto, maquina_id) == ("ok", "abc-123")
    assert gerar_mock.call_args.kwargs["gateway"] == "http://antares:8000"


# ── a política de LLM externo é imposta no ponto único de chamada ────────────

@pytest.mark.parametrize("nivel", ["restrito", "confidencial"])
def test_gerar_texto_recusa_provider_externo_para_dado_restrito(nivel):
    from apps.artifacts.extractors.llm_common import PoliticaLLMNegada

    with mock.patch("apps.artifacts.extractors.llm_common._get_client") as cliente:
        with pytest.raises(PoliticaLLMNegada):
            gerar_texto("anthropic", "m", "s", "p", finalidade=Finalidade.ESTRUTURACAO_MANUAL, classificacao=nivel)
    cliente.assert_not_called()          # nem chave nem rede são tocadas


def test_gerar_texto_exige_declarar_a_classificacao():
    with pytest.raises(TypeError):
        gerar_texto("anthropic", "m", "s", "p", finalidade=Finalidade.ESTRUTURACAO_MANUAL)


@pytest.mark.django_db
def test_ollama_nao_e_externo_e_aceita_dado_confidencial():
    with mock.patch("apps.cluster.llm_router.escolher_execucao", return_value=None), \
         mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok"):
        texto, _ = gerar_texto("ollama", "m", "s", "p", finalidade=Finalidade.ESTRUTURACAO_MANUAL,
                               classificacao="confidencial")
    assert texto == "ok"

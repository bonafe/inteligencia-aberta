"""ollama_client degrada graciosamente quando o Ollama (nativo no host) está
offline — listar_modelos nunca levanta; gerar levanta OllamaIndisponivel."""
from unittest import mock

import pytest
import requests

from apps.artifacts.extractors.ollama_client import OllamaIndisponivel, gerar, listar_modelos


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

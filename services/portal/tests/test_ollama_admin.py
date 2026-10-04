"""Cliente de administração do Ollama (Marco D1): tudo com a API HTTP simulada."""
import json
from unittest import mock

import pytest
import requests

from apps.cluster import ollama_admin as oa
from apps.cluster.ollama_admin import (
    DiscoCheio, ErroOllama, ModeloNaoEncontrado, OllamaIndisponivel, validar_nome_modelo,
)


@pytest.fixture(autouse=True)
def host(settings):
    settings.OLLAMA_HOST = "http://ollama:11434"
    settings.OLLAMA_PERMITE_REGISTRY_EXTERNO = False


def _resp(status=200, dados=None, linhas=None, conteudo=b"x"):
    r = mock.MagicMock(status_code=status, content=conteudo)
    r.json.return_value = dados
    if linhas is not None:
        r.iter_lines.return_value = iter(linhas)
    return r


def _linhas(*eventos):
    return [json.dumps(e).encode() if isinstance(e, dict) else e for e in eventos]


# ── nome do modelo ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", ["llama3", "qwen3.5:9b", "llama3.1:8b-instruct-q4_K_M", "usuario/modelo:latest",
                                  "nomic-embed-text", "a" * 200])
def test_nome_valido(nome):
    assert validar_nome_modelo(nome) == nome


@pytest.mark.parametrize("nome", [
    "", "   ", None, "Llama3", "llama 3", "llama3;rm -rf /", "../etc/passwd", "a/../b", "llama3:", ":tag", "/abs",
    "http://x/y", "llama3\nx", "a" * 201, "usuario//modelo", "modelo:tag:outra",
])
def test_nome_invalido(nome):
    with pytest.raises(ValueError):
        validar_nome_modelo(nome)


@pytest.mark.parametrize("nome", ["registry.exemplo.com/usuario/modelo:latest", "localhost:5000/modelo", "host.docker.internal/x/y"])
def test_registry_externo_exige_permissao_explicita(nome, settings):
    with pytest.raises(ValueError, match="registry oficial"):
        validar_nome_modelo(nome)
    settings.OLLAMA_PERMITE_REGISTRY_EXTERNO = True
    assert validar_nome_modelo(nome) == nome


# ── consultas ───────────────────────────────────────────────────────────────

def test_listar_detalhado_aproveita_o_que_o_tags_ja_devolve():
    dados = {"models": [
        {"name": "qwen3.5:9b", "size": 5_500_000_000, "digest": "abc123", "modified_at": "2026-10-01T10:00:00Z",
         "details": {"family": "qwen", "parameter_size": "9B", "quantization_level": "Q4_K_M"}},
        {"model": "so-model:latest", "size": 10}, {"size": 5}, {"name": "sem-detalhes:1"},
    ]}
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados=dados)) as get:
        modelos = oa.listar_detalhado()
    assert get.call_args.args[0] == "http://ollama:11434/api/tags"
    assert modelos[0] == {"nome": "qwen3.5:9b", "tamanho_bytes": 5_500_000_000, "digest": "abc123", "familia": "qwen",
                          "parametros": "9B", "quantizacao": "Q4_K_M", "modificado_em": "2026-10-01T10:00:00Z"}
    assert [m["nome"] for m in modelos] == ["qwen3.5:9b", "so-model:latest", "sem-detalhes:1"]   # o sem nome é descartado
    assert modelos[2]["familia"] == "" and modelos[2]["tamanho_bytes"] is None


def test_ps_lista_o_que_esta_em_memoria():
    dados = {"models": [{"name": "qwen3.5:9b", "size": 7, "size_vram": 5, "expires_at": "2026-10-04T12:00:00Z"}, {}]}
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados=dados)):
        assert oa.ps() == [{"nome": "qwen3.5:9b", "tamanho_bytes": 7, "tamanho_vram_bytes": 5, "expira_em": "2026-10-04T12:00:00Z"}]


def test_versao():
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados={"version": "0.35.1"})):
        assert oa.versao() == "0.35.1"
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados={})):
        assert oa.versao() == ""


def test_host_explicito_vence_a_configuracao():
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados={"models": []})) as get:
        oa.listar_detalhado(host="http://host.docker.internal:11434/")
    assert get.call_args.args[0] == "http://host.docker.internal:11434/api/tags"


def test_sem_ollama_configurado(settings):
    settings.OLLAMA_HOST = ""
    for chamada in (oa.listar_detalhado, oa.ps, oa.versao, lambda: oa.delete("x"), lambda: list(oa.pull_stream("x"))):
        with pytest.raises(OllamaIndisponivel, match="não está configurado"):
            chamada()


@pytest.mark.parametrize("excecao", [requests.ConnectionError("x"), requests.Timeout("x")])
def test_ollama_fora_do_ar(excecao):
    with mock.patch("apps.cluster.ollama_admin.requests.get", side_effect=excecao):
        with pytest.raises(OllamaIndisponivel):
            oa.listar_detalhado()


def test_resposta_ruim_vira_erro():
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(status=500)):
        with pytest.raises(ErroOllama, match="HTTP 500"):
            oa.ps()
    quebrada = _resp()
    quebrada.json.side_effect = ValueError("x")
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=quebrada):
        with pytest.raises(ErroOllama, match="não é JSON"):
            oa.versao()
    with mock.patch("apps.cluster.ollama_admin.requests.get", return_value=_resp(dados=[1, 2])):
        assert oa.listar_detalhado() == []


def test_show():
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=_resp(dados={"license": "x"}, conteudo=b"{}")) as post:
        assert oa.show("qwen3.5:9b") == {"license": "x"}
    assert post.call_args.kwargs["json"] == {"model": "qwen3.5:9b"}
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=_resp(status=404)):
        with pytest.raises(ModeloNaoEncontrado):
            oa.show("nao-existe")
    with pytest.raises(ValueError):
        oa.show("nome ruim")


# ── pull com stream ─────────────────────────────────────────────────────────

def test_pull_gera_o_progresso_e_termina_no_sucesso():
    linhas = _linhas({"status": "pulling manifest"}, b"", b"lixo que nao e json",
                     {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 250},
                     {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 1000},
                     b'["nao-e-um-objeto"]', {"status": "success"})
    resp = _resp(linhas=linhas)
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp) as post:
        eventos = list(oa.pull_stream("qwen3.5:9b"))
    assert [e["status"] for e in eventos] == ["pulling manifest", "pulling abc", "pulling abc", "success"]
    assert eventos[1]["completed"] == 250
    assert post.call_args.kwargs["json"] == {"model": "qwen3.5:9b", "stream": True}
    assert post.call_args.kwargs["stream"] is True and post.call_args.kwargs["timeout"] == (5, 120)   # sem timeout total
    resp.close.assert_called_once()


def test_pull_para_quando_o_consumidor_para_de_iterar_e_fecha_a_conexao():
    resp = _resp(linhas=_linhas({"status": "a"}, {"status": "b"}, {"status": "success"}))
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp):
        gerador = oa.pull_stream("qwen3.5:9b")
        assert next(gerador)["status"] == "a"
        gerador.close()                                         # cancelamento
    resp.close.assert_called_once()


@pytest.mark.parametrize("mensagem,excecao", [
    ("pull model manifest: file does not exist", ModeloNaoEncontrado),
    ("model 'x' not found", ModeloNaoEncontrado),
    ("write /root/.ollama/blobs/x: no space left on device", DiscoCheio),
    ("insufficient disk space: not enough space", DiscoCheio),
    ("algo inesperado", ErroOllama),
])
def test_erros_ditos_pelo_ollama_no_meio_do_pull(mensagem, excecao):
    resp = _resp(linhas=_linhas({"status": "pulling manifest"}, {"error": mensagem}))
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp):
        with pytest.raises(excecao):
            list(oa.pull_stream("qwen3.5:9b"))
    resp.close.assert_called_once()


def test_pull_http_404_e_outros_status():
    for status, excecao in ((404, ModeloNaoEncontrado), (500, ErroOllama)):
        resp = _resp(status=status)
        with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp):
            with pytest.raises(excecao):
                list(oa.pull_stream("qwen3.5:9b"))
        resp.close.assert_called_once()


def test_conexao_cortada_no_meio_do_pull():
    def linhas():
        yield json.dumps({"status": "pulling x", "total": 10, "completed": 1}).encode()
        raise requests.ConnectionError("cortou")

    resp = _resp()
    resp.iter_lines.return_value = linhas()
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp):
        gerador = oa.pull_stream("qwen3.5:9b")
        assert next(gerador)["completed"] == 1
        with pytest.raises(OllamaIndisponivel, match="interrompida"):
            next(gerador)


def test_pull_que_acaba_sem_sucesso_e_tratado_como_interrompido():
    resp = _resp(linhas=_linhas({"status": "pulling x", "total": 10, "completed": 5}))
    with mock.patch("apps.cluster.ollama_admin.requests.post", return_value=resp):
        with pytest.raises(OllamaIndisponivel, match="sem confirmar o sucesso"):
            list(oa.pull_stream("qwen3.5:9b"))


def test_pull_nao_conecta_e_nome_invalido():
    with mock.patch("apps.cluster.ollama_admin.requests.post", side_effect=requests.ConnectionError("x")):
        with pytest.raises(OllamaIndisponivel):
            list(oa.pull_stream("qwen3.5:9b"))
    with pytest.raises(ValueError):
        list(oa.pull_stream("../x"))


# ── delete ──────────────────────────────────────────────────────────────────

def test_delete_manda_model_e_name_para_cobrir_versoes_antigas():
    with mock.patch("apps.cluster.ollama_admin.requests.delete", return_value=_resp()) as rm:
        oa.delete("qwen3.5:9b")
    assert rm.call_args.args[0] == "http://ollama:11434/api/delete"
    assert rm.call_args.kwargs["json"] == {"model": "qwen3.5:9b", "name": "qwen3.5:9b"}


def test_delete_erros():
    with mock.patch("apps.cluster.ollama_admin.requests.delete", return_value=_resp(status=404)):
        with pytest.raises(ModeloNaoEncontrado):
            oa.delete("nao-existe")
    with mock.patch("apps.cluster.ollama_admin.requests.delete", return_value=_resp(status=500)):
        with pytest.raises(ErroOllama):
            oa.delete("qwen3.5:9b")
    with mock.patch("apps.cluster.ollama_admin.requests.delete", side_effect=requests.Timeout("x")):
        with pytest.raises(OllamaIndisponivel):
            oa.delete("qwen3.5:9b")
    with pytest.raises(ValueError):
        oa.delete("nome ruim")

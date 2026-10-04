"""Administração do Ollama pela API HTTP: listar, ver o que está em memória, instalar e remover.

Só HTTP até `OLLAMA_HOST` (nativo em `host.docker.internal:11434` ou container em `ollama:11434`): nada de
Docker socket nem de shell no host. Fica **separado** de `apps.artifacts.extractors.ollama_client` (o da
extração) de propósito, para não arriscar o caminho quente de uma captura.

O Ollama **não tem autenticação**. Este módulo só o alcança por dentro da própria instância; entre
instâncias o caminho é o canal assinado (`apps.federacao`), nunca uma rota que repasse tráfego bruto.

`pull_stream` não tem timeout total — instalar um modelo de dezenas de GB leva horas — só um timeout de
**leitura por pedaço**: se o Ollama ficar calado por `TIMEOUT_LEITURA_S`, a conexão é considerada morta.
"""

import json
import logging
import re
from collections.abc import Iterator

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_CONEXAO_S = 5
TIMEOUT_LEITURA_S = 120
TIMEOUT_CONSULTA_S = 10
NOME_MAXIMO = 200
_NOME = re.compile(r"^[a-z0-9][a-z0-9._\-]*(/[a-z0-9][a-z0-9._\-]*)*(:[A-Za-z0-9][A-Za-z0-9._\-]*)?$")


class ErroOllama(Exception):
    """O Ollama respondeu com um erro que não se encaixa nos outros."""


class OllamaIndisponivel(ErroOllama):
    """Não foi possível falar com o Ollama (não configurado, fora do ar, conexão cortada)."""


class ModeloNaoEncontrado(ErroOllama):
    """O Ollama não conhece o modelo (nome errado, ou nada a remover)."""


class DiscoCheio(ErroOllama):
    """Não há espaço para instalar o modelo."""


_HOST_REGISTRY = re.compile(r"^[a-z0-9][a-z0-9.\-]*(:\d{1,5})?$")


def validar_nome_modelo(nome: str) -> str:
    """Nome normalizado de um modelo (`nome`, `nome:tag` ou `usuario/nome:tag`) ou `ValueError`.

    Por padrão só aceita o registry oficial: um nome com **host** na frente (`outro.com/x/y`, `host:5000/y`)
    exige `OLLAMA_PERMITE_REGISTRY_EXTERNO`. A regex também barra `..`, espaços, `;`, URLs e maiúsculas
    (o Ollama normaliza para minúsculas, e dois nomes para o mesmo modelo confundiriam o controle de duplicidade).
    """
    nome = (nome or "").strip()
    if not nome or len(nome) > NOME_MAXIMO or ".." in nome:
        raise ValueError("nome de modelo inválido (use nome, nome:tag ou usuario/nome:tag)")
    primeiro, _, resto = nome.partition("/")
    tem_host = bool(resto) and ("." in primeiro or ":" in primeiro)
    if tem_host:
        if not getattr(settings, "OLLAMA_PERMITE_REGISTRY_EXTERNO", False):
            raise ValueError("só o registry oficial do Ollama é aceito (registry externo exige configuração explícita)")
        if not _HOST_REGISTRY.match(primeiro) or not _NOME.match(resto):
            raise ValueError("nome de modelo inválido (use nome, nome:tag ou usuario/nome:tag)")
    elif not _NOME.match(nome):
        raise ValueError("nome de modelo inválido (use nome, nome:tag ou usuario/nome:tag)")
    return nome


def _base(host: str | None) -> str:
    base = (host or getattr(settings, "OLLAMA_HOST", "") or "").rstrip("/")
    if not base:
        raise OllamaIndisponivel("o Ollama não está configurado nesta instância (OLLAMA_HOST vazio)")
    return base


def _get(host, caminho, timeout=TIMEOUT_CONSULTA_S):
    try:
        resposta = requests.get(_base(host) + caminho, timeout=(TIMEOUT_CONEXAO_S, timeout))
    except requests.RequestException as exc:
        raise OllamaIndisponivel(f"Ollama inacessível ({type(exc).__name__})") from None
    if resposta.status_code != 200:
        raise ErroOllama(f"{caminho} respondeu HTTP {resposta.status_code}")
    try:
        dados = resposta.json()
    except ValueError:
        raise ErroOllama(f"{caminho} devolveu algo que não é JSON") from None
    return dados if isinstance(dados, dict) else {}


def listar_detalhado(host: str | None = None) -> list[dict]:
    """Modelos instalados, com tamanho e detalhes (`/api/tags` já os devolve; o resto do código os descartava)."""
    modelos = []
    for m in _get(host, "/api/tags").get("models") or []:
        detalhes = m.get("details") or {}
        nome = m.get("name") or m.get("model")
        if not nome:
            continue
        modelos.append({
            "nome": nome, "tamanho_bytes": m.get("size"), "digest": m.get("digest") or "",
            "familia": detalhes.get("family") or "", "parametros": detalhes.get("parameter_size") or "",
            "quantizacao": detalhes.get("quantization_level") or "", "modificado_em": m.get("modified_at") or "",
        })
    return modelos


def ps(host: str | None = None) -> list[dict]:
    """Modelos **carregados em memória** agora (`/api/ps`)."""
    return [
        {"nome": m.get("name") or m.get("model"), "tamanho_bytes": m.get("size"),
         "tamanho_vram_bytes": m.get("size_vram"), "expira_em": m.get("expires_at") or ""}
        for m in _get(host, "/api/ps").get("models") or [] if m.get("name") or m.get("model")
    ]


def versao(host: str | None = None) -> str:
    return str(_get(host, "/api/version").get("version") or "")


def show(nome: str, host: str | None = None) -> dict:
    nome = validar_nome_modelo(nome)
    try:
        resposta = requests.post(_base(host) + "/api/show", json={"model": nome}, timeout=(TIMEOUT_CONEXAO_S, TIMEOUT_CONSULTA_S))
    except requests.RequestException as exc:
        raise OllamaIndisponivel(f"Ollama inacessível ({type(exc).__name__})") from None
    if resposta.status_code == 404:
        raise ModeloNaoEncontrado(nome)
    if resposta.status_code != 200:
        raise ErroOllama(f"/api/show respondeu HTTP {resposta.status_code}")
    return resposta.json() if resposta.content else {}


def _classificar_erro(mensagem: str, nome: str) -> ErroOllama:
    baixo = (mensagem or "").lower()
    if "no space" in baixo or "disk full" in baixo or "not enough space" in baixo:
        return DiscoCheio(mensagem)
    if "not found" in baixo or "does not exist" in baixo or "file does not exist" in baixo or "pull model manifest" in baixo:
        return ModeloNaoEncontrado(nome)
    return ErroOllama(mensagem or "erro desconhecido do Ollama")


def pull_stream(nome: str, host: str | None = None) -> Iterator[dict]:
    """Instala o modelo e **gera** os eventos de progresso (`status`, `digest`, `total`, `completed`).

    Termina quando o Ollama manda `{"status": "success"}`. Levanta `ModeloNaoEncontrado`, `DiscoCheio`,
    `ErroOllama` (erro dito pelo Ollama) ou `OllamaIndisponivel` (conexão cortada). Linhas que não são JSON
    são ignoradas. Quem consome pode parar de iterar a qualquer momento (cancelamento): a conexão é fechada.
    """
    nome = validar_nome_modelo(nome)
    try:
        resposta = requests.post(_base(host) + "/api/pull", json={"model": nome, "stream": True},
                                 stream=True, timeout=(TIMEOUT_CONEXAO_S, TIMEOUT_LEITURA_S))
    except requests.RequestException as exc:
        raise OllamaIndisponivel(f"Ollama inacessível ({type(exc).__name__})") from None
    try:
        if resposta.status_code == 404:
            raise ModeloNaoEncontrado(nome)
        if resposta.status_code != 200:
            raise ErroOllama(f"/api/pull respondeu HTTP {resposta.status_code}")
        concluiu = False
        try:
            for linha in resposta.iter_lines():
                if not linha:
                    continue
                try:
                    evento = json.loads(linha)
                except ValueError:
                    logger.debug("linha de pull ignorada (não é JSON)")
                    continue
                if not isinstance(evento, dict):
                    continue
                if evento.get("error"):
                    raise _classificar_erro(str(evento["error"]), nome)
                concluiu = concluiu or evento.get("status") == "success"
                yield evento
        except requests.RequestException as exc:
            raise OllamaIndisponivel(f"conexão com o Ollama interrompida ({type(exc).__name__})") from None
        if not concluiu:
            raise OllamaIndisponivel("o Ollama encerrou o download sem confirmar o sucesso")
    finally:
        resposta.close()


def delete(nome: str, host: str | None = None) -> None:
    """Remove o modelo. `ModeloNaoEncontrado` se ele não existe."""
    nome = validar_nome_modelo(nome)
    try:
        # `model` é o campo atual; `name` é o legado — mandar os dois cobre versões antigas.
        resposta = requests.delete(_base(host) + "/api/delete", json={"model": nome, "name": nome},
                                   timeout=(TIMEOUT_CONEXAO_S, TIMEOUT_CONSULTA_S))
    except requests.RequestException as exc:
        raise OllamaIndisponivel(f"Ollama inacessível ({type(exc).__name__})") from None
    if resposta.status_code == 404:
        raise ModeloNaoEncontrado(nome)
    if resposta.status_code != 200:
        raise ErroOllama(f"/api/delete respondeu HTTP {resposta.status_code}")

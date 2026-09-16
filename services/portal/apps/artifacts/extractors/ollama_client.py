"""Cliente HTTP para a API REST do Ollama (nativo no host, fora do Docker).

Ollama roda como um processo comum na máquina do usuário — os containers
alcançam via settings.OLLAMA_HOST (default http://host.docker.internal:11434,
que no Linux exige `extra_hosts: host-gateway` no docker-compose).
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class OllamaIndisponivel(Exception):
    """Ollama não respondeu (offline, timeout, erro de rede, status inesperado)."""


def _host() -> str:
    return getattr(settings, "OLLAMA_HOST", "http://host.docker.internal:11434")


def listar_modelos(timeout: float = 3.0) -> list[str]:
    """GET {OLLAMA_HOST}/api/tags — nomes dos modelos já baixados no host.

    Nunca levanta: usado para popular a UI, que precisa degradar graciosamente
    (lista vazia) se o Ollama estiver offline, em vez de quebrar a página.
    """
    try:
        resp = requests.get(f"{_host()}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except requests.RequestException:
        logger.info("listar_modelos: Ollama indisponível em %s", _host())
        return []
    except (ValueError, KeyError, TypeError):
        logger.exception("listar_modelos: resposta inesperada do Ollama")
        return []


def gerar(model: str, system: str, prompt: str, timeout: float | None = None) -> str:
    """POST {OLLAMA_HOST}/api/chat — retorna o texto da resposta.

    Levanta OllamaIndisponivel em qualquer falha de rede/timeout/status —
    quem chama decide se isso vira status=falhou.
    """
    timeout = timeout or getattr(settings, "OLLAMA_TIMEOUT_S", 120)
    num_ctx = getattr(settings, "OLLAMA_NUM_CTX", 4096)
    try:
        resp = requests.post(
            f"{_host()}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"num_ctx": num_ctx},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]
    except requests.RequestException as exc:
        raise OllamaIndisponivel(f"Ollama indisponível em {_host()}: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise OllamaIndisponivel(f"resposta inesperada do Ollama: {exc}") from exc

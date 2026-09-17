"""Cliente HTTP para a API REST do Ollama (nativo no host, fora do Docker).

Ollama roda como um processo comum na máquina do usuário — os containers
alcançam via settings.OLLAMA_HOST (default http://host.docker.internal:11434,
que no Linux exige `extra_hosts: host-gateway` no docker-compose) quando
nenhum `host` explícito é passado. Com várias máquinas no cluster
(apps.cluster), `apps.cluster.llm_router.escolher_execucao()` decide qual
`host`/`num_thread` usar para cada chamada — este módulo só sabe FALAR com
UM Ollama por vez, nunca escolhe qual.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class OllamaIndisponivel(Exception):
    """Ollama não respondeu (offline, timeout, erro de rede, status inesperado)."""


def _host(host: str | None = None) -> str:
    return host or getattr(settings, "OLLAMA_HOST", "http://host.docker.internal:11434")


def listar_modelos(host: str | None = None, timeout: float = 3.0) -> list[str]:
    """GET {host}/api/tags — nomes dos modelos já baixados nesse Ollama.

    Nunca levanta: usado para popular a UI e o heartbeat de máquina
    (apps.cluster.tasks), que precisam degradar graciosamente (lista vazia)
    se o Ollama estiver offline, em vez de quebrar a página/o heartbeat.
    """
    alvo = _host(host)
    try:
        resp = requests.get(f"{alvo}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except requests.RequestException:
        logger.info("listar_modelos: Ollama indisponível em %s", alvo)
        return []
    except (ValueError, KeyError, TypeError):
        logger.exception("listar_modelos: resposta inesperada do Ollama")
        return []


def _chars_enviados(messages: list[dict]) -> int:
    return sum(len(m.get("content", "") or "") for m in messages)


def _chamar(
    model: str, messages: list[dict], *,
    host: str | None = None, num_thread: int | None = None, num_ctx: int | None = None,
    maquina_id=None, timeout: float | None = None, extra_options: dict | None = None,
    finalidade: str | None = None, subject_id=None, tenant_id=None,
) -> dict:
    """POST {host}/api/chat — devolve a resposta crua do Ollama (não só o
    texto), porque quem chama pode precisar do payload inteiro (gateway
    compatível com OpenAI, `apps.cluster.gateway`).

    Sempre registra a chamada via `apps.events.llm_telemetria` — sucesso ou
    falha, com ou sem `maquina_id` (instalação single-machine sem cluster
    também é telemetrada agora; antes, sem `maquina_id`, não gerava nenhum
    registro). `finalidade`/`subject_id`/`tenant_id` default para o caso do
    gateway externo, cujas chamadas não têm um artefato por trás.

    Levanta OllamaIndisponivel em qualquer falha de rede/timeout/status —
    quem chama decide se isso vira status=falhou.
    """
    from time import perf_counter

    from apps.events.llm_telemetria import ResultadoLLM, registrar_chamada_llm
    from apps.events.models import Finalidade

    finalidade = finalidade or Finalidade.GATEWAY_EXTERNO

    alvo = _host(host)
    timeout = timeout or getattr(settings, "OLLAMA_TIMEOUT_S", 120)
    num_ctx = num_ctx or getattr(settings, "OLLAMA_NUM_CTX", 4096)
    # num_thread vai em toda chamada, para qualquer modelo — não depende de o
    # modelo escolhido ter sido criado com um Modelfile que já fixe isso (ver
    # OLLAMA_NUM_THREAD em settings/base.py e infra/ollama/Modelfile-qwen-max).
    num_thread = num_thread or getattr(settings, "OLLAMA_NUM_THREAD", None)
    options = {"num_ctx": num_ctx, **(extra_options or {})}
    if num_thread:
        options["num_thread"] = num_thread

    chars_enviados = _chars_enviados(messages)
    t0 = perf_counter()
    try:
        resp = requests.post(
            f"{alvo}/api/chat",
            json={"model": model, "messages": messages, "stream": False, "options": options},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "message" not in data:
            raise KeyError("message")
    except requests.RequestException as exc:
        erro = OllamaIndisponivel(f"Ollama indisponível em {alvo}: {exc}")
    except (ValueError, KeyError, TypeError) as exc:
        erro = OllamaIndisponivel(f"resposta inesperada do Ollama: {exc}")
    else:
        erro = None

    duration_ms = int((perf_counter() - t0) * 1000)

    if erro is not None:
        registrar_chamada_llm(
            finalidade=finalidade, duration_ms=duration_ms, maquina_id=maquina_id,
            subject_id=subject_id, tenant_id=tenant_id,
            resultado=ResultadoLLM(
                provider="ollama", modelo_solicitado=model, sucesso=False,
                num_thread=num_thread, num_ctx=num_ctx, chars_enviados=chars_enviados,
                error_message=str(erro),
            ),
        )
        raise erro

    registrar_chamada_llm(
        finalidade=finalidade, duration_ms=duration_ms, maquina_id=maquina_id,
        subject_id=subject_id, tenant_id=tenant_id,
        resultado=ResultadoLLM(
            provider="ollama", modelo_solicitado=model, sucesso=True,
            modelo_resposta=data.get("model", model),
            tokens_entrada=data.get("prompt_eval_count"),
            tokens_saida=data.get("eval_count"),
            stop_reason=data.get("done_reason", ""),
            eval_duration_ns=data.get("eval_duration"),
            num_thread=num_thread, num_ctx=num_ctx, chars_enviados=chars_enviados,
        ),
    )
    return data


def gerar_chat(
    model: str, messages: list[dict], *,
    host: str | None = None, num_thread: int | None = None, num_ctx: int | None = None,
    maquina_id=None, timeout: float | None = None, extra_options: dict | None = None,
    finalidade: str | None = None, subject_id=None, tenant_id=None,
) -> dict:
    """Como `_chamar`, mas nome público — usado pelo gateway compatível com
    OpenAI (`apps.cluster.gateway`), que precisa da resposta completa
    (mensagens multi-turno, contagem de tokens), não só do texto."""
    return _chamar(
        model, messages, host=host, num_thread=num_thread, num_ctx=num_ctx,
        maquina_id=maquina_id, timeout=timeout, extra_options=extra_options,
        finalidade=finalidade, subject_id=subject_id, tenant_id=tenant_id,
    )


def gerar(
    model: str, system: str, prompt: str, *,
    host: str | None = None, num_thread: int | None = None,
    maquina_id=None, timeout: float | None = None,
    finalidade: str | None = None, subject_id=None, tenant_id=None,
) -> str:
    """Wrapper fino de `_chamar` para o caso comum (um system + um prompt,
    só o texto da resposta) — mantém a assinatura que
    `llm_common.py`/`estruturacao_manual.py`/`comparador.py` já usam."""
    data = _chamar(
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        host=host, num_thread=num_thread, maquina_id=maquina_id, timeout=timeout,
        finalidade=finalidade, subject_id=subject_id, tenant_id=tenant_id,
    )
    return data["message"]["content"]

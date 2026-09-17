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


def _emitir_telemetria(maquina_id, model: str, num_thread, num_ctx, resposta: dict) -> None:
    """Registra tokens/segundo desta chamada para `apps.cluster` aprender qual
    máquina é mais rápida para qual modelo — ver
    apps.cluster.projecao.aplicar_metrica_llm.

    Só roda quando `maquina_id` é informado (o roteador sempre informa; uma
    chamada local sem cluster configurado não tem o que atribuir). Nunca
    levanta — telemetria não pode derrubar uma geração que já funcionou.
    """
    if not maquina_id:
        return
    try:
        eval_count = resposta.get("eval_count")
        eval_duration_ns = resposta.get("eval_duration")
        if not eval_count or not eval_duration_ns:
            return
        tokens_por_segundo = eval_count / (eval_duration_ns / 1e9)

        from apps.cluster.models import Maquina
        from apps.cluster.projecao import aplicar_metrica_llm
        from apps.events.emit import emit

        # tenant_id explícito, não herdado do contexto ambiente (get_tenant_id()):
        # esta chamada pode acontecer fora de qualquer captura em andamento, e o
        # evento é sobre a organização DESTA MÁQUINA, não sobre o que estiver no
        # contextvar no momento.
        organizacao_id = Maquina.objects.filter(id=maquina_id).values_list("organizacao_id", flat=True).first()

        evento = emit(
            "llm.chamada_ollama", "ok",
            subject_type="maquina", subject_id=maquina_id, tenant_id=organizacao_id,
            payload={"modelo": model, "tokens_por_segundo": round(tokens_por_segundo, 2),
                     "num_thread": num_thread, "num_ctx": num_ctx},
        )
        if evento is not None:
            aplicar_metrica_llm(evento)
    except Exception:
        logger.exception("falha ao registrar telemetria de chamada Ollama — modelo=%s", model)


def _chamar(
    model: str, messages: list[dict], *,
    host: str | None = None, num_thread: int | None = None, num_ctx: int | None = None,
    maquina_id=None, timeout: float | None = None, extra_options: dict | None = None,
) -> dict:
    """POST {host}/api/chat — devolve a resposta crua do Ollama (não só o
    texto), porque quem chama pode precisar de `eval_count`/`eval_duration`
    (telemetria, ver `_emitir_telemetria`) ou do payload inteiro (gateway
    compatível com OpenAI, `apps.cluster.gateway`).

    Levanta OllamaIndisponivel em qualquer falha de rede/timeout/status —
    quem chama decide se isso vira status=falhou.
    """
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
        raise OllamaIndisponivel(f"Ollama indisponível em {alvo}: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise OllamaIndisponivel(f"resposta inesperada do Ollama: {exc}") from exc

    _emitir_telemetria(maquina_id, model, num_thread, num_ctx, data)
    return data


def gerar_chat(
    model: str, messages: list[dict], *,
    host: str | None = None, num_thread: int | None = None, num_ctx: int | None = None,
    maquina_id=None, timeout: float | None = None, extra_options: dict | None = None,
) -> dict:
    """Como `_chamar`, mas nome público — usado pelo gateway compatível com
    OpenAI (`apps.cluster.gateway`), que precisa da resposta completa
    (mensagens multi-turno, contagem de tokens), não só do texto."""
    return _chamar(
        model, messages, host=host, num_thread=num_thread, num_ctx=num_ctx,
        maquina_id=maquina_id, timeout=timeout, extra_options=extra_options,
    )


def gerar(
    model: str, system: str, prompt: str, *,
    host: str | None = None, num_thread: int | None = None,
    maquina_id=None, timeout: float | None = None,
) -> str:
    """Wrapper fino de `_chamar` para o caso comum (um system + um prompt,
    só o texto da resposta) — mantém a assinatura que
    `llm_common.py`/`estruturacao_manual.py`/`comparador.py` já usam."""
    data = _chamar(
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        host=host, num_thread=num_thread, maquina_id=maquina_id, timeout=timeout,
    )
    return data["message"]["content"]

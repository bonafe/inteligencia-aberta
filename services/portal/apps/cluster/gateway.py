"""Gateway compatível com a API de chat da OpenAI — só fala com Ollama das
máquinas do próprio cluster, nunca com um provider externo.

Existe pra outras ferramentas (Langflow, bibliotecas cliente OpenAI-
compatíveis) apontarem `base_url=".../v1"` pro cluster inteiro sem saber que
há mais de uma máquina por trás. Não é um gateway de LLM genérico: aceitar
"provider" e rotear para Anthropic aqui reabriria, fora do
`policy_engine.py`, exatamente a decisão local-vs-externo que ele já toma —
por isso este gateway não tem esse conceito.

Sem streaming nesta versão — todo pedido vira uma chamada `stream: false` ao
Ollama e uma resposta única.
"""

import json
import logging
import time
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.artifacts.extractors.ollama_client import OllamaIndisponivel, gerar_chat

from .llm_router import escolher_execucao

logger = logging.getLogger(__name__)


def _autorizado(request) -> bool:
    esperado = getattr(settings, "LLM_GATEWAY_TOKEN", "")
    if not esperado:
        return False
    auth = request.headers.get("Authorization", "")
    recebido = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    return constant_time_compare(recebido, esperado)


def _resposta_openai(model: str, resposta_ollama: dict) -> dict:
    conteudo = resposta_ollama.get("message", {}).get("content", "")
    prompt_tokens = resposta_ollama.get("prompt_eval_count") or 0
    completion_tokens = resposta_ollama.get("eval_count") or 0
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": conteudo},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@method_decorator(csrf_exempt, name="dispatch")
class ChatCompletionsView(View):
    """`POST /v1/chat/completions` — sem `LLM_GATEWAY_TOKEN` configurado,
    o gateway nem existe (404), por padrão."""

    def post(self, request):
        if not getattr(settings, "LLM_GATEWAY_TOKEN", ""):
            return JsonResponse({"error": "gateway desabilitado"}, status=404)
        if not _autorizado(request):
            logger.warning("gateway LLM recusado — Authorization ausente ou inválida")
            return JsonResponse({"error": "Não autorizado"}, status=401)

        try:
            corpo = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        model = corpo.get("model")
        messages = corpo.get("messages")
        if not model or not isinstance(messages, list) or not messages:
            return JsonResponse({"error": "'model' e 'messages' são obrigatórios"}, status=400)

        extra_options = {}
        if "temperature" in corpo:
            extra_options["temperature"] = corpo["temperature"]
        if "top_p" in corpo:
            extra_options["top_p"] = corpo["top_p"]

        execucao = escolher_execucao(model)
        kwargs = (
            {"host": execucao.host, "num_thread": execucao.num_thread, "maquina_id": execucao.maquina_id}
            if execucao else {}
        )

        try:
            resposta = gerar_chat(model, messages, extra_options=extra_options, **kwargs)
        except OllamaIndisponivel as exc:
            return JsonResponse({"error": str(exc)}, status=503)

        return JsonResponse(_resposta_openai(model, resposta))

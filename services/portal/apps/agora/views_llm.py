"""Chat com os modelos do cluster, para o componente `ia-llm-chat` do Ultima Agora.

Duas rotas sob `/agora/api/v1/dominio/llm/`, ambas com a sessão da pessoa e isoladas por
organização (as mesmas regras de `views_dominio`):

- `GET modelos/`: o que dá para usar agora, agrupado por modelo, só de máquinas das organizações
  da pessoa que estão ativas, confirmadas, online e com o modelo visto há pouco (mesma régua do
  roteador de LLM);
- `POST chat/`: uma rodada de conversa (sem streaming). O histórico vive no navegador e vem inteiro
  a cada chamada; nada é guardado aqui.

Só fala com o Ollama de máquinas do próprio cluster, nunca com provedor externo. O prompt é **só o
que a pessoa digitou** (nenhum artefato é injetado), por isso esta rota não passa pelo
`policy_engine`; quando o chat ganhar contexto de artefatos, ele terá de passar (ADR 013/016).
"""

import json
import logging
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.views import View

from apps.accounts.views import orgs_do_usuario
from apps.artifacts.extractors.ollama_client import OllamaIndisponivel, gerar_chat
from apps.artifacts.models import AuditLog
from apps.cluster.llm_router import STALENESS_MODELO_S, execucao_para
from apps.cluster.models import Maquina, MaquinaModeloOllama
from apps.cluster.tasks import HEARTBEAT_INTERVALO_S, HEARTBEAT_TOLERANCIA
from apps.events.models import Finalidade

logger = logging.getLogger(__name__)

PAPEIS = {"system", "user", "assistant"}
MAX_MENSAGENS = 50
MAX_CARACTERES_MENSAGEM = 8000
MAX_CARACTERES_TOTAL = 40000
MAX_NOME_MODELO = 200


def _erro(mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"error": mensagem}, status=status)


def _disponiveis(request):
    """`MaquinaModeloOllama` usáveis agora pela pessoa (consulta já filtrada por organização)."""
    agora = timezone.now()
    limite = agora - timezone.timedelta(seconds=STALENESS_MODELO_S)
    # mesma régua de `MaquinaStatus.online` (recência do heartbeat), expressa como filtro de banco
    heartbeat_minimo = agora - timezone.timedelta(seconds=HEARTBEAT_INTERVALO_S * HEARTBEAT_TOLERANCIA)
    return (
        MaquinaModeloOllama.objects.filter(
            maquina__organizacao__in=orgs_do_usuario(request.user),
            maquina__ativa=True, maquina__estado=Maquina.Estado.CONFIRMADO,
            maquina__status__ultimo_heartbeat_em__gte=heartbeat_minimo, visto_pela_ultima_vez__gte=limite,
        )
        .exclude(maquina__status__ollama_disponivel=False)
        .exclude(maquina__ollama_endpoint="", maquina__gateway_endpoint="")
        .select_related("maquina", "maquina__status")
    )


class ModelosView(View):
    """GET → `{"modelos": [{"modelo", "maquinas": [{"id", "apelido", "tokens_por_segundo"}]}]}`."""

    def get(self, request):
        agrupado: dict[str, list] = {}
        for item in _disponiveis(request).order_by("nome_modelo", "maquina__apelido"):
            agrupado.setdefault(item.nome_modelo, []).append({
                "id": str(item.maquina_id), "apelido": item.maquina.apelido,
                "tokens_por_segundo": round(item.tokens_por_segundo_medio, 1) if item.amostras_n and item.tokens_por_segundo_medio else None,
            })
        return JsonResponse({"modelos": [{"modelo": m, "maquinas": ms} for m, ms in agrupado.items()]})


def _validar_mensagens(bruto):
    if not isinstance(bruto, list) or not 1 <= len(bruto) <= MAX_MENSAGENS:
        raise ValueError(f"'mensagens' deve ter de 1 a {MAX_MENSAGENS} itens")
    mensagens, total = [], 0
    for item in bruto:
        if not isinstance(item, dict) or item.get("role") not in PAPEIS or not isinstance(item.get("content"), str):
            raise ValueError("cada mensagem precisa de 'role' (system|user|assistant) e 'content' em texto")
        conteudo = item["content"]
        if len(conteudo) > MAX_CARACTERES_MENSAGEM:
            raise ValueError(f"mensagem maior que {MAX_CARACTERES_MENSAGEM} caracteres")
        total += len(conteudo)
        mensagens.append({"role": item["role"], "content": conteudo})
    if total > MAX_CARACTERES_TOTAL:
        raise ValueError(f"conversa maior que {MAX_CARACTERES_TOTAL} caracteres")
    if mensagens[-1]["role"] != "user" or not mensagens[-1]["content"].strip():
        raise ValueError("a última mensagem deve ser do usuário e não pode estar vazia")
    return mensagens


class ChatView(View):
    """POST `{"modelo", "mensagens": [{"role","content"}], "maquina"?: id}` → a resposta e quem a deu."""

    def post(self, request):
        try:
            corpo = json.loads(request.body or b"{}")
            if not isinstance(corpo, dict):
                raise ValueError("corpo deve ser um objeto JSON")
            modelo = corpo.get("modelo")
            if not isinstance(modelo, str) or not modelo.strip() or len(modelo) > MAX_NOME_MODELO:
                raise ValueError("'modelo' é obrigatório")
            mensagens = _validar_mensagens(corpo.get("mensagens"))
            maquina_id = corpo.get("maquina")
            if maquina_id is not None:
                try:
                    maquina_id = str(uuid.UUID(str(maquina_id)))
                except ValueError:
                    raise ValueError("'maquina' deve ser o id da máquina") from None
        except (ValueError, json.JSONDecodeError) as exc:
            return _erro(str(exc), 400)

        candidatas = _disponiveis(request).filter(nome_modelo=modelo)
        if maquina_id:
            candidatas = candidatas.filter(maquina_id=maquina_id)
        if not candidatas.exists():
            return _erro("modelo indisponível: nenhuma máquina online com ele", 404)

        if maquina_id:
            item = candidatas.first()
            maquina, execucao = item.maquina, execucao_para(item.maquina)
        else:
            from apps.cluster.llm_router import escolher_execucao

            execucao = escolher_execucao(modelo)
            maquina = next((c.maquina for c in candidatas if str(c.maquina_id) == getattr(execucao, "maquina_id", None)), None)
            if execucao is None or maquina is None:   # o roteador pode escolher uma máquina fora das organizações da pessoa
                item = candidatas.first()
                maquina, execucao = item.maquina, execucao_para(item.maquina)

        kwargs = {"host": execucao.host, "num_thread": execucao.num_thread, "maquina_id": execucao.maquina_id,
                  **({"gateway": execucao.gateway} if execucao.gateway else {})}
        try:
            resposta = gerar_chat(modelo, mensagens, finalidade=Finalidade.CHAT, tenant_id=maquina.organizacao_id, **kwargs)
        except OllamaIndisponivel as exc:
            logger.warning("chat do Agora: %s", exc)
            # 424 e não 502/503/504: o `api()` do host trata esses como "sem conexão com o servidor"
            return _erro("a máquina não respondeu; tente de novo ou escolha outra", 424)

        self._auditar(request, maquina, modelo)
        entrada, saida = resposta.get("prompt_eval_count"), resposta.get("eval_count")
        duracao = resposta.get("eval_duration")
        return JsonResponse({
            "conteudo": (resposta.get("message") or {}).get("content", ""),
            "modelo": modelo,
            "maquina": {"id": str(maquina.id), "apelido": maquina.apelido},
            "tokens": {"entrada": entrada, "saida": saida},
            "tokens_por_segundo": round(saida / (duracao / 1e9), 1) if saida and duracao else None,
        })

    @staticmethod
    def _auditar(request, maquina, modelo):
        try:
            AuditLog.objects.create(
                organization=maquina.organizacao, user=request.user, operation="llm.chat", outcome="permitido",
                metadata={"modelo": modelo, "maquina": str(maquina.id)},   # nunca o conteúdo da conversa
            )
        except Exception:
            logger.exception("falha ao gravar auditoria do chat")

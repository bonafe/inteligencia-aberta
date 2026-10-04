"""Tela do cluster: inventário e controle dos modelos do Ollama de uma máquina (local ou par).

- **Ver** (inventário, operações): qualquer membro da organização da máquina.
- **Instalar, remover, cancelar**: só dono/administrador — o servidor confere sempre; o botão desabilitado
  na tela é só cortesia. Máquina de **outra organização é 404**. Máquina **offline falha na hora** (503).
- Respostas em JSON; erros de regra de negócio viram `{"erro": ...}` com o status certo (409 conflito,
  503 offline, 403 sem permissão, 400 pedido inválido).

`avaliar_acoes` é a **única** fonte de verdade de "esta máquina aceita ações agora, e por que não": a tela
desabilita os botões com o motivo que ela devolve, e as views recusam pelo mesmo critério.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.accounts.permissoes import eh_admin
from apps.accounts.views import orgs_do_usuario

from . import operacoes
from .models import Maquina, OperacaoModeloOllama as Op

#: Quantas operações recentes (além das ativas) a tela mostra.
OPERACOES_RECENTES = 12


def estado_do_ollama(maquina: Maquina) -> str:
    """`ok`, `indisponivel`, `nao_configurado` ou `desconhecido`."""
    status = getattr(maquina, "status", None)
    if status is not None and status.ollama_disponivel is True:
        return "ok"
    if status is not None and status.ollama_disponivel is False:
        return "indisponivel"
    if maquina.eh_local and not getattr(settings, "OLLAMA_HOST", ""):
        return "nao_configurado"
    if not maquina.eh_local and (maquina.capacidades_json or {}).get("ollama_configurado") is False:
        return "nao_configurado"                                  # o par disse que não tem Ollama
    return "desconhecido"


def avaliar_acoes(maquina: Maquina, usuario) -> tuple[bool, str]:
    """`(habilitado, motivo)`: o usuário pode mandar instalar/remover nesta máquina agora?"""
    if not eh_admin(usuario, maquina.organizacao):
        return False, "Requer papel de dono ou administrador da organização."
    if not maquina.eh_local:
        if maquina.tipo != Maquina.Tipo.PROPRIO or maquina.estado != Maquina.Estado.CONFIRMADO or not maquina.ativa:
            return False, "Só é possível controlar pares próprios e confirmados."
        if not maquina.esta_online:
            return False, "Máquina offline."
    estado = estado_do_ollama(maquina)
    if estado == "nao_configurado":
        return False, "O Ollama não está configurado nesta máquina."
    if estado == "indisponivel":
        return False, "O Ollama desta máquina não está respondendo."
    return True, ""


def _maquina_do_usuario(request, maquina_id) -> Maquina:
    return get_object_or_404(
        Maquina.objects.select_related("organizacao", "status"),
        pk=maquina_id, organizacao__in=orgs_do_usuario(request.user),
    )


def _erro(mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": mensagem}, status=status)


def inventario(maquina: Maquina, usuario) -> dict:
    status = getattr(maquina, "status", None)
    habilitado, motivo = avaliar_acoes(maquina, usuario)
    modelos = []
    for m in maquina.modelos_ollama.order_by("nome_modelo"):
        modelos.append({
            "nome": m.nome_modelo, "tamanho_bytes": m.tamanho_bytes, "familia": m.familia, "parametros": m.parametros,
            "quantizacao": m.quantizacao, "carregado": m.carregado, "tokens_por_segundo": m.tokens_por_segundo_medio,
            "avisos_remocao": operacoes.avisos_remocao(maquina, m.nome_modelo),
        })
    ativas = list(maquina.operacoes_modelo.filter(status__in=Op.ATIVOS).order_by("-criada_em"))
    recentes = list(maquina.operacoes_modelo.exclude(status__in=Op.ATIVOS).order_by("-criada_em")[:OPERACOES_RECENTES])
    return {
        "maquina": {
            "id": str(maquina.id), "apelido": maquina.apelido, "local": maquina.eh_local, "online": maquina.esta_online,
            "ollama": estado_do_ollama(maquina), "ollama_versao": status.ollama_versao if status else "",
            "disco_ollama_livre_gb": status.disco_ollama_livre_gb if status else None,
            "ultimo_estado_em": status.ultimo_heartbeat_em.isoformat() if status and status.ultimo_heartbeat_em else None,
            "ultimo_erro": maquina.ultimo_pull_erro if not maquina.eh_local else "",
            "acoes_habilitadas": habilitado, "motivo_desabilitado": motivo,
        },
        "modelos": modelos,
        "operacoes": [operacoes.serializar(o) for o in ativas + recentes],
    }


class InventarioView(View):
    def get(self, request, maquina_id):
        return JsonResponse(inventario(_maquina_do_usuario(request, maquina_id), request.user))


class _Acao(View):
    """Base das ações: converte as exceções do serviço em JSON com o status certo."""

    def _executar(self, fn):
        try:
            return fn()
        except PermissionDenied:
            return _erro("Requer papel de dono ou administrador da organização.", 403)
        except operacoes.OperacaoRecusada as exc:
            return _erro(exc.mensagem, exc.http)


class InstalarView(_Acao):
    def post(self, request, maquina_id):
        maquina = _maquina_do_usuario(request, maquina_id)
        modelo = request.POST.get("modelo", "")
        return self._executar(lambda: JsonResponse(
            {"operacao": operacoes.serializar(operacoes.solicitar(maquina, Op.Tipo.PULL, modelo, usuario=request.user))}, status=202))


class RemoverView(_Acao):
    def post(self, request, maquina_id):
        maquina = _maquina_do_usuario(request, maquina_id)
        modelo = request.POST.get("modelo", "")
        return self._executar(lambda: JsonResponse(
            {"operacao": operacoes.serializar(operacoes.solicitar(maquina, Op.Tipo.DELETE, modelo, usuario=request.user))}, status=202))


def _operacao_do_usuario(request, operacao_id) -> Op:
    return get_object_or_404(
        Op.objects.select_related("maquina", "maquina__organizacao"),
        pk=operacao_id, maquina__organizacao__in=orgs_do_usuario(request.user),
    )


class OperacaoView(View):
    def get(self, request, operacao_id):
        return JsonResponse({"operacao": operacoes.serializar(_operacao_do_usuario(request, operacao_id))})


class CancelarOperacaoView(_Acao):
    def post(self, request, operacao_id):
        op = _operacao_do_usuario(request, operacao_id)
        return self._executar(lambda: JsonResponse({"operacao": operacoes.serializar(operacoes.cancelar(op, usuario=request.user))}))

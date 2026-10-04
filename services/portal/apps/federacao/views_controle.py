"""Canal de controle entre instâncias — `/federacao/controle/v1/` (ADR 011).

Autenticado **só pela assinatura** da instância de origem (`apps.federacao.canal`). Depois da
assinatura, a ordem é: o DID é de um **par `confirmado` e ativo**? → limite de taxa →
a rota exige que tipo de par? Cada camada falha de um jeito deliberado:

- assinatura ruim, DID desconhecido, par pendente ou revogado → **o mesmo 404** (não
  revela se o DID existe nem em que estado está);
- par **autenticado** sem permissão para a rota → `403` (ele já é conhecido);
- passou do limite → `429`.

O motivo de cada recusa vai só para o log local (`PipelineEvent` `federacao.canal`, sem
corpo). **Nenhuma rota é proxy para o Ollama**: o Ollama continua sem autenticação e fechado.
Toda resposta de sucesso é **assinada** e presa ao nonce da requisição.
"""

import json
import logging
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.cluster.estado import montar_estado_local
from apps.cluster.models import Maquina
from apps.events.emit import emit

from . import canal
from .chaves import chave_ativa

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_CORPO = 16384
NAO_ENCONTRADO = {"erro": "não encontrado"}
#: Tipos que cada rota aceita.
QUALQUER_PAR = (Maquina.Tipo.PROPRIO, Maquina.Tipo.TERCEIRO)
SO_PROPRIO = (Maquina.Tipo.PROPRIO,)


def _recusar(rota: str, codigo: str, status: int = 404, **extra) -> JsonResponse:
    logger.warning("canal de controle: %s recusado — %s", rota, codigo)
    emit("federacao.canal", "ignorado", source="portal", message="requisição de outra instância recusada",
         payload={"rota": rota, "codigo": codigo, **extra})
    mensagens = {403: "não autorizado", 405: "método não permitido", 429: "muitas requisições", 400: "requisição inválida"}
    return JsonResponse(NAO_ENCONTRADO if status == 404 else {"erro": mensagens.get(status, "recusado")}, status=status)


def _dentro_do_limite(did: str) -> bool:
    limite = int(getattr(settings, "FEDERACAO_LIMITE_POR_MINUTO", 120))
    chave = f"ia-rate:{did}"
    cache.add(chave, 0, timeout=60)
    try:
        return cache.incr(chave) <= limite
    except ValueError:  # a chave expirou entre o add e o incr
        cache.add(chave, 1, timeout=60)
        return True


def par_assinado(rota: str, *, tipos=QUALQUER_PAR, metodos=("GET",)):
    """Decorador: só deixa passar uma requisição assinada por um par confirmado de um dos `tipos`, num dos `metodos`.

    A view recebe `(request, par, corpo)`, onde `corpo` é o JSON já validado (dict, `{}` se não houver)
    e `par` é a `Maquina` que fez o pedido. Devolve dict (vira JSON **assinado**) ou um `HttpResponse`.
    """
    def decorador(view):
        @csrf_exempt
        @wraps(view)
        def envolvida(request, *args, **kwargs):
            chave = chave_ativa()
            if chave is None:
                return _recusar(rota, "sem_chave")
            bruto = request.body[: TAMANHO_MAXIMO_CORPO + 1]
            if len(bruto) > TAMANHO_MAXIMO_CORPO:
                return _recusar(rota, "corpo_grande")
            try:
                did_origem = canal.verificar_requisicao(
                    request.method, request.get_full_path(), request.headers, bruto, did_local=chave.did)
            except canal.AssinaturaInvalida as exc:
                return _recusar(rota, f"assinatura:{exc.codigo}")

            par = (Maquina.objects.filter(did=did_origem, estado=Maquina.Estado.CONFIRMADO, ativa=True, eh_local=False)
                   .select_related("organizacao").order_by("criada_em").first())
            if par is None:
                return _recusar(rota, "par_desconhecido_ou_nao_confirmado")
            if not _dentro_do_limite(did_origem):
                return _recusar(rota, "limite", 429, par=str(par.id))
            if par.tipo not in tipos:
                return _recusar(rota, "tipo_sem_permissao", 403, par=str(par.id), tipo=par.tipo)
            if request.method not in metodos:
                return _recusar(rota, "metodo", 405, par=str(par.id))

            try:
                corpo = json.loads(bruto) if bruto else {}
                if not isinstance(corpo, dict):
                    raise ValueError("corpo")
            except ValueError:
                return _recusar(rota, "corpo_invalido", 400, par=str(par.id))

            resultado = view(request, par, corpo, *args, **kwargs)
            if isinstance(resultado, HttpResponse):
                return resultado
            conteudo = json.dumps(resultado, separators=(",", ":"), default=str).encode()
            resposta = HttpResponse(conteudo, content_type="application/json")
            resposta[canal.H_ASSINATURA_RESPOSTA] = canal.assinar_resposta(conteudo, request.headers[canal.H_NONCE])
            return resposta
        return envolvida
    return decorador


@par_assinado("ping", tipos=QUALQUER_PAR)
def ping(request, par, corpo):
    """Qualquer par confirmado pode perguntar se esta instância está de pé. Não revela inventário."""
    return {"ok": True, "v": 1}


@par_assinado("estado", tipos=SO_PROPRIO)
def estado(request, par, corpo):
    """Inventário e recursos desta instância — só para pares **próprios** (expõe o que ela tem)."""
    return montar_estado_local()


# ── comandos sobre os modelos do Ollama (só par próprio, e só se o ator afirmado for dono/admin) ──

PAPEIS_ADMIN_AFIRMADOS = ("owner", "admin")


def _ator_administrador(corpo: dict) -> dict | None:
    """O ator que a origem afirma, se ele for dono/administrador. A origem **afirma**; este lado não verifica."""
    ator = corpo.get("ator")
    if not isinstance(ator, dict):
        return None
    usuario, papel = ator.get("user"), ator.get("papel")
    if not isinstance(usuario, str) or not usuario or len(usuario) > 64 or papel not in PAPEIS_ADMIN_AFIRMADOS:
        return None
    return {"user": usuario, "papel": papel, "org": str(ator.get("org") or "")[:64]}


def _maquina_local():
    return Maquina.objects.filter(eh_local=True).select_related("organizacao").first()


def _erro(mensagem: str, status: int) -> JsonResponse:
    return JsonResponse({"erro": mensagem[:200]}, status=status)


@par_assinado("ollama_solicitar", tipos=SO_PROPRIO, metodos=("POST",))
def ollama_solicitar(request, par, corpo):
    """`POST …/ollama/operacoes/` — instalar ou remover um modelo **desta** instância."""
    from apps.cluster import operacoes

    ator = _ator_administrador(corpo)
    if ator is None:
        return _recusar("ollama_solicitar", "ator_sem_papel", 403, par=str(par.id))
    local = _maquina_local()
    if local is None:
        return _erro("esta instância não tem máquina local cadastrada", 409)
    try:
        op = operacoes.solicitar(local, str(corpo.get("tipo") or ""), str(corpo.get("modelo") or ""),
                                 origem_par=par, ator=ator)
    except operacoes.OperacaoRecusada as exc:
        return _erro(exc.mensagem, exc.http)
    return operacoes.serializar(op)


def _operacao_do_par(par, op_id):
    from apps.cluster.models import OperacaoModeloOllama

    local = _maquina_local()
    if local is None:
        return None
    return OperacaoModeloOllama.objects.filter(pk=op_id, maquina=local, origem_par=par).select_related("maquina").first()


@par_assinado("ollama_operacao", tipos=SO_PROPRIO)
def ollama_operacao(request, par, corpo, op_id):
    """`GET …/ollama/operacoes/<id>/` — o estado de uma operação que **este par** pediu."""
    from apps.cluster import operacoes

    op = _operacao_do_par(par, op_id)
    if op is None:
        return _erro("operação não encontrada", 404)
    return operacoes.serializar(op)


@par_assinado("ollama_cancelar", tipos=SO_PROPRIO, metodos=("POST",))
def ollama_cancelar(request, par, corpo, op_id):
    """`POST …/ollama/operacoes/<id>/cancelar/` — cancela uma operação que este par pediu."""
    from apps.cluster import operacoes

    if _ator_administrador(corpo) is None:
        return _recusar("ollama_cancelar", "ator_sem_papel", 403, par=str(par.id))
    op = _operacao_do_par(par, op_id)
    if op is None:
        return _erro("operação não encontrada", 404)
    return operacoes.serializar(operacoes.cancelar(op, origem_par=par))

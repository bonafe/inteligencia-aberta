"""Criar e acompanhar pedidos de processamento do IPED — ADR 019."""

from django.utils import timezone

from apps.events.emit import emit

from . import caminhos
from .models import ProcessamentoIPED


class ErroProcessamento(ValueError):
    """Pedido inválido (caminho fora das raízes, perfil desconhecido...)."""


def solicitar(*, organizacao, usuario, raiz: str, relativo: str, perfil: str = "fastmode", despachar: bool = True):
    """Valida e cria o pedido; despacha para a fila `iped`. `ErroProcessamento` se inválido."""
    if perfil not in ProcessamentoIPED.PERFIS:
        raise ErroProcessamento(f"perfil desconhecido; use um de {', '.join(ProcessamentoIPED.PERFIS)}")
    try:
        alvo = caminhos.resolver(raiz, relativo)
    except caminhos.CaminhoNaoPermitido as exc:
        raise ErroProcessamento(str(exc)) from None
    pedido = ProcessamentoIPED.objects.create(
        organizacao=organizacao, criado_por=usuario, raiz=raiz,
        relativo=relativo, perfil=perfil,
    )
    emit("forense.iped.solicitado", "ok", source="portal", subject_type="processamento_iped",
         subject_id=pedido.id, tenant_id=organizacao.id,
         message=f"processamento IPED solicitado (perfil {perfil})",
         payload={"perfil": perfil, "relativo": relativo})
    if despachar:
        from .tasks import executar_processamento_iped

        res = executar_processamento_iped.delay(str(pedido.id))
        ProcessamentoIPED.objects.filter(pk=pedido.pk).update(celery_task_id=res.id or "")
    return pedido


def marcar(pedido, estado, **campos):
    """Atualiza o estado (e `concluido_em` nos estados finais) e emite o evento."""
    pedido.estado = estado
    if estado in (ProcessamentoIPED.Estado.CONCLUIDO, ProcessamentoIPED.Estado.FALHOU, ProcessamentoIPED.Estado.CANCELADO):
        pedido.concluido_em = timezone.now()
    for k, v in campos.items():
        setattr(pedido, k, v)
    pedido.save()
    emit(f"forense.iped.{estado}", {"concluido": "ok", "executando": "iniciado", "falhou": "falhou"}.get(estado, "ignorado"),
         source="worker", subject_type="processamento_iped", subject_id=pedido.id,
         tenant_id=pedido.organizacao_id, message=f"processamento IPED {estado}",
         payload={"codigo_saida": pedido.codigo_saida, "perfil": pedido.perfil})
    return pedido


def cancelar(pedido):
    """Pede o cancelamento: pendente cancela na hora; executando é interrompido pela task. Idempotente."""
    E = ProcessamentoIPED.Estado
    if pedido.estado in (E.PENDENTE, E.EXECUTANDO):
        marcar(pedido, E.CANCELADO)
    return pedido

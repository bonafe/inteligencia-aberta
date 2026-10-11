"""Task Celery do processamento IPED (fila `iped`, atendida só pelo `worker-iped`) — ADR 019."""

import logging
import time

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import fila
from .models import ProcessamentoIPED
from .servico import marcar

logger = logging.getLogger(__name__)

#: Intervalo de consulta ao estado gravado pelo runner (segundos).
INTERVALO_S = 3
#: Folga sobre o teto do IPED, para o runner terminar e gravar o estado.
FOLGA_S = 120


@shared_task(bind=True, max_retries=0)
def executar_processamento_iped(self, pedido_id: str):
    """Envia o pedido ao runner e acompanha até o fim. Bloqueia um slot do worker (concorrência 1)."""
    E = ProcessamentoIPED.Estado
    try:
        pedido = ProcessamentoIPED.objects.get(pk=pedido_id)
    except ProcessamentoIPED.DoesNotExist:
        return {"status": "ignorado", "motivo": "pedido inexistente"}
    if pedido.estado != E.PENDENTE:
        return {"status": "ignorado", "motivo": f"pedido {pedido.estado}"}

    try:
        fila.enviar(pedido.id, perfil=pedido.perfil, relativo=pedido.relativo)
    except (OSError, ValueError) as exc:
        marcar(pedido, E.FALHOU, erro=f"não foi possível enviar o pedido ao runner: {exc}"[:2000])
        return {"status": "falhou"}
    marcar(pedido, E.EXECUTANDO, iniciado_em=timezone.now())

    teto = settings.IPED_TIMEOUT_S
    limite = time.monotonic() + (teto + FOLGA_S if teto else float("inf"))
    cancelado_pedido = False
    while True:
        atual = fila.estado(pedido.id)
        if atual in fila.ESTADOS_FINAIS:
            break
        pedido.refresh_from_db(fields=["estado"])
        if pedido.estado == E.CANCELADO and not cancelado_pedido:
            fila.pedir_cancelamento(pedido.id)
            cancelado_pedido = True
        if time.monotonic() > limite:
            fila.pedir_cancelamento(pedido.id)
            marcar(pedido, E.FALHOU, erro="o runner não respondeu dentro do prazo",
                   codigo_saida=fila.codigo_de_saida(pedido.id))
            return {"status": "falhou", "motivo": "prazo"}
        time.sleep(INTERVALO_S)

    codigo = fila.codigo_de_saida(pedido.id)
    if atual == "concluido":
        marcar(pedido, E.CONCLUIDO, codigo_saida=codigo, resumo={"saida": str(pedido.id)})
    elif atual == "cancelado":
        marcar(pedido, E.CANCELADO, codigo_saida=codigo)
    else:
        marcar(pedido, E.FALHOU, codigo_saida=codigo, erro=fila.final_do_log(pedido.id)[-2000:])
    return {"status": atual, "codigo": codigo}

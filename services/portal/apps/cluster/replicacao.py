"""Decide o que sai no fluxo de replicação para um peer.

`eventos_para_peer` é o único lugar que uma fase futura precisa tocar para
restringir replicação por organização/classificação (ver
docs/seguranca/compartilhamento.md — compartilhamento entre organizações é
sempre explícito e nunca automático). Fase 1: todo peer registrado vê tudo,
porque todas as máquinas são do mesmo dono — não há gate nenhum aqui ainda,
só o corte por `desde`/`limite`.
"""

from .models import EventoReplicacao

LOTE_MAX = 500


def eventos_para_peer(peer, desde: int, limite: int = LOTE_MAX):
    """Eventos de replicação com `sequence > desde`, em ordem, para `peer`.

    `peer` (a `Maquina` que está puxando) não filtra nada na fase 1 — ele
    existe na assinatura porque é aqui, e só aqui, que a fase 2 vai inserir
    "só devolve o que a organização de `peer` pode receber".
    """
    return list(
        EventoReplicacao.objects.filter(sequence__gt=desde)
        .order_by("sequence")[:limite]
    )

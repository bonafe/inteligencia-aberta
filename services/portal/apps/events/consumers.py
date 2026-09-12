"""Canal ao vivo do painel de eventos.

Isolamento multi-tenant é feito **aqui**, na assinatura dos grupos: o consumer
só entra nos grupos das organizações às quais o usuário pertence. Filtrar no
frontend não seria isolamento — seria decoração.

O canal de volta (o cliente manda filtros) é o que justifica WebSocket em vez de
SSE: o painel pode pedir "só esta execução" ou "só falhas" sem reconectar.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)

#: Teto de eventos devolvidos no backfill de reconexão.
BACKFILL_MAX = 300

#: Janela de segurança do backfill. Sob concorrência, um evento com `sequence`
#: menor pode se tornar visível depois de um maior (commits fora de ordem); ao
#: reconectar, reler os últimos segundos evita perder esses retardatários. O
#: cliente deduplica por `id`.
JANELA_SEGURANCA_S = 5


class EventosConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        usuario = self.scope.get("user")
        if usuario is None or not usuario.is_authenticated:
            await self.close(code=4401)
            return

        self.grupos = await self._grupos_do_usuario(usuario)
        if not self.grupos:
            await self.close(code=4403)
            return

        self.filtro_correlacao = None
        self.filtro_status = None

        for grupo in self.grupos:
            await self.channel_layer.group_add(grupo, self.channel_name)
        await self.accept()

        desde = self._param("desde")
        await self.send_json({
            "tipo": "conectado",
            "grupos": len(self.grupos),
        })
        if desde is not None:
            for evento in await self._backfill(usuario, desde):
                await self.send_json({"tipo": "evento", "evento": evento})

    async def disconnect(self, code):
        for grupo in getattr(self, "grupos", []):
            await self.channel_layer.group_discard(grupo, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Filtros vindos do painel. Só restringem — nunca ampliam o acesso."""
        if content.get("tipo") != "filtro":
            return
        self.filtro_correlacao = content.get("correlation_id") or None
        self.filtro_status = content.get("status") or None
        await self.send_json({"tipo": "filtro_aplicado"})

    async def evento_novo(self, message):
        evento = message["evento"]
        if self.filtro_correlacao and evento.get("correlation_id") != self.filtro_correlacao:
            return
        if self.filtro_status and evento.get("status") != self.filtro_status:
            return
        await self.send_json({"tipo": "evento", "evento": evento})

    # ── auxiliares ────────────────────────────────────────────────────────────

    def _param(self, nome):
        from urllib.parse import parse_qs

        query = parse_qs((self.scope.get("query_string") or b"").decode())
        valores = query.get(nome)
        if not valores:
            return None
        try:
            return int(valores[0])
        except ValueError:
            return None

    @database_sync_to_async
    def _grupos_do_usuario(self, usuario):
        from apps.accounts.views import orgs_do_usuario

        from .emit import grupo_do_tenant

        return [grupo_do_tenant(org.id) for org in orgs_do_usuario(usuario)]

    @database_sync_to_async
    def _backfill(self, usuario, desde):
        from datetime import timedelta

        from django.db.models import Q
        from django.utils import timezone

        from apps.accounts.views import orgs_do_usuario

        from .models import PipelineEvent

        corte = timezone.now() - timedelta(seconds=JANELA_SEGURANCA_S)
        eventos = (
            PipelineEvent.objects.filter(tenant__in=orgs_do_usuario(usuario))
            .filter(Q(sequence__gt=desde) | Q(recorded_at__gte=corte))
            .order_by("sequence")[:BACKFILL_MAX]
        )
        return [e.para_websocket() for e in eventos]

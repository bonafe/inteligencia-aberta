"""Alimenta `EventoReplicacao` (apps.cluster) a cada mudança em
`Artifact`/`DocumentText`/`DocumentFragment` — o log de saída que outras
máquinas do cluster puxam para se manter em sincronia.

Separado de `signals.py` (que dispara o pipeline de extração) de propósito:
são duas responsabilidades sem relação — uma decide o que processar em
seguida, esta decide o que fica disponível para replicar. Escrita sempre
incondicional; o filtro de "o que pode sair daqui" vive só em
`apps.cluster.replicacao.eventos_para_peer`.

Nunca levanta — uma falha aqui não pode impedir a gravação do `Artifact`/
`DocumentText`/`DocumentFragment` que a disparou.
"""

import logging

from django.conf import settings
from django.core import serializers
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Artifact, DocumentFragment, DocumentText

logger = logging.getLogger(__name__)


def _proximo_sequence() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('cluster_evento_replicacao_seq')")
        return cursor.fetchone()[0]


def _serializar(instance) -> dict:
    """Campos do modelo em forma JSON-segura (UUID/datetime já convertidos)."""
    bruto = serializers.serialize("json", [instance])
    import json
    return json.loads(bruto)[0]["fields"]


def _origem_maquina_atual():
    """A `Maquina` desta instância, se `CLUSTER_MACHINE_ID` estiver configurado."""
    machine_id = getattr(settings, "CLUSTER_MACHINE_ID", "")
    if not machine_id:
        return None
    from apps.cluster.models import Maquina

    return Maquina.objects.filter(id=machine_id).first()


def _registrar(tipo: str, organizacao_id, objeto_id, instance):
    from apps.cluster.models import EventoReplicacao

    try:
        EventoReplicacao.objects.create(
            sequence=_proximo_sequence(),
            tipo=tipo,
            organizacao_id=organizacao_id,
            objeto_id=objeto_id,
            payload=_serializar(instance),
            origem_maquina=_origem_maquina_atual(),
            ocorrido_em=timezone.now(),
        )
    except Exception:
        logger.exception("falha ao registrar evento de replicação — tipo=%s objeto_id=%s", tipo, objeto_id)


@receiver(post_save, sender=Artifact)
def registrar_artifact(sender, instance, **kwargs):
    _registrar("artifact.upsert", instance.tenant_id, instance.id, instance)


@receiver(post_save, sender=DocumentText)
def registrar_document_text(sender, instance, **kwargs):
    _registrar("document_text.upsert", instance.document.tenant_id, instance.id, instance)


@receiver(post_save, sender=DocumentFragment)
def registrar_document_fragment(sender, instance, **kwargs):
    _registrar(
        "document_fragment.upsert",
        instance.document_text.document.tenant_id,
        instance.id,
        instance,
    )

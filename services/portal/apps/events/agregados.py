"""Números do estado atual do sistema, para a faixa superior do painel.

Tudo aqui degrada em silêncio: se o Redis não responde ou o inspect do Celery
demora, o painel mostra o que conseguiu e segue. Um painel de observabilidade
que trava porque a infra observada está ruim é inútil justamente quando importa.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def contar_gaps() -> dict:
    """Os três buracos do pipeline que o catch-up do Beat procura.

    Extraído de `apps.artifacts.tasks.scan_unprocessed_documents`, que passou a
    usar esta função — a contagem exibida no painel é literalmente a mesma que
    o Beat usa para decidir o que reenfileirar.

    Cada item vem como `(id_do_alvo, artifact_id, tenant_id)`: o catch-up
    precisa do artefato e do tenant para pôr a correlação em contexto antes de
    despachar, senão os eventos de ciclo de vida da task nascem órfãos, fora da
    linha do tempo da captura a que pertencem.
    """
    from apps.artifacts.models import Artifact, DocumentFragment, DocumentText

    processados = DocumentText.objects.values_list("document_id", flat=True)
    sem_texto = [
        (art.id, art.id, art.tenant_id)
        for art in Artifact.objects.filter(
            artifact_type=Artifact.Type.DOCUMENT
        ).exclude(id__in=processados).only("id", "content", "tenant_id")
        if (art.content or {}).get("mhtml_path")
    ]

    fragmentados = DocumentFragment.objects.values_list(
        "document_text_id", flat=True
    ).distinct()
    sem_fragmentos = list(
        DocumentText.objects.exclude(id__in=fragmentados)
        .values_list("id", "document_id", "document__tenant_id")
    )

    sem_embedding = list(
        DocumentFragment.objects.filter(qdrant_point_id="")
        .values_list(
            "id", "document_text__document_id", "document_text__document__tenant_id"
        )
    )

    return {
        "extracao": sem_texto,
        "fragmentacao": sem_fragmentos,
        "embedding": sem_embedding,
    }


def resumo_gaps() -> dict:
    gaps = contar_gaps()
    return {chave: len(ids) for chave, ids in gaps.items()}


def estado_da_fila() -> dict:
    """Profundidade da fila e tasks em execução agora."""
    resultado = {"enfileiradas": None, "ativas": None, "workers": [], "disponivel": False}

    try:
        import redis
        from django.conf import settings

        cliente = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        resultado["enfileiradas"] = cliente.llen("celery")
        resultado["disponivel"] = True
    except Exception:
        logger.debug("não foi possível medir a fila do Celery", exc_info=True)

    try:
        from config.celery import celery_app

        inspect = celery_app.control.inspect(timeout=2)
        ativas = inspect.active() or {}
        resultado["ativas"] = sum(len(v) for v in ativas.values())
        resultado["workers"] = [
            {"nome": nome, "tasks": len(tarefas)} for nome, tarefas in ativas.items()
        ]
    except Exception:
        logger.debug("não foi possível inspecionar os workers", exc_info=True)

    return resultado


def resumo_execucoes(tenants=None) -> dict:
    """Contagens das execuções recentes, por status."""
    from .models import PipelineEvent, PipelineRun

    runs = PipelineRun.objects.all()
    eventos = PipelineEvent.objects.all()
    if tenants is not None:
        runs = runs.filter(tenant__in=tenants)
        eventos = eventos.filter(tenant__in=tenants)

    desde = timezone.now() - timedelta(hours=24)
    # "Falhas" conta EVENTOS que falharam, não execuções com status `falhou`.
    # Uma captura em que só o dom2parser quebrou termina como `parcial`, e
    # mostrar "0 falhas" nesse caso esconderia exatamente o que o operador
    # precisa ver.
    return {
        "em_andamento": runs.filter(status=PipelineRun.Status.EM_ANDAMENTO).count(),
        "concluidas_24h": runs.filter(
            status__in=(PipelineRun.Status.CONCLUIDO, PipelineRun.Status.PARCIAL),
            iniciado_em__gte=desde,
        ).count(),
        "falhas_24h": eventos.filter(status="falhou", occurred_at__gte=desde).count(),
        "execucoes_com_falha_24h": runs.filter(
            total_falhas__gt=0, iniciado_em__gte=desde
        ).count(),
        "eventos_24h": eventos.filter(occurred_at__gte=desde).count(),
        "eventos_total": eventos.count(),
    }


def painel(tenants=None) -> dict:
    """Tudo que a faixa 'Agora' do painel precisa, em uma chamada."""
    return {
        "gaps": resumo_gaps(),
        "fila": estado_da_fila(),
        "execucoes": resumo_execucoes(tenants),
        "em": timezone.now().isoformat(),
    }

"""Execução das etapas de processamento declaradas pelas extensões (ADR 023).

O núcleo despacha (`signals.despachar_etapas`) e roda aqui, dentro de `etapa()`: cada execução emite
exatamente um evento `extensao.etapa.<chave>` (`ok`, `vazio`, `ignorado` ou `falhou`), com a correlação
do artefato. Falha de uma extensão não derruba a captura nem as outras etapas.
"""
import logging

from celery import shared_task
from django.utils.module_loading import import_string

from apps.events.context import set_correlation_id, set_tenant_id
from apps.events.emit import emit, etapa

from . import registro

logger = logging.getLogger(__name__)


@shared_task(name="apps.extensoes.tasks.executar_etapa")
def executar_etapa(chave: str, artifact_id: str) -> str:
    from apps.artifacts.models import Artifact
    from apps.artifacts.tasks import correlacao_do_artefato

    definicao = registro.etapa(chave)
    if definicao is None:        # extensão desligada depois de enfileirar: nada a fazer
        emit("extensao.etapa", "ignorado", subject_type="artifact", subject_id=artifact_id,
             message=f"etapa {chave!r} não está instalada", source="worker")
        return "ignorado"
    artefato = Artifact.objects.filter(id=artifact_id).first()
    if artefato is None:
        return "ignorado"
    set_correlation_id(correlacao_do_artefato(artefato.id))
    set_tenant_id(artefato.tenant_id)
    try:
        with etapa(f"extensao.etapa.{chave}", subject_type="artifact", subject_id=artefato.id,
                   tenant_id=artefato.tenant_id, message=definicao.rotulo) as resultado:
            import_string(definicao.executar)(artefato, resultado)
            status = resultado.status or "ok"
    except Exception:
        logger.exception("etapa %s falhou no artefato %s", chave, artifact_id)
        return "falhou"          # já virou evento `falhou` em `etapa()`
    return status

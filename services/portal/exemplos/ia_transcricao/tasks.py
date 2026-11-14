from celery import shared_task

from apps.artifacts.models import Artifact
from apps.extensoes import dados, registro
from apps.extensoes.tasks import executar_etapa

LOTE = 50


@shared_task(name="ia_transcricao.tasks.varrer_pendentes")
def varrer_pendentes() -> int:
    """Enfileira (na fila da etapa) os áudios e vídeos que ainda não têm transcrição."""
    etapa = registro.etapa("transcricao.audio")
    if etapa is None:
        return 0
    enfileirados = 0
    for artefato in Artifact.objects.filter(artifact_type__in=etapa.tipos).order_by("created_at").iterator():
        if registro.casa(etapa, artefato) and not dados.ler(artefato, "transcricao"):
            executar_etapa.apply_async(args=[etapa.chave, str(artefato.id)], queue=etapa.fila or None)
            enfileirados += 1
            if enfileirados >= LOTE:
                break
    return enfileirados

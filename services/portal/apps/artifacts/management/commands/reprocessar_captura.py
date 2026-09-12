"""Reexecuta a extração de uma captura, emitindo a trilha completa de eventos.

Uso típico — descobrir por que um artefato não gerou dados do dom2parser:

    manage.py reprocessar_captura <artifact_id> --forcar
    manage.py reprocessar_captura https://exemplo.com/pagina --forcar

Depois, a timeline em /eventos/<correlation_id>/ diz em que etapa e por quê.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.artifacts.models import Artifact
from apps.artifacts.tasks import correlacao_do_artefato, extract_text_from_mhtml
from apps.events.context import set_correlation_id, set_tenant_id


class Command(BaseCommand):
    help = "Reenfileira a extração de um artefato, por id ou por URL."

    def add_arguments(self, parser):
        parser.add_argument("alvo", help="UUID do artefato ou URL capturada")
        parser.add_argument(
            "--forcar", action="store_true",
            help="Apaga o DocumentText e os fragmentos existentes antes de reextrair. "
                 "Sem isso, um artefato já processado é apenas ignorado.",
        )
        parser.add_argument(
            "--sincrono", action="store_true",
            help="Executa no processo atual em vez de enfileirar no Celery "
                 "(útil para ver o traceback direto no terminal).",
        )

    def handle(self, *args, **opts):
        alvo = opts["alvo"]
        artefatos = self._resolver(alvo)
        if not artefatos:
            raise CommandError(f"Nenhum artefato encontrado para '{alvo}'.")

        for artefato in artefatos:
            correlacao = correlacao_do_artefato(artefato.id)
            # Antes de enfileirar: é daqui que o signal do Celery tira a
            # correlação para o header da mensagem.
            set_correlation_id(correlacao)
            set_tenant_id(artefato.tenant_id)
            url = (artefato.content or {}).get("url", "")
            if opts["sincrono"]:
                resultado = extract_text_from_mhtml(str(artefato.id), forcar=opts["forcar"])
                self.stdout.write(f"{artefato.id} {url} → {resultado}")
            else:
                extract_text_from_mhtml.delay(str(artefato.id), forcar=opts["forcar"])
                self.stdout.write(f"{artefato.id} {url} → enfileirado")
            self.stdout.write(self.style.SUCCESS(f"  timeline: /eventos/{correlacao}/"))

    def _resolver(self, alvo):
        try:
            import uuid
            uuid.UUID(alvo)
        except ValueError:
            return list(
                Artifact.objects.filter(
                    artifact_type=Artifact.Type.DOCUMENT, content__url=alvo
                )
            )
        return list(Artifact.objects.filter(id=alvo))

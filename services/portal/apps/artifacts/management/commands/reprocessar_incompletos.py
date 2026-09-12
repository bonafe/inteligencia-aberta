"""Reprocessa capturas cujo DocumentText existe mas está incompleto.

Este é o buraco que nenhum mecanismo automático cobria. O catch-up do Beat
(`scan_unprocessed_documents`) só enxerga artefatos **sem** DocumentText; um
artefato processado antes de uma etapa nova existir — o `dom2parser` e o
`extruct` são exatamente esse caso — fica com o campo `NULL` para sempre, e o
sintoma na galeria (aba desabilitada) é indistinguível de "a página não tinha
esse dado".

    manage.py reprocessar_incompletos --criterio dom2parser --limite 20
"""

from django.core.management.base import BaseCommand

from apps.artifacts.models import DocumentText
from apps.artifacts.tasks import correlacao_do_artefato, extract_text_from_mhtml
from apps.events.context import set_correlation_id, set_tenant_id

CRITERIOS = {
    "dom2parser": {"dados_estruturados_dom2parser__isnull": True},
    "extruct": {"dados_estruturados_extruct__isnull": True},
    "dom_representation": {"dom_representation__isnull": True},
    "structured_data": {"structured_data__isnull": True},
}


class Command(BaseCommand):
    help = "Reenfileira extrações cujo DocumentText está sem um campo específico."

    def add_arguments(self, parser):
        parser.add_argument(
            "--criterio", choices=sorted(CRITERIOS), required=True,
            help="Qual campo ausente caracteriza um DocumentText incompleto.",
        )
        parser.add_argument("--limite", type=int, default=50)
        parser.add_argument(
            "--simular", action="store_true",
            help="Só lista o que seria reprocessado, sem enfileirar nada.",
        )

    def handle(self, *args, **opts):
        filtro = CRITERIOS[opts["criterio"]]
        alvos = list(
            DocumentText.objects.filter(**filtro)
            .select_related("document")
            .order_by("-created_at")[: opts["limite"]]
        )

        if not alvos:
            self.stdout.write("Nada a reprocessar.")
            return

        self.stdout.write(
            f"{len(alvos)} DocumentText sem '{opts['criterio']}'"
            f"{' (simulação)' if opts['simular'] else ''}:"
        )
        for doc in alvos:
            self.stdout.write(f"  {doc.document_id}  {doc.source_url[:90]}")
            if not opts["simular"]:
                set_correlation_id(correlacao_do_artefato(doc.document_id))
                set_tenant_id(doc.document.tenant_id)
                extract_text_from_mhtml.delay(str(doc.document_id), forcar=True)

        if opts["simular"]:
            self.stdout.write("Nenhuma task enfileirada. Rode sem --simular para valer.")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{len(alvos)} reprocessamentos enfileirados — acompanhe em /eventos/"
            ))

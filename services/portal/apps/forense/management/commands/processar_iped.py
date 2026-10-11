"""Pede ao IPED que processe uma pasta do servidor (prova de conceito — ADR 019).

    manage.py processar_iped --organizacao <slug> --relativo docs/caso1 [--perfil fastmode]
    manage.py processar_iped --status <id>
    manage.py processar_iped --cancelar <id>

O caminho é **relativo** a uma raiz permitida (`IPED_RAIZES_PERMITIDAS`); o processamento roda no
`worker-iped` (perfil `iped` do compose) e o caso fica em `IPED_SAIDA_DIR/<id>`.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.forense import servico
from apps.forense.models import ProcessamentoIPED


class Command(BaseCommand):
    help = "Solicita, consulta ou cancela um processamento do IPED."

    def add_arguments(self, parser):
        parser.add_argument("--organizacao")
        parser.add_argument("--relativo")
        parser.add_argument("--perfil", default="fastmode")
        parser.add_argument("--status")
        parser.add_argument("--cancelar")

    def handle(self, *args, **o):
        if o["status"] or o["cancelar"]:
            try:
                p = ProcessamentoIPED.objects.get(pk=o["status"] or o["cancelar"])
            except (ProcessamentoIPED.DoesNotExist, ValueError):
                raise CommandError("processamento não encontrado") from None
            if o["cancelar"]:
                servico.cancelar(p)
            self.stdout.write(f"{p.id}: {p.estado} (código {p.codigo_saida}) {p.erro[:300]}")
            return
        if not (o["organizacao"] and o["relativo"]):
            raise CommandError("informe --organizacao e --relativo")
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        try:
            p = servico.solicitar(
                organizacao=org, usuario=None, raiz=settings.IPED_ENTRADA_RAIZ,
                relativo=o["relativo"], perfil=o["perfil"],
            )
        except servico.ErroProcessamento as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"{p.id}: solicitado. Acompanhe com --status {p.id}")

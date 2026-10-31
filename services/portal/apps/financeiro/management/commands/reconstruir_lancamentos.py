"""Refaz a projeção `Movimento` a partir das alegações ativas (ver `apps.financeiro.lancamentos`)."""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.financeiro.lancamentos import reconstruir


class Command(BaseCommand):
    help = "Refaz os movimentos a partir das alegações (contas, fusões e categorias são mantidas)."

    def add_arguments(self, parser):
        parser.add_argument("--organizacao", help="slug da organização (padrão: todas)")

    def handle(self, *args, **options):
        tenant = None
        if slug := options.get("organizacao"):
            tenant = Organization.objects.filter(slug=slug).first()
            if tenant is None:
                raise CommandError(f"organização {slug!r} não encontrada")
        total = reconstruir(tenant)
        self.stdout.write(self.style.SUCCESS(f"{total} movimentos projetados"))

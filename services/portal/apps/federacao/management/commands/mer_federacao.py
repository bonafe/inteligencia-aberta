"""Imprime o MER da federação em Mermaid, lido dos models reais.

    manage.py mer_federacao > /tmp/mer.mmd

É o que o `federacao.html` do site embute; regenerar quando um model da federação mudar.
"""

from django.core.management.base import BaseCommand

from apps.federacao import mer


class Command(BaseCommand):
    help = "Imprime o MER da federação (Mermaid) a partir dos models."

    def handle(self, *args, **options):
        self.stdout.write(mer.mermaid())

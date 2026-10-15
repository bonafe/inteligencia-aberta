"""Lista os pares da instância (base da idempotência da automação).

    manage.py par_listar [--json]
"""
from django.core.management.base import BaseCommand

from apps.cluster.models import Maquina

from ._pares_cli import contexto


class Command(BaseCommand):
    help = "Lista os pares (não inclui a própria instância)."

    def add_arguments(self, parser):
        parser.add_argument("--usuario")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        import json

        org, _ = contexto(opts["usuario"])
        linhas = [
            {"apelido": p.apelido, "did": p.did, "tipo": p.tipo, "estado": p.estado, "endpoint": p.endpoint_controle}
            for p in Maquina.objects.filter(organizacao=org, eh_local=False).order_by("apelido")
        ]
        if opts["json"]:
            self.stdout.write(json.dumps(linhas, sort_keys=True))
        else:
            self.stdout.write("\n".join(f"{p['apelido']}\t{p['tipo']}\t{p['estado']}\t{p['did']}" for p in linhas) or "(sem pares)")

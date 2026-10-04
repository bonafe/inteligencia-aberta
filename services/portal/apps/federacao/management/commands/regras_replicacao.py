"""Regras de replicação (ADR 011): semear, listar e **simular uma decisão**.

    manage.py regras_replicacao --semear
    manage.py regras_replicacao --listar --organizacao <slug>
    manage.py regras_replicacao --decidir --organizacao <slug> --sentido enviar \\
        --par-tipo terceiro --par-ref maria --nivel confidencial --tipo-objeto documento

`--decidir` responde "por que isto não vai para o par X?": imprime a decisão, a regra
que decidiu e as que casaram. **Não registra nada** (simular não deixa rastro).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.federacao import politica, regras
from apps.federacao.models import RegraReplicacao


class Command(BaseCommand):
    help = "Semeia as regras padrão, lista as regras e simula decisões do motor de replicação."

    def add_arguments(self, parser):
        parser.add_argument("--semear", action="store_true", help="Cria as regras padrão que faltam em cada organização.")
        parser.add_argument("--listar", action="store_true")
        parser.add_argument("--decidir", action="store_true")
        parser.add_argument("--organizacao", help="Slug da organização (obrigatório em --listar e --decidir).")
        parser.add_argument("--sentido", choices=[regras.ENVIAR, regras.RECEBER], default=regras.ENVIAR)
        parser.add_argument("--par-tipo", choices=[regras.PROPRIO, regras.TERCEIRO])
        parser.add_argument("--par-ref")
        parser.add_argument("--nivel", default="restrito")
        parser.add_argument("--tipo-objeto")
        parser.add_argument("--espaco-urn")
        parser.add_argument("--objeto-urn")

    def _organizacao(self, slug):
        if not slug:
            raise CommandError("informe --organizacao <slug>")
        try:
            return Organization.objects.get(slug=slug)
        except Organization.DoesNotExist:
            raise CommandError(f"organização {slug!r} não encontrada")

    def handle(self, *args, **opts):
        if not (opts["semear"] or opts["listar"] or opts["decidir"]):
            raise CommandError("escolha --semear, --listar ou --decidir")

        if opts["semear"]:
            total = sum(politica.garantir_regras_padrao(o, completo=True) for o in Organization.objects.all())
            self.stdout.write(f"{total} regra(s) padrão criada(s).")

        if opts["listar"]:
            org = self._organizacao(opts["organizacao"])
            for r in RegraReplicacao.objects.filter(organizacao=org).order_by("criada_em"):
                estado = "ativa" if r.ativa else "inativa"
                self.stdout.write(f"{r.id}  {r.efeito:8} {r.sentido:8} {estado:7} {'padrão ' if r.padrao else ''}{r}")

        if opts["decidir"]:
            org = self._organizacao(opts["organizacao"])
            if not opts["par_tipo"]:
                raise CommandError("--decidir exige --par-tipo")
            ctx = regras.Contexto(
                sentido=opts["sentido"], par_tipo=opts["par_tipo"], par_ref=opts["par_ref"], nivel=opts["nivel"],
                tipo_objeto=opts["tipo_objeto"], espaco_urn=opts["espaco_urn"], objeto_urn=opts["objeto_urn"],
            )
            d = politica.decidir(org, ctx)
            estilo = self.style.SUCCESS if d.permitido else self.style.ERROR
            self.stdout.write(estilo(f"{'PERMITIDO' if d.permitido else 'NEGADO'}  [{d.origem}]  {d.motivo}"))
            self.stdout.write(f"nível efetivo: {d.nivel_efetivo}; regras que casaram: {', '.join(d.casaram) or 'nenhuma'}")

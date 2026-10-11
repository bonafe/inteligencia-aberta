"""Exporta um espaço para um par como pacote offline assinado (F1b).

    manage.py exportar_pacote --organizacao <slug> --espaco <nome|urn|padrao> --par <apelido> --saida pacote.zip

Só leva o que o motor de regras permite enviar a esse par; o que ele nega é contado e
não vai. O par precisa estar confirmado. O pacote é para **aquele** par (assinado e
endereçado a ele): outro destino o recusa.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.cluster.models import Maquina
from apps.federacao import espacos, pacote
from apps.federacao.models import Space


class Command(BaseCommand):
    help = "Exporta um espaço para um par como pacote offline assinado."

    def add_arguments(self, parser):
        parser.add_argument("--organizacao", required=True, help="Slug da organização.")
        parser.add_argument("--espaco", required=True, help="Nome ou urn do espaço, ou `padrao`.")
        parser.add_argument("--par", required=True, help="Apelido do par de destino.")
        parser.add_argument("--saida", required=True, help="Arquivo .zip a gravar.")

    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        par = Maquina.objects.filter(organizacao=org, apelido=o["par"], eh_local=False).first()
        if par is None:
            raise CommandError("par não encontrado")
        ref = o["espaco"]
        if ref == "padrao":
            espaco = espacos.espaco_padrao(org)
        else:
            espaco = Space.objects.filter(organizacao=org, nome=ref).first() or espacos.resolver_urn(ref)
            if espaco is None:
                raise CommandError("espaço não encontrado")
        try:
            dados, r = pacote.exportar(organizacao=org, espaco=espaco, par=par)
        except pacote.ErroPacote as exc:
            raise CommandError(str(exc)) from None
        with open(o["saida"], "wb") as f:
            f.write(dados)
        self.stdout.write(f"{o['saida']}: {r.enviados} enviado(s), {r.negados} negado(s) pelas regras, "
                          f"{len(r.sem_blob)} sem blob legível, {len(r.grandes)} grande(s) demais.")
        for urn, motivo in r.sem_blob:
            self.stdout.write(self.style.WARNING(f"  SEM BLOB {urn}: {motivo}"))

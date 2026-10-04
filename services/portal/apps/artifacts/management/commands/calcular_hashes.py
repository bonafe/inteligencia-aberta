"""Calcula o hash (RFC 6920) do MHTML de artefatos anteriores à F0 — ADR 010.

Capturas novas já chegam com `blob_hash` (o orchestrator o calcula no ato da
captura). Este comando cobre o que foi capturado antes e, com `--verificar`,
reconfere o que já tem hash contra os bytes hoje no armazenamento.

    manage.py calcular_hashes --simular
    manage.py calcular_hashes --limite 200
    manage.py calcular_hashes --verificar

A gravação usa `save(update_fields=["blob_hash"])`: não mexe em `updated_at` e o
`post_save` de replicação propaga o hash aos peers do cluster (o signal de
extração só age na criação, então nada é reprocessado). Hash divergente nunca é
sobrescrito — é só relatado, porque significa que o blob mudou depois da captura.
"""

import os

from django.core.management.base import BaseCommand
from minio import Minio

from apps.artifacts.models import Artifact
from config.conteudo_hash import hash_ni


def ler_blob(bucket: str, caminho: str) -> bytes:
    cliente = Minio(
        os.getenv("S3_ENDPOINT", "garage:3900"),
        access_key=os.getenv("S3_ACCESS_KEY", ""),
        secret_key=os.getenv("S3_SECRET_KEY", ""),
        secure=False,
    )
    resposta = cliente.get_object(bucket, caminho)
    try:
        return resposta.read()
    finally:
        resposta.close()
        resposta.release_conn()


class Command(BaseCommand):
    help = "Calcula blob_hash (ni:) dos MHTML sem hash; --verificar reconfere os que já têm."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=0, help="0 = sem limite.")
        parser.add_argument(
            "--simular", action="store_true",
            help="Calcula e relata, sem gravar nada.",
        )
        parser.add_argument(
            "--verificar", action="store_true",
            help="Inclui artefatos que já têm hash e relata os que divergem do blob atual.",
        )

    def handle(self, *args, **opts):
        alvos = Artifact.objects.filter(content__has_key="mhtml_path").order_by("created_at")
        if not opts["verificar"]:
            alvos = alvos.filter(blob_hash__isnull=True)
        if opts["limite"]:
            alvos = alvos[: opts["limite"]]

        gravados = confirmados = 0
        divergentes, ausentes = [], []
        total = 0
        for artefato in alvos:
            total += 1
            bucket = artefato.content.get("mhtml_bucket", "")
            caminho = artefato.content["mhtml_path"]
            try:
                calculado = hash_ni(ler_blob(bucket, caminho))
            except Exception as exc:  # objeto ausente, S3 fora do ar...
                ausentes.append((artefato.id, f"{type(exc).__name__}: {exc}"[:120]))
                continue

            if artefato.blob_hash is None:
                if not opts["simular"]:
                    artefato.blob_hash = calculado
                    artefato.save(update_fields=["blob_hash"])
                gravados += 1
            elif artefato.blob_hash == calculado:
                confirmados += 1
            else:
                divergentes.append((artefato.id, artefato.blob_hash, calculado))

        sufixo = " (simulação)" if opts["simular"] else ""
        self.stdout.write(
            f"{total} artefato(s) examinado(s){sufixo}: {gravados} hash(es) "
            f"{'a gravar' if opts['simular'] else 'gravado(s)'}, {confirmados} confirmado(s), "
            f"{len(divergentes)} divergente(s), {len(ausentes)} sem blob legível."
        )
        for id_, antigo, novo in divergentes:
            self.stdout.write(self.style.ERROR(f"  DIVERGENTE {id_}: gravado {antigo} ≠ atual {novo}"))
        for id_, motivo in ausentes:
            self.stdout.write(self.style.WARNING(f"  SEM BLOB {id_}: {motivo}"))

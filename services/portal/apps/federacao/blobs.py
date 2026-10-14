"""Leitura e gravação dos blobs (MHTML) no armazenamento S3 da instância (Garage).

Fica aqui, em funções pequenas, para os testes substituírem o armazenamento sem rede.
"""

import io
import os

from minio import Minio

BUCKET_MHTML = "inteligencia-aberta-mhtml"


def _cliente() -> Minio:
    return Minio(
        os.getenv("S3_ENDPOINT", "garage:3900"),
        access_key=os.getenv("S3_ACCESS_KEY", ""),
        secret_key=os.getenv("S3_SECRET_KEY", ""),
        secure=False,
    )


def ler_blob(bucket: str, caminho: str) -> bytes:
    resposta = _cliente().get_object(bucket, caminho)
    try:
        return resposta.read()
    finally:
        resposta.close()
        resposta.release_conn()


def gravar_blob(bucket: str, caminho: str, dados: bytes, tipo: str = "multipart/related") -> None:
    _cliente().put_object(bucket, caminho, io.BytesIO(dados), length=len(dados), content_type=tipo)


# ── acervo pessoal (arquivos grandes: nunca inteiros em memória) ─────────────

BUCKET_ACERVO = "inteligencia-aberta-acervo"
#: Tamanho das partes do envio multipart (o mínimo do S3 é 5 MiB).
PARTE_ENVIO = 64 * 1024 * 1024


def garantir_bucket(bucket: str) -> None:
    cliente = _cliente()
    if not cliente.bucket_exists(bucket):
        cliente.make_bucket(bucket)


def existe_blob(bucket: str, caminho: str) -> bool:
    from minio.error import S3Error

    try:
        _cliente().stat_object(bucket, caminho)
        return True
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
            return False
        raise


def gravar_arquivo(bucket: str, caminho: str, origem, tipo: str = "application/octet-stream") -> None:
    """Envia o arquivo `origem` (caminho local) por streaming, em partes."""
    tamanho = os.path.getsize(origem)
    with open(origem, "rb") as fh:
        _cliente().put_object(bucket, caminho, fh, length=tamanho, part_size=PARTE_ENVIO, content_type=tipo)

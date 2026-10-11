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

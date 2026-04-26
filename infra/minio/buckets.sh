#!/bin/sh
# Aguarda MinIO inicializar e cria os buckets necessários
sleep 5
mc alias set local http://minio:9000 "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}"
mc mb --ignore-existing local/artefatos
mc mb --ignore-existing local/documentos
mc mb --ignore-existing local/audios

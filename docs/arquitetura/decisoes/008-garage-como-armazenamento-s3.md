# ADR 008 — Garage como armazenamento de objetos S3

**Status:** aceito — 2026-10-03

## Contexto

A MinIO arquivou a edição community e removeu `minio/minio` do Docker Hub; o único registry que restou (`quay.io/minio/minio:latest`) é uma imagem sem garantia de continuidade, e a edição community perdeu o console de administração. A stack não depende de nada específico do MinIO: o código só usa o cliente Python `minio` com operações S3 básicas (`bucket_exists`, `make_bucket`, `put_object`, `get_object`, `list_objects`).

## Decisão

Trocar o MinIO pelo **Garage** (Deuxfleurs, S3 compatível, em Rust, pensado para auto-hospedagem em poucos nós). Escolhido sobre SeaweedFS (mais peças para operar: master, volume, filer) e RustFS (mais novo) por ser leve e casar com o modelo de uma instância por host (ADR 007).

- **Imagem própria** em `infra/garage/`: a oficial é distroless (só o binário); o binário estático é copiado para um alpine para o `entrypoint.sh` poder configurar o nó.
- **Primeiro boot idempotente** no `entrypoint.sh`: layout de nó único (`replication_factor = 1`), importação da chave `S3_ACCESS_KEY`/`S3_SECRET_KEY` e criação dos buckets de `S3_BUCKETS`. O healthcheck só passa depois disso, então `depends_on: service_healthy` já encontra tudo pronto. O `bootstrap_instancia` continua garantindo os buckets pela API S3.
- **Região `us-east-1`** em `garage.toml`: é a que o cliente `minio` assume; evita passar região em cinco pontos do código.
- **Variáveis renomeadas** sem alias: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`/`MINIO_ENDPOINT` → `S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_ENDPOINT` (padrão `garage:3900`); `MINIO_IMAGE` → `GARAGE_VERSION`. Novo segredo `GARAGE_RPC_SECRET`.
- A biblioteca Python `minio` **permanece** (cliente S3 genérico, funciona com o Garage).
- Os nomes de estágio de evento `minio` / `extracao.minio` **não** foram renomeados: são taxonomia persistida em `PipelineEvent` (append-only) e na projeção.

## Consequências

- Os formatos dos segredos S3 deixam de ser livres: `GK` + 24 hex para o id, 64 hex para o secret e para o RPC. `token_urlsafe` não serve para eles.
- Não há console web. Administração é pela CLI `garage` (`docker compose exec garage garage status`) ou por clientes S3 (`aws`, `mc`, `rclone`).
- Porta S3 passa de 9000/9001 para 3900. Dev publica 3900; o overlay `no-infraestrutura` publica 3900 só na VPN.
- Dados em `${DATA_DIR}/garage/{meta,data}`. Nenhuma migração de dados foi necessária (sem dados em produção na troca); `${DATA_DIR}/minio/` antigo pode ser removido.
- Máquinas de cluster existentes precisam trocar `MINIO_ENDPOINT` por `S3_ENDPOINT=<infra>:3900` e receber as mesmas `S3_*` do nó de infra.

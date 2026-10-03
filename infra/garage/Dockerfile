# Garage (S3 compatível) + shell. A imagem oficial é distroless (só o binário),
# então o binário estático é copiado para um alpine para o entrypoint poder
# configurar layout, chave e buckets no primeiro boot.
ARG GARAGE_VERSION=v2.4.1
FROM dxflrs/garage:${GARAGE_VERSION} AS garage

FROM alpine:3.20
COPY --from=garage /garage /usr/local/bin/garage
COPY garage.toml /etc/garage.toml
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

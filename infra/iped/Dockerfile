# IPED como capacidade opcional do nó (ADR 019). Sem rede, entrada somente leitura, saída em volume.
#
# Base: a imagem da comunidade `ipeddocker/iped:processor` (github.com/iped-docker/iped), que já traz o
# IPED, o Java e o Sleuthkit compilado — no Linux o IPED não roda só com o zip do release (precisa do
# fork do Sleuthkit). Ela NÃO é do projeto IPED: por isso a versão é fixada por argumento e o ideal é
# fixar por digest (`ipeddocker/iped:processor@sha256:...`) depois de validar.
ARG IPED_IMAGE=ipeddocker/iped:processor
FROM ${IPED_IMAGE}

COPY runner.sh /usr/local/bin/iped-runner.sh
COPY exportar_itens.py /usr/local/bin/iped-exportar-itens.py
RUN chmod +x /usr/local/bin/iped-runner.sh

# O runner só lê pedidos do volume /trabalho; não escuta porta nenhuma.
ENTRYPOINT ["/usr/local/bin/iped-runner.sh"]

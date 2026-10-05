FROM node:24-alpine
RUN apk add --no-cache su-exec
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev
COPY src ./src
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENV PORT=8787 BIND_ADDR=0.0.0.0 DATA_DIR=/data
VOLUME /data
EXPOSE 8787
HEALTHCHECK CMD wget -qO- http://127.0.0.1:8787/health || exit 1
# Starts as root only to make DATA_DIR writable (a bind-mounted host directory is created as root:root by Docker),
# then drops to the unprivileged "node" user: the service itself never runs as root.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["node", "src/index.js"]

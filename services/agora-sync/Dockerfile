FROM node:24-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev
COPY src ./src
ENV PORT=8787 BIND_ADDR=0.0.0.0 DATA_DIR=/data
VOLUME /data
USER node
EXPOSE 8787
HEALTHCHECK CMD wget -qO- http://127.0.0.1:8787/health || exit 1
CMD ["node", "src/index.js"]

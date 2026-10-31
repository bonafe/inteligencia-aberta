FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Versão = commit do checkout no build (contexto "git" no docker-compose.yml), lida em /health.
# Sem o binário git: resolve HEAD pelos refs. Fora de /app, que o override de dev monta por cima.
RUN --mount=type=bind,from=git,target=/git \
    ref=$(sed -n 's/^ref: //p' /git/HEAD); \
    if [ -n "$ref" ]; then sha=$(cat "/git/$ref" 2>/dev/null || awk -v r="$ref" '$2==r{print $1}' /git/packed-refs); \
    else sha=$(cat /git/HEAD); fi; \
    printf '%.7s' "$sha" > /etc/ia-version

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]

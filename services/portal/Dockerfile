FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    libpq-dev gcc git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Estáticos coletados na imagem: o daphne de produção os serve via WhiteNoise.
# Usa `base` (e não `production`) porque esta só existe com os segredos em
# ambiente, que não devem estar presentes no build — mas o STORAGES com
# manifesto, que é o que importa aqui, já está em base.
RUN DJANGO_SETTINGS_MODULE=config.settings.base \
    python manage.py collectstatic --noinput

EXPOSE 8000
# daphne (ASGI) em vez do runserver: o painel de eventos usa WebSocket.
# O docker-compose.override.yml de desenvolvimento sobrescreve este comando
# por `manage.py runserver`, que também serve ASGI graças ao app "daphne"
# em INSTALLED_APPS, mas com auto-reload.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]

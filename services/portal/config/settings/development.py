import os
from .base import *

DEBUG = True

ALLOWED_HOSTS = ["*"]

# O cadastro é aberto em todos os ambientes (ver base.py); esta linha só repete o padrão.
REGISTRO_ABERTO = os.environ.get("REGISTRO_ABERTO", "true").lower() == "true"

AUTH_PASSWORD_VALIDATORS = []

# Sem manifesto de estáticos em desenvolvimento: o runserver serve direto de
# STATICFILES_DIRS, sem exigir `collectstatic` a cada alteração de CSS/JS.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

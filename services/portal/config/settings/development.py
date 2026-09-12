from .base import *

DEBUG = True

ALLOWED_HOSTS = ["*"]

AUTH_PASSWORD_VALIDATORS = []

# Sem manifesto de estáticos em desenvolvimento: o runserver serve direto de
# STATICFILES_DIRS, sem exigir `collectstatic` a cada alteração de CSS/JS.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

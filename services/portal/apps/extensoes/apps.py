from django.apps import AppConfig


class ExtensoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.extensoes"
    label = "extensoes"
    verbose_name = "Extensões de domínio"

    def ready(self):
        from . import signals  # noqa: F401

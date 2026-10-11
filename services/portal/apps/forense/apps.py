from django.apps import AppConfig


class ForenseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.forense"
    label = "forense"
    verbose_name = "Processamento forense (IPED)"

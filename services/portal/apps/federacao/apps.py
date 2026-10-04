from django.apps import AppConfig


class FederacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.federacao"
    label = "federacao"
    verbose_name = "Federação entre instâncias"

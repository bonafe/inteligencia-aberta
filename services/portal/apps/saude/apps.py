from django.apps import AppConfig


class SaudeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.saude"
    label = "saude"
    verbose_name = "Saúde (resultados de exames)"
    #: Extensão de domínio (ADR 022): o núcleo lê o manifesto, não importa o app.
    extensao = "apps.saude.extensao.EXTENSAO"

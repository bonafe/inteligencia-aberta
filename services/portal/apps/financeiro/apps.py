from django.apps import AppConfig


class FinanceiroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.financeiro"
    label = "financeiro"
    verbose_name = "Gastos (faturas e extratos)"
    #: Extensão de domínio (ADR 022): o núcleo lê o manifesto, não importa o app.
    extensao = "apps.financeiro.extensao.EXTENSAO"

    def ready(self):
        from . import signals  # noqa: F401

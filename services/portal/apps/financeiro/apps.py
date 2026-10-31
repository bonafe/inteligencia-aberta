from django.apps import AppConfig


class FinanceiroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.financeiro"
    label = "financeiro"
    verbose_name = "Gastos (faturas e extratos)"

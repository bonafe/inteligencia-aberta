from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.events"
    label = "events"
    verbose_name = "Eventos e Observabilidade"

    def ready(self):
        # Conecta os signals do Celery: fila, início, fim, falha e retry de toda
        # task passam a virar evento sem que nenhuma task precise saber disso.
        import apps.events.celery_signals  # noqa: F401

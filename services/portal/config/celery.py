import os
from celery import Celery
from celery.signals import beat_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

celery_app = Celery("inteligencia_aberta")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks()


@beat_init.connect
def agendamentos_das_extensoes(sender=None, **kwargs):
    """Acrescenta ao Beat os agendamentos que as extensões instaladas declaram (ADR 023).

    Só aqui os apps já estão carregados e o registro pode ser lido; `CELERY_BEAT_SCHEDULE` dos settings
    é montado antes disso e continua só com o que é do núcleo."""
    from celery.schedules import schedule

    from apps.extensoes import registro

    for ag in registro.agendamentos():
        sender.scheduler.add(name=f"extensao.{ag.chave}", task=ag.tarefa, schedule=schedule(ag.a_cada_s))

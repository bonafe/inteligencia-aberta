from django.apps import AppConfig


class ClusterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cluster"
    label = "cluster"
    verbose_name = "Cluster multi-máquina"

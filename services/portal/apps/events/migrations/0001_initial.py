"""Log de eventos do pipeline e sua projeção.

A sequência `pipeline_event_seq` é criada antes das tabelas: ela dá a ordem
total dos eventos, usada como cursor de replay e de reconexão do painel. O
valor é lido explicitamente em `emit.py` (`SELECT nextval`) em vez de ser um
default da coluna, porque o emissor precisa do número em mãos para publicar o
evento no WebSocket junto com a gravação.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
        ("artifacts", "0009_documenttext_dados_estruturados"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE SEQUENCE IF NOT EXISTS pipeline_event_seq AS bigint START WITH 1 INCREMENT BY 1;",
            reverse_sql="DROP SEQUENCE IF EXISTS pipeline_event_seq;",
        ),
        migrations.CreateModel(
            name="PipelineEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sequence", models.BigIntegerField(editable=False, unique=True)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("causation_id", models.UUIDField(blank=True, null=True)),
                ("source", models.CharField(choices=[
                    ("extensao", "Extensão do navegador"),
                    ("orchestrator", "Orquestrador"),
                    ("portal", "Portal (web)"),
                    ("worker", "Worker Celery"),
                    ("beat", "Celery Beat"),
                    ("mcp", "MCP"),
                ], max_length=20)),
                ("stage", models.CharField(max_length=80)),
                ("status", models.CharField(choices=[
                    ("iniciado", "Iniciado"),
                    ("ok", "Concluído"),
                    ("vazio", "Sem resultado"),
                    ("ignorado", "Ignorado"),
                    ("retentando", "Retentando"),
                    ("falhou", "Falhou"),
                ], max_length=20)),
                ("subject_type", models.CharField(blank=True, max_length=40)),
                ("subject_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("message", models.TextField(blank=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("hostname", models.CharField(blank=True, max_length=120)),
                ("process_id", models.IntegerField(blank=True, null=True)),
                ("celery_task_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("celery_task_name", models.CharField(blank=True, max_length=200)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("schema_version", models.SmallIntegerField(default=1)),
                ("tenant", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pipeline_events", to="accounts.organization")),
                ("user", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pipeline_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "pipeline_event",
                "ordering": ["-sequence"],
                "default_permissions": (),
                "permissions": [("add_pipelineevent", "Pode registrar evento de pipeline")],
            },
        ),
        migrations.CreateModel(
            name="PipelineRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("correlation_id", models.UUIDField(unique=True)),
                ("url", models.CharField(blank=True, max_length=2048)),
                ("titulo", models.CharField(blank=True, max_length=500)),
                ("status", models.CharField(choices=[
                    ("em_andamento", "Em andamento"),
                    ("concluido", "Concluído"),
                    ("parcial", "Concluído com ressalvas"),
                    ("falhou", "Falhou"),
                    ("ignorado", "Ignorado"),
                ], default="em_andamento", max_length=20)),
                ("etapas", models.JSONField(blank=True, default=dict)),
                ("iniciado_em", models.DateTimeField()),
                ("atualizado_em", models.DateTimeField()),
                ("concluido_em", models.DateTimeField(blank=True, null=True)),
                ("duracao_ms", models.IntegerField(blank=True, null=True)),
                ("total_eventos", models.IntegerField(default=0)),
                ("total_falhas", models.IntegerField(default=0)),
                ("ultimo_evento_sequence", models.BigIntegerField(default=0)),
                ("artifact", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pipeline_runs", to="artifacts.artifact")),
                ("tenant", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pipeline_runs", to="accounts.organization")),
                ("user", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pipeline_runs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "pipeline_run",
                "ordering": ["-iniciado_em"],
            },
        ),
        migrations.AddIndex(
            model_name="pipelineevent",
            index=models.Index(fields=["correlation_id", "sequence"], name="pipeline_evt_corr_seq_idx"),
        ),
        migrations.AddIndex(
            model_name="pipelineevent",
            index=models.Index(fields=["tenant", "-occurred_at"], name="pipeline_evt_tenant_dt_idx"),
        ),
        migrations.AddIndex(
            model_name="pipelineevent",
            index=models.Index(fields=["stage", "status"], name="pipeline_evt_stage_status_idx"),
        ),
        migrations.AddIndex(
            model_name="pipelineevent",
            index=models.Index(fields=["-sequence"], name="pipeline_evt_seq_desc_idx"),
        ),
        migrations.AddIndex(
            model_name="pipelinerun",
            index=models.Index(fields=["tenant", "-iniciado_em"], name="pipeline_run_tenant_dt_idx"),
        ),
        migrations.AddIndex(
            model_name="pipelinerun",
            index=models.Index(fields=["status"], name="pipeline_run_status_idx"),
        ),
    ]

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="artifact",
            name="artifact_type",
            field=models.CharField(
                choices=[
                    ("pessoa", "Pessoa Física"),
                    ("empresa", "Pessoa Jurídica"),
                    ("documento", "Documento"),
                    ("processo", "Processo"),
                    ("endereco", "Endereço"),
                    ("evento", "Evento"),
                    ("texto", "Texto Extraído"),
                    ("fragmento", "Fragmento"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ArtifactLineage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("transformation", models.CharField(max_length=50)),
                ("processor", models.CharField(max_length=100)),
                ("parameters", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "child",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="parent_lineage",
                        to="artifacts.artifact",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children_lineage",
                        to="artifacts.artifact",
                    ),
                ),
            ],
            options={
                "db_table": "artifacts_lineage",
            },
        ),
    ]

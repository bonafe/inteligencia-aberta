import django.db.models.deletion
import uuid
from django.db import migrations, models


def cleanup_pipeline_artifacts(apps, schema_editor):
    Artifact = apps.get_model("artifacts", "Artifact")
    ArtifactLineage = apps.get_model("artifacts", "ArtifactLineage")

    pipeline_ids = list(
        Artifact.objects.filter(artifact_type__in=["texto", "fragmento"]).values_list("id", flat=True)
    )
    if not pipeline_ids:
        return

    ArtifactLineage.objects.filter(parent_id__in=pipeline_ids).delete()
    ArtifactLineage.objects.filter(child_id__in=pipeline_ids).delete()
    Artifact.objects.filter(id__in=pipeline_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0003_urlpatterncache"),
    ]

    operations = [
        migrations.RunPython(cleanup_pipeline_artifacts, migrations.RunPython.noop),
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
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="DocumentText",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text", models.TextField()),
                ("title", models.CharField(blank=True, max_length=500)),
                ("source_url", models.CharField(blank=True, max_length=2048)),
                ("page_type", models.CharField(blank=True, max_length=50)),
                ("detection_confidence", models.FloatField(blank=True, null=True)),
                ("detection_source", models.CharField(blank=True, max_length=50)),
                ("structured_data", models.JSONField(blank=True, null=True)),
                ("extractor_version", models.CharField(blank=True, max_length=100)),
                ("char_count", models.IntegerField(default=0)),
                ("word_count", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extracted_text",
                        to="artifacts.artifact",
                    ),
                ),
                (
                    "url_pattern_cache",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="artifacts.urlpatterncache",
                    ),
                ),
            ],
            options={"db_table": "artifacts_document_text"},
        ),
        migrations.CreateModel(
            name="DocumentFragment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text", models.TextField()),
                ("fragment_index", models.IntegerField()),
                ("total_fragments", models.IntegerField()),
                ("qdrant_point_id", models.CharField(blank=True, max_length=100)),
                ("qdrant_collection", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document_text",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fragments",
                        to="artifacts.documenttext",
                    ),
                ),
            ],
            options={
                "db_table": "artifacts_document_fragment",
                "ordering": ["fragment_index"],
            },
        ),
    ]

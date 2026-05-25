import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("artifacts", "0002_artifact_texto_fragmento_artifactlineage"),
    ]

    operations = [
        migrations.CreateModel(
            name="URLPatternCache",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("domain", models.CharField(max_length=255)),
                ("path_pattern", models.CharField(max_length=1024)),
                ("page_type", models.CharField(max_length=50)),
                ("extractor_config", models.JSONField(default=dict)),
                ("confidence", models.FloatField()),
                ("hit_count", models.PositiveIntegerField(default=1)),
                ("divergence_count", models.PositiveIntegerField(default=0)),
                ("needs_review", models.BooleanField(default=False)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="url_pattern_caches",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={"db_table": "artifacts_url_pattern_cache"},
        ),
        migrations.AddConstraint(
            model_name="urlpatterncache",
            constraint=models.UniqueConstraint(
                fields=("tenant", "domain", "path_pattern"),
                name="unique_tenant_domain_path",
            ),
        ),
        migrations.AddIndex(
            model_name="urlpatterncache",
            index=models.Index(fields=["tenant", "domain"], name="artifacts_url_tenant_domain_idx"),
        ),
    ]

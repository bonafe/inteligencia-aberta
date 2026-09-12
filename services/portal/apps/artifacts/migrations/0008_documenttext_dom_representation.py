from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0007_urlpatterncache_schema_failure_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenttext",
            name="dom_representation",
            field=models.TextField(blank=True, null=True),
        ),
    ]

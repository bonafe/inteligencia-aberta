from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0006_urlpatterncache_structure_fingerprint"),
    ]

    operations = [
        migrations.AddField(
            model_name="urlpatterncache",
            name="schema_failure_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0005_urlpatterncache_detection_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="urlpatterncache",
            name="structure_fingerprint",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AlterUniqueTogether(
            name="urlpatterncache",
            unique_together={("tenant", "domain", "path_pattern", "structure_fingerprint")},
        ),
    ]

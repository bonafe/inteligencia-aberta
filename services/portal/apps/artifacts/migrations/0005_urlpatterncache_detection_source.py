from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0004_pipeline_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="urlpatterncache",
            name="detection_source",
            field=models.CharField(default="structural_analysis", max_length=30),
        ),
    ]

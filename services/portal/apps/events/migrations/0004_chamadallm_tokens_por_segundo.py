from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0003_chamadallm"),
    ]

    operations = [
        migrations.AddField(
            model_name="chamadallm",
            name="tokens_por_segundo",
            field=models.FloatField(blank=True, null=True),
        ),
    ]

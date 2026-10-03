from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0011_comparacao_maquina_estruturacaollm_maquina"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenttext",
            name="full_text",
            field=models.TextField(blank=True, null=True),
        ),
    ]

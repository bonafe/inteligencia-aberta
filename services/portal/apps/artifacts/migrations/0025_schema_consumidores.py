"""`SchemaPagina.alimenta_gastos` → `consumidores` (ADR 022): o núcleo deixa de ter um campo de domínio.

A chave `fin.movimentos` aparece aqui só para não perder o que já estava ligado; daqui em diante
o núcleo não conhece consumidor nenhum pelo nome.
"""

from django.db import migrations, models


def para_consumidores(apps, schema_editor):
    SchemaPagina = apps.get_model("artifacts", "SchemaPagina")
    SchemaPagina.objects.filter(alimenta_gastos=True).update(consumidores=["fin.movimentos"])


def para_alimenta_gastos(apps, schema_editor):
    SchemaPagina = apps.get_model("artifacts", "SchemaPagina")
    for s in SchemaPagina.objects.exclude(consumidores=[]):
        s.alimenta_gastos = "fin.movimentos" in (s.consumidores or [])
        s.save(update_fields=["alimenta_gastos"])


class Migration(migrations.Migration):
    dependencies = [
        ("artifacts", "0024_papeis_nos_schemas"),
    ]

    operations = [
        migrations.AddField(
            model_name="schemapagina",
            name="consumidores",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(para_consumidores, para_alimenta_gastos),
        migrations.RemoveField(model_name="schemapagina", name="alimenta_gastos"),
    ]

"""Marca de evento avulso (que não pertence a nenhuma captura).

Antes disto a regra vivia apenas em `emit()`, e a reconstrução da projeção
materializava uma "execução" para cada worker que subira — divergindo do
caminho incremental, que é justamente o que o modelo promete não fazer.
"""

from django.db import migrations, models


def marcar_avulsos_existentes(apps, schema_editor):
    """Eventos já gravados: infere pela etapa, que é o único sinal disponível."""
    PipelineEvent = apps.get_model("events", "PipelineEvent")
    PipelineEvent.objects.filter(
        stage__in=["worker.pronto", "worker.encerrando", "catchup.varredura"]
    ).update(avulso=True)


class Migration(migrations.Migration):

    dependencies = [("events", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="pipelineevent",
            name="avulso",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(marcar_avulsos_existentes, migrations.RunPython.noop),
    ]

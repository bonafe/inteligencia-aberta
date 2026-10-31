"""Resultados de exames: projeção das alegações `saude:` da leitura escolhida de cada laudo
(`exames.py`). Nunca editar à mão; tudo aqui nasce `confidencial` (o piso de `saude:Exame`)."""

import uuid

from django.db import models

from apps.accounts.models import Organization
from apps.artifacts.models import Artifact


class LeituraExames(models.Model):
    """Qual leitura de um laudo alimenta a Saúde: uma por documento (as outras seguem ativas)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="leituras_exames")
    artefato = models.OneToOneField(Artifact, on_delete=models.CASCADE, related_name="leitura_exames")
    #: Prefixo dos sujeitos das alegações desta leitura (`mencao:<blob>#parser/<schema>` ou `#estruturacao/<id>`).
    base = models.CharField(max_length=500)
    escolhida_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saude_leitura"


class ResultadoExame(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="resultados_exame")
    artefato = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="resultados_exame")
    sujeito_ref = models.CharField(max_length=500)
    indice = models.PositiveIntegerField()
    #: Como está no laudo e a chave para juntar o histórico ("Hemoglobina glicada" → "HEMOGLOBINA GLICADA").
    analito = models.CharField(max_length=300)
    analito_chave = models.CharField(max_length=300, db_index=True)
    resultado = models.CharField(max_length=200)
    #: O resultado como número, quando é um ("5,4" → 5.4); "Não reagente" fica sem.
    valor = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unidade = models.CharField(max_length=50, blank=True)
    referencia = models.CharField(max_length=300, blank=True)
    material = models.CharField(max_length=100, blank=True)
    data = models.DateField(null=True, blank=True)
    data_texto = models.CharField(max_length=100, blank=True)
    laboratorio = models.CharField(max_length=200, blank=True)
    classification_level = models.CharField(max_length=20, choices=Artifact.ClassificationLevel.choices)

    class Meta:
        db_table = "saude_resultado"
        ordering = ["analito_chave", "data", "indice"]
        constraints = [
            models.UniqueConstraint(fields=["artefato", "sujeito_ref"], name="uniq_resultado_artefato_sujeito"),
        ]

    def __str__(self):
        return f"{self.analito} {self.resultado} {self.unidade}"

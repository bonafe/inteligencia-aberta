"""Gastos a partir das faturas: o que a pessoa **sabe** sobre os lançamentos que a fatura **diz**.

A fonte é a alegação (`apps.artifacts.models.Claim`, produtor `estruturacao-llm` ou
`parser-pagina`): cada lançamento é um sujeito `mencao:<blob>#estruturacao/<id>/lancamento/<n>`
(leitura por LLM) ou `mencao:<blob>#parser/<schema>/lancamento/<n>` (parser aprovado, sem LLM)
com `ia:data`, `ia:valor` e `ia:descricao`, sempre com evidência. `Lancamento` é a **projeção** dessas alegações para somar —
reconstruível a qualquer momento (`reconstruir_lancamentos`), como o `PipelineRun` é do log de
eventos. `Estabelecimento`, `DescritorEstabelecimento` e `CategoriaGasto` são estado da pessoa
(fusões e categorias) e sobrevivem à reconstrução.
"""

import uuid

from django.db import models

from apps.accounts.models import Organization
from apps.artifacts.models import Artifact


class CategoriaGasto(models.Model):
    """Categoria definida pela organização (mercado, restaurante...). Vale por estabelecimento."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="categorias_gasto")
    nome = models.CharField(max_length=80)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_categoria"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["tenant", "nome"], name="uniq_categoria_tenant_nome")]

    def __str__(self):
        return self.nome


class Estabelecimento(models.Model):
    """Um lugar onde se gasta. Agrupa um ou mais descritores de maquininha (fusão manual)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="estabelecimentos")
    nome = models.CharField(max_length=200)
    categoria = models.ForeignKey(
        CategoriaGasto, null=True, blank=True, on_delete=models.SET_NULL, related_name="estabelecimentos",
    )
    #: Referência tipada (`cnpj:…`) quando a pessoa a informar; a fatura não traz CNPJ.
    empresa_ref = models.CharField(max_length=500, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_estabelecimento"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class DescritorEstabelecimento(models.Model):
    """O texto da maquininha, normalizado, e o estabelecimento a que pertence."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="descritores_estabelecimento")
    descritor = models.CharField(max_length=300)
    estabelecimento = models.ForeignKey(Estabelecimento, on_delete=models.CASCADE, related_name="descritores")

    class Meta:
        db_table = "financeiro_descritor"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "descritor"], name="uniq_descritor_tenant"),
        ]

    def __str__(self):
        return self.descritor


class Lancamento(models.Model):
    """Projeção de um lançamento alegado por uma fatura. Nunca editar à mão: reconstruir."""

    class Tipo(models.TextChoices):
        COMPRA = "compra", "Compra"
        TARIFA = "tarifa", "Tarifa/imposto"
        ESTORNO = "estorno", "Estorno"
        PAGAMENTO = "pagamento", "Pagamento da fatura"

    class Fonte(models.TextChoices):
        ESTRUTURACAO = "estruturacao", "Estruturação por LLM"
        PARSER = "parser", "Parser do tipo de página"

    #: Tipos que entram na soma de gastos (estorno entra negativo e abate).
    TIPOS_GASTO = (Tipo.COMPRA, Tipo.TARIFA, Tipo.ESTORNO)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="lancamentos")
    artefato = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="lancamentos")
    fonte = models.CharField(max_length=20, choices=Fonte.choices, default=Fonte.ESTRUTURACAO)
    #: A `EstruturacaoLLM` ou o `SchemaPagina` que leu os lançamentos (conforme `fonte`).
    fonte_id = models.UUIDField()
    indice = models.PositiveIntegerField()
    sujeito_ref = models.CharField(max_length=500)
    data = models.DateField(null=True, blank=True)
    data_texto = models.CharField(max_length=100, blank=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valor_texto = models.CharField(max_length=100, blank=True)
    descricao = models.CharField(max_length=500)
    secao = models.CharField(max_length=200, blank=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    estabelecimento = models.ForeignKey(
        Estabelecimento, null=True, blank=True, on_delete=models.SET_NULL, related_name="lancamentos",
    )
    classification_level = models.CharField(max_length=20, choices=Artifact.ClassificationLevel.choices)

    class Meta:
        db_table = "financeiro_lancamento"
        ordering = ["data", "indice"]
        constraints = [
            models.UniqueConstraint(fields=["artefato", "sujeito_ref"], name="uniq_lancamento_artefato_sujeito"),
        ]

    def __str__(self):
        return f"{self.data_texto} {self.descricao} {self.valor_texto}"


class LeituraLancamentos(models.Model):
    """Qual leitura de um documento alimenta os Gastos: uma por documento.

    As alegações de todas as leituras (cada estruturação por LLM, o parser) continuam ativas —
    são o que cada produtor leu, nada foi desmentido. Escolher outra leitura é estado da pessoa,
    não retratação: trocar e voltar não perde nada, e a fatura nunca é somada duas vezes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="leituras_lancamentos")
    artefato = models.OneToOneField(Artifact, on_delete=models.CASCADE, related_name="leitura_lancamentos")
    #: Prefixo dos sujeitos das alegações desta leitura (`mencao:<blob>#estruturacao/<id>` ou `#parser/<schema>`).
    base = models.CharField(max_length=500)
    fonte = models.CharField(max_length=20, choices=Lancamento.Fonte.choices)
    fonte_id = models.UUIDField()
    escolhida_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "financeiro_leitura"

    def __str__(self):
        return f"{self.fonte}:{self.fonte_id} para {self.artefato_id}"

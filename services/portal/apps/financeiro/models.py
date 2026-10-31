"""Gastos a partir de faturas e extratos: o que a pessoa **sabe** sobre os movimentos que os
documentos **dizem** (ADR 021).

A fonte é a alegação (`apps.artifacts.models.Claim`, produtor `estruturacao-llm` ou
`parser-pagina`): cada movimento é um sujeito `mencao:<blob>#estruturacao/<id>/lancamento/<n>`
(leitura por LLM) ou `mencao:<blob>#parser/<schema>/lancamento/<n>` (parser aprovado, sem LLM)
com `ia:data`, `ia:valor` e `ia:descricao`, sempre com evidência. `Movimento` é a **projeção**
dessas alegações — reconstruível a qualquer momento (`reconstruir_lancamentos`), como o
`PipelineRun` é do log de eventos. `Conta`, `Estabelecimento`, `DescritorEstabelecimento` e
`CategoriaGasto` são estado da pessoa (apelidos, fusões e categorias) e sobrevivem à reconstrução.
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


class Conta(models.Model):
    """Um cartão ou uma conta da pessoa (ADR 021). Estado da pessoa: sobrevive à reconstrução.

    Nasce na primeira vez que um documento a identifica (o campo de papel `identifica_conta` do
    schema: `6516.XXXX.XXXX.1402`, agência/conta). Documento que não traz identificador cai na
    conta "sem número" da instituição e do tipo; juntar contas leva os identificadores junto.
    `propria` é o que, na conciliação, faz de um Pix entre duas contas uma transferência interna.
    """

    class Tipo(models.TextChoices):
        CARTAO = "cartao", "Cartão"
        CORRENTE = "corrente", "Conta corrente"
        POUPANCA = "poupanca", "Poupança"
        INVESTIMENTO = "investimento", "Investimento"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="contas")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    #: Domínio de onde vêm os documentos (www2.bb.com.br) ou o nome da instituição.
    instituicao = models.CharField(max_length=255)
    #: Os identificadores que os documentos trazem; "" é o documento que não trouxe nenhum.
    identificadores = models.JSONField(default=list)
    apelido = models.CharField(max_length=120, blank=True)
    titular = models.CharField(max_length=200, blank=True)
    propria = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financeiro_conta"
        ordering = ["tipo", "instituicao", "apelido"]

    @property
    def nome(self) -> str:
        if self.apelido:
            return self.apelido
        numeros = [i for i in self.identificadores if i]
        final = f" {numeros[0][-4:]}" if numeros else " (sem número)"
        return f"{self.get_tipo_display()} {self.instituicao}{final}"

    def __str__(self):
        return self.nome


class Movimento(models.Model):
    """Um movimento de uma conta, projetado das alegações da leitura escolhida do documento.
    Nunca editar à mão: reconstruir (`reconstruir_lancamentos`).

    `valor` tem o sinal **da conta**: negativo é dinheiro que sai (uma compra no cartão, um Pix
    enviado), positivo é o que entra (o pagamento que quita a fatura, um salário). `competencia`
    é o mês em que o dinheiro saiu de fato: o do vencimento da fatura que cobrou — a parcela de
    uma compra de maio cobrada em outubro é de outubro —, ou o da própria data no extrato.
    """

    class Natureza(models.TextChoices):
        COMPRA = "compra", "Compra"
        TARIFA = "tarifa", "Tarifa/imposto"
        ESTORNO = "estorno", "Estorno"
        SAIDA_TERCEIROS = "saida_terceiros", "Saída a terceiros"
        PAGAMENTO_FATURA = "pagamento_fatura", "Pagamento de fatura"
        TRANSFERENCIA_INTERNA = "transferencia_interna", "Transferência entre contas próprias"
        APLICACAO = "aplicacao", "Aplicação"
        RESGATE = "resgate", "Resgate"
        ENTRADA = "entrada", "Entrada"
        OUTRO = "outro", "Outro"

    class Fonte(models.TextChoices):
        ESTRUTURACAO = "estruturacao", "Estruturação por LLM"
        PARSER = "parser", "Parser do tipo de página"

    #: Naturezas que são consumo: entram na soma de gastos (o estorno, positivo, abate). As
    #: demais são dinheiro mudando de bolso entre contas próprias ou entrando.
    CONSUMO = (Natureza.COMPRA, Natureza.TARIFA, Natureza.SAIDA_TERCEIROS, Natureza.ESTORNO)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="movimentos")
    artefato = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="movimentos")
    conta = models.ForeignKey(Conta, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimentos")
    fonte = models.CharField(max_length=20, choices=Fonte.choices)
    #: A `EstruturacaoLLM` ou o `SchemaPagina` que leu o documento (conforme `fonte`).
    fonte_id = models.UUIDField()
    indice = models.PositiveIntegerField()
    sujeito_ref = models.CharField(max_length=500)
    data = models.DateField(null=True, blank=True)
    data_texto = models.CharField(max_length=100, blank=True)
    competencia = models.DateField(null=True, blank=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valor_texto = models.CharField(max_length=100, blank=True)
    descricao = models.CharField(max_length=500)
    documento = models.CharField(max_length=100, blank=True)
    secao = models.CharField(max_length=200, blank=True)
    natureza = models.CharField(max_length=30, choices=Natureza.choices)
    parcela = models.PositiveSmallIntegerField(null=True, blank=True)
    parcelas = models.PositiveSmallIntegerField(null=True, blank=True)
    estabelecimento = models.ForeignKey(
        Estabelecimento, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimentos",
    )
    classification_level = models.CharField(max_length=20, choices=Artifact.ClassificationLevel.choices)

    class Meta:
        db_table = "financeiro_movimento"
        ordering = ["data", "indice"]
        constraints = [
            models.UniqueConstraint(fields=["artefato", "sujeito_ref"], name="uniq_movimento_artefato_sujeito"),
        ]
        indexes = [models.Index(fields=["tenant", "competencia"])]

    @property
    def gasto(self):
        """O valor como gasto (positivo), para mostrar: a saída de uma compra vira R$ 93,50."""
        return -self.valor if self.valor is not None else None

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
    fonte = models.CharField(max_length=20, choices=Movimento.Fonte.choices)
    fonte_id = models.UUIDField()
    escolhida_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "financeiro_leitura"

    def __str__(self):
        return f"{self.fonte}:{self.fonte_id} para {self.artefato_id}"

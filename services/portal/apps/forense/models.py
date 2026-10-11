import uuid

from django.db import models


class ProcessamentoIPED(models.Model):
    """Um pedido para o IPED processar uma pasta do servidor — ADR 019.

    A origem é sempre **uma raiz permitida + um caminho relativo** (`apps.forense.caminhos`),
    nunca um caminho livre. O processamento roda no `worker-iped` (fila `iped`); o caso gerado
    fica em `IPED_SAIDA_DIR/<id>`. Esta tabela só guarda o estado do pedido, não o caso.
    """

    class Estado(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EXECUTANDO = "executando", "Executando"
        CONCLUIDO = "concluido", "Concluído"
        FALHOU = "falhou", "Falhou"
        CANCELADO = "cancelado", "Cancelado"

    #: Perfis do IPED aceitos (nomes das pastas em `profiles/`). Lista fechada: vai para a linha de comando.
    PERFIS = ("forensic", "triage", "fastmode", "pedo", "blind")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organizacao = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="processamentos_iped")
    criado_por = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    raiz = models.CharField(max_length=500)
    relativo = models.CharField(max_length=1000)
    perfil = models.CharField(max_length=20, default="fastmode")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDENTE)
    celery_task_id = models.CharField(max_length=100, blank=True)
    versao_iped = models.CharField(max_length=40, blank=True)
    codigo_saida = models.IntegerField(null=True, blank=True)
    erro = models.TextField(blank=True)
    resumo = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "forense_processamento_iped"
        constraints = [
            models.CheckConstraint(
                check=models.Q(perfil__in=["forensic", "triage", "fastmode", "pedo", "blind"]),
                name="forense_perfil_valido",
            ),
        ]
        ordering = ["-criado_em"]

    def __str__(self):
        return f"IPED {self.id} ({self.estado})"

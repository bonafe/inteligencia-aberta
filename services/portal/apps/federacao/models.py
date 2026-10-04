import uuid

from django.db import models
from django.db.models import Q


class ChaveInstancia(models.Model):
    """Par de chaves Ed25519 da instância — ADR 010.

    Hoje a instância é a raiz de confiança: ela assina, em nome dos usuários
    locais, tudo o que a federação vier a trocar. A chave privada fica **cifrada
    em repouso** (mesmo Fernet de `apps.infrastructure.crypto`) e nunca sai
    deste model: quem precisa assinar chama `assinar()`.

    A chave pública não é guardada à parte porque o `did` (`did:key`) a contém.
    O chaveiro por usuário e a rotação virão com o log de eventos; por ora há
    no máximo uma chave `ativa`.
    """

    class Estado(models.TextChoices):
        ATIVA = "ativa", "Ativa"
        APOSENTADA = "aposentada", "Aposentada"
        COMPROMETIDA = "comprometida", "Comprometida"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    did = models.CharField(max_length=100, unique=True)
    privada_cifrada = models.TextField()
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ATIVA)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "federacao_chave_instancia"
        constraints = [
            models.UniqueConstraint(
                fields=["estado"], condition=Q(estado="ativa"),
                name="uma_chave_ativa_por_instancia",
            ),
        ]

    def __str__(self):
        return f"{self.did} ({self.estado})"

    def assinar(self, mensagem: bytes) -> bytes:
        """Assinatura Ed25519 de 64 bytes sobre `mensagem`."""
        from .chaves import assinar_com

        return assinar_com(self, mensagem)


class Space(models.Model):
    """Espaço: o agrupamento de objetos que se exporta, sincroniza e sobre o qual
    incidem as regras de replicação (ADR 010, ADR 011). Hoje é só **estrutura**:
    não concede acesso a ninguém — o acesso segue sendo por organização
    (`orgs_do_usuario`) — e ainda não tem membros.

    Pertence a uma **organização local** (decisão P7: o espaço é o limite e aponta
    para uma organização do banco local). Um artefato pode estar em vários espaços.

    O **espaço padrão** de cada organização é implícito e **não tem linha aqui**:
    é, por definição, "todos os artefatos da organização" (ver
    `apps.federacao.espacos.espaco_padrao`).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(max_length=120)
    descricao = models.TextField(blank=True)
    organizacao = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="espacos")
    criado_por = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    arquivado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "federacao_space"
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "nome"], name="uniq_space_organizacao_nome"),
        ]

    def __str__(self):
        return self.nome

    @property
    def urn(self) -> str:
        from .ids import urn_de

        return urn_de(self.id)


class EspacoArtefato(models.Model):
    """Um artefato dentro de um espaço explícito (o espaço padrão não usa esta tabela)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    espaco = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="itens")
    artefato = models.ForeignKey("artifacts.Artifact", on_delete=models.CASCADE, related_name="espaco_itens")
    incluido_por = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    incluido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "federacao_espaco_artefato"
        constraints = [
            models.UniqueConstraint(fields=["espaco", "artefato"], name="uniq_espaco_artefato"),
        ]

    def __str__(self):
        return f"{self.artefato_id} em {self.espaco}"

    def clean(self):
        from django.core.exceptions import ValidationError

        # Pelo admin só entra artefato da mesma organização do espaço. O objeto
        # importado (que mantém o tenant de origem) passa por `espacos.incluir_artefato`.
        if self.espaco_id and self.artefato_id and self.artefato.tenant_id != self.espaco.organizacao_id:
            raise ValidationError("O artefato é de outra organização que a do espaço.")

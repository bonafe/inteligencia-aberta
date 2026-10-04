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

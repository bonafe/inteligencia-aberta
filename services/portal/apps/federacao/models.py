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


class RegraReplicacao(models.Model):
    """Uma regra do motor de replicação (ADR 011), de uma organização.

    As **padrão** (`padrao=True`, com `chave_padrao`) são semeadas por organização e
    **desativadas, não apagadas** (o admin não deixa apagá-las): apagar uma faria a
    semeadura explícita trazê-la de volta. `None` numa condição significa "qualquer".

    Uma **concessão** é uma regra de *permitir* com `objeto_urn` — que por isso
    **exige** `valida_ate` — e `par_ref`; é a única coisa que faz um nível `restrito`
    ou `confidencial` sair para um terceiro. Revogar é desativar (`revogada_em`).

    O motor em si é puro e fica em `apps.federacao.regras`; o resto da lógica de banco,
    em `apps.federacao.politica`.
    """

    class Efeito(models.TextChoices):
        PERMITIR = "permitir", "Permitir"
        NEGAR = "negar", "Negar"

    class Sentido(models.TextChoices):
        ENVIAR = "enviar", "Ao enviar"
        RECEBER = "receber", "Ao receber"

    class TipoDePar(models.TextChoices):
        PROPRIO = "proprio", "Próprio (mesmo dono)"
        TERCEIRO = "terceiro", "Terceiro"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organizacao = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="regras_replicacao")
    efeito = models.CharField(max_length=10, choices=Efeito.choices)
    sentido = models.CharField(max_length=10, choices=Sentido.choices)
    par_ref = models.CharField(max_length=200, null=True, blank=True, help_text="Nome ou chave do par; vazio = qualquer.")
    par_tipo = models.CharField(max_length=10, choices=TipoDePar.choices, null=True, blank=True)
    nivel = models.CharField(max_length=20, null=True, blank=True)
    tipo_objeto = models.CharField(max_length=30, null=True, blank=True)
    espaco_urn = models.CharField(max_length=60, null=True, blank=True)
    objeto_urn = models.CharField(max_length=60, null=True, blank=True)
    valida_ate = models.DateTimeField(null=True, blank=True)
    ativa = models.BooleanField(default=True)
    revogada_em = models.DateTimeField(null=True, blank=True)
    padrao = models.BooleanField(default=False)
    chave_padrao = models.CharField(max_length=60, null=True, blank=True)
    observacao = models.TextField(blank=True)
    criada_por = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "federacao_regra_replicacao"
        constraints = [
            models.UniqueConstraint(
                fields=["organizacao", "chave_padrao"], condition=Q(chave_padrao__isnull=False),
                name="uniq_regra_replicacao_padrao",
            ),
            models.CheckConstraint(
                check=Q(objeto_urn__isnull=True) | Q(valida_ate__isnull=False),
                name="regra_com_objeto_exige_validade",
            ),
        ]

    def __str__(self):
        alvo = self.objeto_urn or self.par_ref or self.par_tipo or "qualquer par"
        return f"{self.efeito} ({self.sentido}) {self.nivel or 'qualquer nível'} → {alvo}"

    def clean(self):
        from django.core.exceptions import ValidationError

        from . import regras
        from .ids import normalizar

        erros = {}
        if self.nivel is not None and self.nivel not in regras.NIVEIS:
            erros["nivel"] = f"use um de {', '.join(regras.NIVEIS)}"
        for campo in ("espaco_urn", "objeto_urn"):
            valor = getattr(self, campo)
            if valor is not None:
                try:
                    setattr(self, campo, normalizar(valor))
                except ValueError:
                    erros[campo] = "deve ser uma urn:uuid: válida"
        if self.par_ref is not None:
            self.par_ref = self.par_ref.strip() or None
        if self.objeto_urn is not None and self.valida_ate is None:
            erros["valida_ate"] = "uma regra com escopo de objeto (concessão) exige validade"
        if self.objeto_urn is not None and self.efeito == self.Efeito.PERMITIR and not self.par_ref:
            erros["par_ref"] = "uma concessão precisa dizer para qual par"
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        # Validar também fora do admin: uma regra malformada num motor de segurança
        # não pode entrar calada.
        self.clean()
        super().save(*args, **kwargs)

    def como_dados(self):
        from .regras import RegraDados

        return RegraDados(
            id=str(self.id), efeito=self.efeito, sentido=self.sentido, par_ref=self.par_ref,
            par_tipo=self.par_tipo, nivel=self.nivel, tipo_objeto=self.tipo_objeto,
            espaco_urn=self.espaco_urn, objeto_urn=self.objeto_urn, valida_ate=self.valida_ate,
            ativa=self.ativa, padrao=self.padrao,
        )

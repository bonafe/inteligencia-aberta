import uuid
from django.db import models
from apps.accounts.models import User, Organization


class Artifact(models.Model):
    class Type(models.TextChoices):
        PERSON = "pessoa", "Pessoa Física"
        COMPANY = "empresa", "Pessoa Jurídica"
        DOCUMENT = "documento", "Documento"
        PROCESS = "processo", "Processo"
        ADDRESS = "endereco", "Endereço"
        EVENT = "evento", "Evento"

    class ClassificationLevel(models.TextChoices):
        PUBLIC = "publico", "Público"
        INTERNAL = "interno", "Interno"
        RESTRICTED = "restrito", "Restrito"
        CONFIDENTIAL = "confidencial", "Confidencial"

    class InfoType(models.TextChoices):
        FACT = "fato", "Fato"
        OPINION = "opiniao", "Opinião"
        INFERENCE = "inferencia", "Inferência"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact_type = models.CharField(max_length=20, choices=Type.choices)
    content = models.JSONField()

    classification_level = models.CharField(
        max_length=20,
        choices=ClassificationLevel.choices,
        default=ClassificationLevel.RESTRICTED,
    )
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="artifacts")
    allow_external_llm = models.BooleanField(default=False)
    classified_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="classified_artifacts"
    )
    classified_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    info_type = models.CharField(max_length=20, choices=InfoType.choices)
    sources = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifacts_artifact"

    def __str__(self):
        return f"{self.get_artifact_type_display()} / {self.id}"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    organization = models.ForeignKey(
        Organization, null=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    operation = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20)  # "permitido" | "bloqueado"
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        db_table = "audit_log"
        default_permissions = ()
        permissions = [("add_auditlog", "Can add audit log")]

    def __str__(self):
        return f"{self.operation} / {self.outcome} / {self.timestamp}"


class Sharing(models.Model):
    class RecipientType(models.TextChoices):
        USER = "usuario", "Usuário"
        ORGANIZATION = "organizacao", "Organização"

    class Status(models.TextChoices):
        ACTIVE = "ativo", "Ativo"
        EXPIRED = "expirado", "Expirado"
        REVOKED = "revogado", "Revogado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="sharings")
    shared_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_sharings")
    recipient_type = models.CharField(max_length=20, choices=RecipientType.choices)
    recipient_id = models.UUIDField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="revoked_sharings"
    )

    class Meta:
        db_table = "artifacts_sharing"

    def __str__(self):
        return f"{self.artifact} → {self.recipient_type}:{self.recipient_id}"

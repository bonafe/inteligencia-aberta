import uuid
from django.db import models
from apps.accounts.models import Organization


class LLMProvider(models.Model):
    class ProviderType(models.TextChoices):
        EXTERNAL = "external", "Externo"
        LOCAL = "local", "Local"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="llm_providers"
    )
    name = models.CharField(max_length=255)
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices)
    endpoint_url = models.URLField(blank=True, null=True)
    api_key_encrypted = models.CharField(max_length=255, blank=True, null=True)
    model_name = models.CharField(max_length=255)
    allowed_classifications = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "infrastructure_llm_provider"

    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"


class MCPServer(models.Model):
    class HealthStatus(models.TextChoices):
        OK = "ok", "OK"
        DEGRADED = "degraded", "Degradado"
        UNAVAILABLE = "unavailable", "Indisponível"
        UNKNOWN = "unknown", "Desconhecido"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="mcp_servers"
    )
    name = models.CharField(max_length=255)
    endpoint_url = models.URLField()
    auth_token_encrypted = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    health_status = models.CharField(
        max_length=20, choices=HealthStatus.choices, default=HealthStatus.UNKNOWN
    )
    last_health_check = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "infrastructure_mcp_server"

    def __str__(self):
        return self.name


class MCPTool(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    server = models.ForeignKey(
        MCPServer, on_delete=models.CASCADE, related_name="tools"
    )
    tool_name = models.CharField(max_length=255)
    description = models.TextField()
    input_schema = models.JSONField()
    is_enabled = models.BooleanField(default=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "infrastructure_mcp_tool"
        unique_together = ("server", "tool_name")

    def __str__(self):
        return f"{self.server.name} - {self.tool_name}"


class ImageRegistry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="image_registries"
    )
    name = models.CharField(max_length=255)
    registry_url = models.CharField(max_length=255)
    username = models.CharField(max_length=255, blank=True, null=True)
    password_encrypted = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "infrastructure_image_registry"

    def __str__(self):
        return self.name


class ContainerImage(models.Model):
    class ImageType(models.TextChoices):
        AGENT = "agent", "Agente"
        TOOL = "tool", "Ferramenta"
        WORKER = "worker", "Worker"

    class PullStatus(models.TextChoices):
        OK = "ok", "OK"
        FAILED = "failed", "Falhou"
        UNKNOWN = "unknown", "Desconhecido"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registry = models.ForeignKey(
        ImageRegistry, on_delete=models.CASCADE, related_name="images"
    )
    image_name = models.CharField(max_length=255)
    tag = models.CharField(max_length=100)
    image_type = models.CharField(max_length=20, choices=ImageType.choices)
    is_active = models.BooleanField(default=True)
    pull_status = models.CharField(
        max_length=20, choices=PullStatus.choices, default=PullStatus.UNKNOWN
    )
    last_pull_attempt = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "infrastructure_container_image"
        unique_together = ("registry", "image_name", "tag")

    def __str__(self):
        return f"{self.image_name}:{self.tag}"

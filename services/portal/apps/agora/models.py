"""Workspaces do Ultima Agora (docs/especificacao/10 do projeto ultima-agora).

O workspace é uma **composição de interface** (componentes, layout, notas); o que ele guarda aqui é só
identidade, dono, papéis e classificação. O documento colaborativo em si vive no serviço `agora-sync`
(Yjs) e **não contém dados de domínio**, só referências a eles (R-DOC-3). Não confundir com `Space`
(`apps.federacao`): o `Space` agrupa artefatos e carrega política; o workspace pode apontar para um.
"""

import uuid

from django.db import models

from apps.artifacts.models import Artifact


class Workspace(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Dono"
        EDITOR = "editor", "Editor"
        PARTICIPANT = "participant", "Participante"
        VIEWER = "viewer", "Somente leitura"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="workspaces")
    owner = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="owned_workspaces")
    # Piso de classificação do conteúdo autoral (notas, quadro, chat). O padrão é o mais restritivo (R-CLS-1).
    classification = models.CharField(
        max_length=20, choices=Artifact.ClassificationLevel.choices, default=Artifact.ClassificationLevel.RESTRICTED,
    )
    space = models.ForeignKey("federacao.Space", null=True, blank=True, on_delete=models.SET_NULL, related_name="workspaces")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agora_workspace"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class WorkspaceMember(models.Model):
    """Papel explícito de alguém num workspace. Nunca amplia o papel na organização (ver `acesso.py`)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="workspace_memberships")
    role = models.CharField(max_length=20, choices=Workspace.Role.choices, default=Workspace.Role.EDITOR)
    invited_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agora_workspace_member"
        unique_together = ("workspace", "user")

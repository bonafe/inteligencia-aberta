import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        db_table = "accounts_user"


class Organization(models.Model):
    class Type(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        TEAM = "team", "Equipe"
        INSTITUTIONAL = "institutional", "Institucional"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    org_type = models.CharField(max_length=20, choices=Type.choices)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="owned_organizations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_organization"

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Dono"
        ADMIN = "admin", "Administrador"
        MEMBER = "member", "Membro"
        GUEST = "guest", "Convidado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_invitations"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_membership"
        unique_together = ("user", "organization")

    def __str__(self):
        return f"{self.user} → {self.organization} ({self.role})"


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="teams")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_team"

    def __str__(self):
        return f"{self.organization} / {self.name}"

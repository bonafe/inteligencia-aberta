"""Permissões por papel de organização.

Até aqui o portal só isolava por organização (`orgs_do_usuario`): nenhuma tela exigia
papel. Estas funções são o primeiro uso de `Membership.role`, e valem só para quem
as chama — `orgs_do_usuario` continua como está.

Só `OWNER` e `ADMIN` administram. O papel vale enquanto o `Membership` não expirou
(`expires_at`). `is_staff`/superusuário **não** dá poder de administrar uma organização:
quem manda é o papel nela.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .models import Membership, Organization

PAPEIS_ADMIN = frozenset({Membership.Role.OWNER, Membership.Role.ADMIN})


def _vigente(agora):
    return Q(expires_at__isnull=True) | Q(expires_at__gt=agora)


def eh_admin(user, organizacao) -> bool:
    """O usuário é dono ou administrador (vigente) desta organização?"""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return Membership.objects.filter(
        _vigente(timezone.now()), user=user, organization=organizacao, role__in=PAPEIS_ADMIN,
    ).exists()


def papel_do_usuario(user, organizacao) -> str | None:
    """O papel vigente do usuário na organização, ou `None` se não for membro."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    membro = Membership.objects.filter(
        _vigente(timezone.now()), user=user, organization=organizacao,
    ).first()
    return membro.role if membro else None


def orgs_onde_e_admin(user):
    """`QuerySet` das organizações em que o usuário é dono ou administrador vigente."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Organization.objects.none()
    return Organization.objects.filter(
        pk__in=Membership.objects.filter(
            _vigente(timezone.now()), user=user, role__in=PAPEIS_ADMIN,
        ).values("organization_id")
    )


def exige_admin(user, organizacao) -> None:
    """`PermissionDenied` (403) se o usuário não administra a organização."""
    if not eh_admin(user, organizacao):
        raise PermissionDenied("Requer papel de dono ou administrador da organização.")

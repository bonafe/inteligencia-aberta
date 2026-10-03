from django.utils.text import slugify

from .models import Membership, Organization


def criar_organizacao_individual(user) -> Organization:
    """Organização pessoal do usuário, com ele como dono.

    Sem organização o usuário não enxerga nenhum artefato (o isolamento de tenant
    é por Membership); por isso o registro e o bootstrap da instância passam
    ambos por aqui.
    """
    org = Organization.objects.create(
        name=user.username,
        slug=slugify(user.username),
        org_type=Organization.Type.INDIVIDUAL,
        owner=user,
    )
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    return org

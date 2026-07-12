from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Membership


class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Emite o par access/refresh com a identidade de tenant embutida nas claims.

    Assim o orchestrator não precisa consultar o banco nem confiar em campos
    auto-declarados pelo cliente: ele valida a assinatura e lê tenant_id/username
    direto do token. O tenant escolhido é a organização onde o usuário é OWNER
    (a que ele criou no registro); se não houver, a primeira associação.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        membership = (
            Membership.objects.filter(user=user, role=Membership.Role.OWNER).first()
            or Membership.objects.filter(user=user).first()
        )
        token["tenant_id"] = str(membership.organization_id) if membership else None
        token["username"] = user.get_username()
        return token

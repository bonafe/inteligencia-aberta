"""Apoio às views de leitura que uma extensão expõe aos componentes do Agora (`Extensao.dominio_urls`).

A regra do núcleo vale para elas: só o que é das organizações da pessoa, e o que não é dela é 404, como
se não existisse (R-CONN-7). Use `artefatos_visiveis` em vez de `Artifact.objects` direto."""
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import Artifact


def artefatos_visiveis(request):
    return Artifact.objects.filter(tenant__in=orgs_do_usuario(request.user))

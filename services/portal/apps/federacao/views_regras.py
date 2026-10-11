"""Tela `/cluster/regras/`: as regras de replicação da organização, os pares, os espaços e o MER.

Qualquer membro **vê**; **simular** uma decisão ("por que isto não vai para o par X?") é de
dono/administrador, porque a primeira decisão semeia as regras padrão. A edição segue no
admin. A simulação **não registra nada** (nem `PipelineEvent` nem `AuditLog`): é a mesma
função de `regras_replicacao --decidir`.
"""

from django.http import Http404
from django.shortcuts import render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.permissoes import exige_admin, orgs_onde_e_admin
from apps.accounts.views import orgs_do_usuario
from apps.cluster.models import Maquina

from . import mer, politica, regras
from .models import RegraReplicacao, Space


def _org(request, org_id):
    orgs = orgs_do_usuario(request.user)
    if org_id:
        try:
            return orgs.get(pk=org_id)
        except (Organization.DoesNotExist, ValueError):
            raise Http404("Organização não encontrada.") from None
    org = orgs.order_by("name").first()
    if org is None:
        raise Http404("Sem organização.")
    return org


def _contexto(request, org, **extra) -> dict:
    admin = orgs_onde_e_admin(request.user).filter(pk=org.pk).exists()
    todas = RegraReplicacao.objects.filter(organizacao=org).order_by("-padrao", "criada_em")
    return {
        "org": org,
        "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
        "sou_admin": admin,
        "regras_padrao": [r for r in todas if r.padrao],
        "regras_usuario": [r for r in todas if not r.padrao and not r.objeto_urn],
        "concessoes": [r for r in todas if r.objeto_urn],
        "pares": Maquina.objects.filter(organizacao=org, eh_local=False).order_by("apelido"),
        "espacos": Space.objects.filter(organizacao=org).order_by("nome"),
        "entidades": mer.entidades(),
        "relacoes": mer.relacoes(),
        "niveis": regras.NIVEIS,
        **extra,
    }


class RegrasView(View):
    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        return render(request, "federacao/regras.html", _contexto(request, org))

    def post(self, request):
        """Simula uma decisão. Só admin; não grava nada além das regras padrão, se faltarem."""
        org = _org(request, request.POST.get("organizacao"))
        exige_admin(request.user, org)
        dados = {k: (request.POST.get(k) or "").strip() or None
                 for k in ("sentido", "par_tipo", "par_ref", "nivel", "tipo_objeto")}
        erro = None
        if dados["sentido"] not in (regras.ENVIAR, regras.RECEBER):
            erro = "Escolha enviar ou receber."
        elif dados["par_tipo"] not in (regras.PROPRIO, regras.TERCEIRO):
            erro = "Escolha o tipo do par."
        elif dados["nivel"] not in regras.NIVEIS:
            erro = "Escolha um nível de classificação."
        if erro:
            return render(request, "federacao/regras.html", _contexto(request, org, erro=erro, sim=dados), status=400)
        ctx = regras.Contexto(
            sentido=dados["sentido"], par_tipo=dados["par_tipo"], nivel=dados["nivel"],
            par_ref=dados["par_ref"], tipo_objeto=dados["tipo_objeto"],
        )
        decisao = politica.decidir(org, ctx)
        return render(request, "federacao/regras.html", _contexto(request, org, sim=dados, decisao=decisao))

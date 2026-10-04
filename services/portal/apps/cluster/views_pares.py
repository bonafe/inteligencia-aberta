"""Telas de pares: listar, convidar, aceitar convite, conferir, revogar e mudar o tipo.

Todas exigem sessão; **ver** a lista é para qualquer membro da organização, **mudar** algo
é só para dono/administrador (`apps.accounts.permissoes`) — o servidor confere sempre, a tela
só esconde o botão. O código de convite só aparece na resposta que o cria: não é guardado.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.permissoes import exige_admin, orgs_onde_e_admin
from apps.accounts.views import orgs_do_usuario
from apps.federacao.chaves import chave_ativa
from apps.federacao.did import impressao_digital

from . import pares
from .models import Maquina
from .pares import ErroEnrolamento


def _marcado(request, nome: str) -> bool:
    return request.POST.get(nome) in ("on", "true", "1", "sim")


def _org_do_usuario(request, org_id):
    """A organização (do usuário) citada no formulário; 404 se não existir ou não for dele."""
    try:
        return orgs_do_usuario(request.user).get(pk=org_id)
    except (Organization.DoesNotExist, ValueError):
        raise Http404("Organização não encontrada.") from None


def _par_do_usuario(request, par_id) -> Maquina:
    return get_object_or_404(Maquina, pk=par_id, eh_local=False, organizacao__in=orgs_do_usuario(request.user))


def _contexto(request, **extra) -> dict:
    orgs = list(orgs_do_usuario(request.user))
    admin_ids = set(orgs_onde_e_admin(request.user).values_list("id", flat=True))
    lista = list(
        Maquina.objects.filter(organizacao__in=orgs, eh_local=False)
        .select_related("organizacao").order_by("organizacao__name", "apelido")
    )
    for par in lista:
        par.impressao = impressao_digital(par.did) if par.did else ""
        par.pode_administrar = par.organizacao_id in admin_ids
    chave = chave_ativa()
    return {
        "pares": lista,
        "organizacoes_admin": [o for o in orgs if o.id in admin_ids],
        "sou_admin": bool(admin_ids),
        "meu_did": chave.did if chave else "",
        "minha_impressao": impressao_digital(chave.did) if chave else "",
        "tipos": Maquina.Tipo.choices,
        **extra,
    }


class ParesView(View):
    def get(self, request):
        return render(request, "cluster/pares.html", _contexto(request))


class _AcaoComErro(View):
    """Base: executa a ação, volta à lista com mensagem; erro de regra de negócio vira 400 com a tela."""

    def _erro(self, request, exc, **extra):
        return render(request, "cluster/pares.html", _contexto(request, erro=str(exc), **extra), status=400)


class CriarConviteView(_AcaoComErro):
    def post(self, request):
        org = _org_do_usuario(request, request.POST.get("organizacao"))
        try:
            convite, codigo = pares.criar_convite(
                org, request.user, tipo=request.POST.get("tipo", Maquina.Tipo.TERCEIRO),
                confirmou_proprio=_marcado(request, "confirmo_proprio"),
            )
        except ErroEnrolamento as exc:
            return self._erro(request, exc)
        return render(request, "cluster/pares.html", _contexto(
            request, convite_codigo=codigo, convite_expira_h=pares.VALIDADE_CONVITE_H, convite_org=org))


class PreverConviteView(_AcaoComErro):
    """Mostra o que o código diz (nome, endereço, **impressão digital**) antes de aceitar."""

    def post(self, request):
        org = _org_do_usuario(request, request.POST.get("organizacao"))
        exige_admin(request.user, org)
        try:
            dados = pares.decodificar_codigo(request.POST.get("codigo", ""))
        except ErroEnrolamento as exc:
            return self._erro(request, exc)
        previa = {**dados, "impressao": impressao_digital(dados["did"]), "codigo": request.POST["codigo"].strip(), "org": org}
        return render(request, "cluster/pares.html", _contexto(request, previa=previa))


class AceitarConviteView(_AcaoComErro):
    def post(self, request):
        org = _org_do_usuario(request, request.POST.get("organizacao"))
        try:
            par = pares.aceitar_convite(
                org, request.user, request.POST.get("codigo", ""), request.POST.get("tipo", Maquina.Tipo.TERCEIRO),
                confirmou_proprio=_marcado(request, "confirmo_proprio"),
            )
        except ErroEnrolamento as exc:
            try:
                dados = pares.decodificar_codigo(request.POST.get("codigo", ""))
                previa = {**dados, "impressao": impressao_digital(dados["did"]), "codigo": request.POST["codigo"].strip(), "org": org}
            except ErroEnrolamento:
                previa = None
            return self._erro(request, exc, previa=previa)
        messages.success(request, f"“{par.apelido}” foi cadastrada como par pendente. Confira a impressão digital com o outro administrador e confirme.")
        return redirect("cluster:pares")


class ConfirmarParView(_AcaoComErro):
    def post(self, request, par_id):
        par = _par_do_usuario(request, par_id)
        if not _marcado(request, "conferi"):
            return self._erro(request, "Confirme que você conferiu a impressão digital com o outro administrador.")
        try:
            pares.confirmar(par, request.user)
        except ErroEnrolamento as exc:
            return self._erro(request, exc)
        messages.success(request, f"“{par.apelido}” confirmado.")
        return redirect("cluster:pares")


class RevogarParView(_AcaoComErro):
    def post(self, request, par_id):
        par = _par_do_usuario(request, par_id)
        try:
            pares.revogar(par, request.user)
        except ErroEnrolamento as exc:
            return self._erro(request, exc)
        messages.success(request, f"“{par.apelido}” revogado. O que já foi copiado não é recolhido.")
        return redirect("cluster:pares")


class DefinirTipoParView(_AcaoComErro):
    def post(self, request, par_id):
        par = _par_do_usuario(request, par_id)
        try:
            pares.definir_tipo(par, request.user, request.POST.get("tipo", ""), confirmou_proprio=_marcado(request, "confirmo_proprio"))
        except ErroEnrolamento as exc:
            return self._erro(request, exc)
        messages.success(request, f"“{par.apelido}” agora é {par.get_tipo_display().lower()}.")
        return redirect("cluster:pares")

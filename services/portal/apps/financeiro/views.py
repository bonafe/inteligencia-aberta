"""Tela `/gastos/`: quanto se gastou por categoria e por estabelecimento, e o registro dos lançamentos.

Isolada por organização (`orgs_do_usuario`); objeto de outra organização é 404. Nada aqui chama
LLM: os lançamentos já vieram da estruturação, e somar e categorizar é determinístico.
"""

from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import EstruturacaoLLM, ExtracaoPagina

from . import lancamentos
from .models import CategoriaGasto, Estabelecimento, Lancamento


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


def _mes(texto: str, fim: bool = False) -> date | None:
    """"2026-09" → 1º (ou último) dia do mês; inválido → None."""
    try:
        ano, mes = (int(p) for p in (texto or "").split("-"))
        inicio = date(ano, mes, 1)
    except ValueError:
        return None
    if not fim:
        return inicio
    proximo = date(ano + (mes == 12), mes % 12 + 1, 1)
    return date.fromordinal(proximo.toordinal() - 1)


class GastosView(View):
    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        lancamentos.garantir_categorias(org)
        de, ate = request.GET.get("de", ""), request.GET.get("ate", "")

        gastos = Lancamento.objects.filter(tenant=org, tipo__in=Lancamento.TIPOS_GASTO, valor__isnull=False)
        if inicio := _mes(de):
            gastos = gastos.filter(data__gte=inicio)
        if fim := _mes(ate, fim=True):
            gastos = gastos.filter(data__lte=fim)

        por_categoria = list(
            gastos.values("estabelecimento__categoria_id", "estabelecimento__categoria__nome")
            .annotate(total=Sum("valor"), n=Count("id")).order_by("-total"))
        por_estabelecimento = list(
            gastos.values("estabelecimento_id", "estabelecimento__nome", "estabelecimento__categoria_id")
            .annotate(total=Sum("valor"), n=Count("id")).order_by("-total"))

        filtro_est, filtro_cat = request.GET.get("estabelecimento", ""), request.GET.get("categoria", "")
        lista, titulo_lista, est = None, "", None
        if filtro_est:
            est = get_object_or_404(Estabelecimento, pk=filtro_est, tenant=org)
            lista, titulo_lista = gastos.filter(estabelecimento=est), est.nome
        elif filtro_cat == "sem":
            lista, titulo_lista = gastos.filter(estabelecimento__categoria__isnull=True), "Sem categoria"
        elif filtro_cat:
            cat = get_object_or_404(CategoriaGasto, pk=filtro_cat, tenant=org)
            lista, titulo_lista = gastos.filter(estabelecimento__categoria=cat), cat.nome
        if lista is not None:
            lista = lista.select_related("estabelecimento", "artefato__extracted_text").order_by("data", "indice")

        return render(request, "financeiro/gastos.html", {
            "org": org,
            "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "de": de, "ate": ate,
            "total": gastos.aggregate(t=Sum("valor"))["t"] or 0,
            "por_categoria": por_categoria,
            "por_estabelecimento": por_estabelecimento,
            "categorias": CategoriaGasto.objects.filter(tenant=org),
            "estabelecimentos": Estabelecimento.objects.filter(tenant=org),
            "lista": lista, "titulo_lista": titulo_lista, "est_filtrado": est,
            "pagamentos": Lancamento.objects.filter(tenant=org, tipo=Lancamento.Tipo.PAGAMENTO).count(),
            "filtros": urlencode({k: v for k, v in (("organizacao", org.pk), ("de", de), ("ate", ate)) if v}),
        })

    def post(self, request):
        org = _org(request, request.POST.get("organizacao"))
        acao = request.POST.get("acao")
        if acao == "categoria":
            est = get_object_or_404(Estabelecimento, pk=request.POST.get("estabelecimento"), tenant=org)
            cat_id = request.POST.get("categoria")
            est.categoria = get_object_or_404(CategoriaGasto, pk=cat_id, tenant=org) if cat_id else None
            est.save(update_fields=["categoria"])
        elif acao == "renomear":
            est = get_object_or_404(Estabelecimento, pk=request.POST.get("estabelecimento"), tenant=org)
            if nome := request.POST.get("nome", "").strip():
                est.nome = nome[:200]
                est.save(update_fields=["nome"])
        elif acao == "fundir":
            destino = get_object_or_404(Estabelecimento, pk=request.POST.get("destino"), tenant=org)
            origem = get_object_or_404(Estabelecimento, pk=request.POST.get("origem"), tenant=org)
            lancamentos.fundir(destino, origem)
        elif acao == "nova_categoria":
            if nome := request.POST.get("nome", "").strip():
                CategoriaGasto.objects.get_or_create(tenant=org, nome=nome[:80])
        volta = request.POST.get("volta", "")
        return redirect(f"{request.path}?{volta}" if volta else request.path)


class RegistrarLancamentosView(View):
    """POST: a estruturação escolhida vira alegações e lançamentos (JSON para o visualizador)."""

    def post(self, request, estruturacao_id):
        execucao = get_object_or_404(
            EstruturacaoLLM.objects.select_related("document_text__document"),
            pk=estruturacao_id, tenant__in=orgs_do_usuario(request.user),
        )
        try:
            resultado = lancamentos.registrar(execucao, user_id=request.user.id)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if not resultado.lancamentos:
            return JsonResponse({"error": "esta estruturação não tem lista de lançamentos (data e valor)"},
                                status=400)
        return JsonResponse({
            "lancamentos": resultado.lancamentos,
            "alegacoes_criadas": resultado.alegacoes_criadas,
            "substituiu": resultado.substituiu,
        })


class RegistrarExtracaoView(View):
    """POST: a extração válida do parser de página vira alegações e lançamentos, sem LLM."""

    def post(self, request, extracao_id):
        extracao = get_object_or_404(
            ExtracaoPagina.objects.select_related("document_text__document", "schema"),
            pk=extracao_id, tenant__in=orgs_do_usuario(request.user),
        )
        try:
            resultado = lancamentos.registrar_extracao(extracao, user_id=request.user.id)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if not resultado.lancamentos:
            return JsonResponse({"error": "esta extração não tem lista de lançamentos (data e valor)"}, status=400)
        return JsonResponse({
            "lancamentos": resultado.lancamentos,
            "alegacoes_criadas": resultado.alegacoes_criadas,
            "substituiu": resultado.substituiu,
        })

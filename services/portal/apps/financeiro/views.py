"""Tela `/gastos/`: quanto se gastou por categoria, por estabelecimento e por conta, e o registro
dos movimentos.

Gasto é a saída de consumo (`Movimento.CONSUMO`), mostrada positiva; o mês é a **competência** —
o da fatura que cobrou —, então a soma de um mês bate com o total das faturas daquele mês.
Isolada por organização (`orgs_do_usuario`); objeto de outra organização é 404. Nada aqui chama
LLM: os movimentos já vieram das leituras, e somar e categorizar é determinístico.
"""

from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, F, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import EstruturacaoLLM, ExtracaoPagina

from . import lancamentos
from .models import CategoriaGasto, Conta, Estabelecimento, Movimento

#: Gasto mostrado positivo: a soma das saídas de consumo com o sinal invertido.
GASTO = Sum(-F("valor"))


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
        contas = Conta.objects.filter(tenant=org)
        filtro_conta = request.GET.get("conta", "")
        conta = get_object_or_404(Conta, pk=filtro_conta, tenant=org) if filtro_conta else None

        gastos = Movimento.objects.filter(tenant=org, natureza__in=Movimento.CONSUMO, valor__isnull=False)
        if conta:
            gastos = gastos.filter(conta=conta)
        if inicio := _mes(de):
            gastos = gastos.filter(competencia__gte=inicio)
        if fim := _mes(ate, fim=True):
            gastos = gastos.filter(competencia__lte=fim)

        por_categoria = list(
            gastos.values("estabelecimento__categoria_id", "estabelecimento__categoria__nome")
            .annotate(total=GASTO, n=Count("id")).order_by("-total"))
        por_estabelecimento = list(
            gastos.values("estabelecimento_id", "estabelecimento__nome", "estabelecimento__categoria_id")
            .annotate(total=GASTO, n=Count("id")).order_by("-total"))
        por_conta = {c["conta_id"]: c for c in gastos.values("conta_id").annotate(total=GASTO, n=Count("id"))}

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
            lista = (lista.select_related("estabelecimento", "conta", "artefato__extracted_text")
                     .order_by("competencia", "data", "indice"))

        return render(request, "financeiro/gastos.html", {
            "org": org,
            "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "de": de, "ate": ate,
            "contas": [{"conta": c, "total": por_conta.get(c.pk, {}).get("total") or 0,
                        "n": por_conta.get(c.pk, {}).get("n") or 0} for c in contas],
            "conta_filtrada": conta,
            "total": gastos.aggregate(t=GASTO)["t"] or 0,
            "por_categoria": por_categoria,
            "por_estabelecimento": por_estabelecimento,
            "categorias": CategoriaGasto.objects.filter(tenant=org),
            "estabelecimentos": Estabelecimento.objects.filter(tenant=org),
            "lista": lista, "titulo_lista": titulo_lista, "est_filtrado": est,
            "pagamentos": Movimento.objects.filter(tenant=org, natureza=Movimento.Natureza.PAGAMENTO_FATURA).count(),
            "filtros": urlencode({k: v for k, v in (("organizacao", org.pk), ("de", de), ("ate", ate),
                                                    ("conta", filtro_conta)) if v}),
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
        elif acao == "apelido_conta":
            alvo = get_object_or_404(Conta, pk=request.POST.get("conta"), tenant=org)
            alvo.apelido = request.POST.get("apelido", "").strip()[:120]
            alvo.save(update_fields=["apelido"])
        elif acao == "juntar_contas":
            destino = get_object_or_404(Conta, pk=request.POST.get("destino"), tenant=org)
            origem = get_object_or_404(Conta, pk=request.POST.get("origem"), tenant=org)
            lancamentos.juntar_contas(destino, origem)
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

"""Tela `/gastos/`: quanto se gastou por categoria, por estabelecimento e por conta, e o registro
dos movimentos.

Gasto é a saída de consumo (`Movimento.CONSUMO`), mostrada positiva. Na visão padrão o mês é a
**competência** — o da fatura que cobrou —, então a soma de um mês bate com o total das faturas
daquele mês; na visão por compra, a compra parcelada entra inteira na data da compra.
Isolada por organização (`orgs_do_usuario`); objeto de outra organização é 404. Nada aqui chama
LLM: os movimentos já vieram das leituras, e somar e categorizar é determinístico.
"""

import json
from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, F, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.permissoes import eh_admin, exige_admin
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import EstruturacaoLLM, ExtracaoPagina

from . import categorias, estado, lancamentos
from .models import CategoriaGasto, CompraParcelada, Conta, DescritorEstabelecimento, Estabelecimento, Movimento

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


def _juntar(*listas, chave):
    """Soma linhas de agregação vindas de consultas diferentes (movimentos avulsos e compras)."""
    juntas: dict = {}
    for linha in (l for lista in listas for l in lista):
        atual = juntas.setdefault(linha[chave], {**linha, "total": 0, "n": 0})
        atual["total"] += linha["total"] or 0
        atual["n"] += linha["n"]
    return sorted(juntas.values(), key=lambda l: -l["total"])


class GastosView(View):
    """Duas visões do mesmo dinheiro (ADR 021):

    - **competência** (padrão): cada parcela no mês da fatura que a cobrou — bate com as faturas;
    - **compra**: a compra parcelada inteira, uma vez, na data da compra; o resto, na data.
    """

    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        lancamentos.garantir_categorias(org)
        de, ate = request.GET.get("de", ""), request.GET.get("ate", "")
        visao = "compra" if request.GET.get("visao") == "compra" else "competencia"
        contas = Conta.objects.filter(tenant=org)
        filtro_conta = request.GET.get("conta", "")
        conta = get_object_or_404(Conta, pk=filtro_conta, tenant=org) if filtro_conta else None

        gastos = Movimento.objects.filter(tenant=org, natureza__in=Movimento.CONSUMO, valor__isnull=False)
        compras = CompraParcelada.objects.filter(tenant=org)
        if conta:
            gastos, compras = gastos.filter(conta=conta), compras.filter(conta=conta)
        campo_data = "data" if visao == "compra" else "competencia"
        if visao == "compra":
            gastos = gastos.filter(compra__isnull=True)     # a parcelada entra pela compra, não pelas parcelas
        if inicio := _mes(de):
            gastos = gastos.filter(**{f"{campo_data}__gte": inicio})
            compras = compras.filter(data__gte=inicio) if visao == "compra" else compras
        if fim := _mes(ate, fim=True):
            gastos = gastos.filter(**{f"{campo_data}__lte": fim})
            compras = compras.filter(data__lte=fim) if visao == "compra" else compras
        somadas = compras if visao == "compra" else CompraParcelada.objects.none()

        def agrupar(campos, chave):
            return _juntar(
                gastos.values(*campos).annotate(total=GASTO, n=Count("id")),
                somadas.values(*campos).annotate(total=Sum("valor"), n=Count("id")),
                chave=chave)

        por_categoria = agrupar(("estabelecimento__categoria_id", "estabelecimento__categoria__nome"),
                                "estabelecimento__categoria_id")
        por_estabelecimento = agrupar(("estabelecimento_id", "estabelecimento__nome", "estabelecimento__categoria_id"),
                                      "estabelecimento_id")
        por_conta = {c["conta_id"]: c for c in agrupar(("conta_id",), "conta_id")}
        total = (gastos.aggregate(t=GASTO)["t"] or 0) + (somadas.aggregate(t=Sum("valor"))["t"] or 0)

        filtro_est, filtro_cat = request.GET.get("estabelecimento", ""), request.GET.get("categoria", "")
        lista, titulo_lista, est = None, "", None
        if filtro_est:
            est = get_object_or_404(Estabelecimento, pk=filtro_est, tenant=org)
            lista, compras, titulo_lista = gastos.filter(estabelecimento=est), compras.filter(estabelecimento=est), est.nome
        elif filtro_cat == "sem":
            lista, compras = gastos.filter(estabelecimento__categoria__isnull=True), compras.filter(
                estabelecimento__categoria__isnull=True)
            titulo_lista = "Sem categoria"
        elif filtro_cat:
            cat = get_object_or_404(CategoriaGasto, pk=filtro_cat, tenant=org)
            lista, compras, titulo_lista = (gastos.filter(estabelecimento__categoria=cat),
                                            compras.filter(estabelecimento__categoria=cat), cat.nome)
        if lista is not None:
            lista = (lista.select_related("estabelecimento", "conta", "artefato__extracted_text")
                     .order_by(campo_data, "data", "indice"))

        return render(request, "financeiro/gastos.html", {
            "org": org,
            "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "de": de, "ate": ate, "visao": visao,
            "contas": [{"conta": c, "total": por_conta.get(c.pk, {}).get("total") or 0,
                        "n": por_conta.get(c.pk, {}).get("n") or 0} for c in contas],
            "conta_filtrada": conta,
            "total": total,
            "por_categoria": por_categoria,
            "por_estabelecimento": por_estabelecimento,
            "categorias": CategoriaGasto.objects.filter(tenant=org),
            "estabelecimentos": Estabelecimento.objects.filter(tenant=org),
            "lista": lista, "titulo_lista": titulo_lista, "est_filtrado": est,
            "compras": compras.select_related("estabelecimento", "conta").order_by("-data")[:60],
            "compras_total": compras.count(),
            "compromisso": lancamentos.compromisso_futuro(org.id, conta.id if conta else None),
            "pagamentos": Movimento.objects.filter(tenant=org, natureza=Movimento.Natureza.PAGAMENTO_FATURA).count(),
            "filtros": urlencode({k: v for k, v in (("organizacao", org.pk), ("de", de), ("ate", ate),
                                                    ("conta", filtro_conta),
                                                    ("visao", visao if visao == "compra" else "")) if v}),
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


def _resposta_registro(resultado) -> dict:
    """O JSON da ação de leitura (ADR 022): os números e a `mensagem`/`link` que o visualizador mostra."""
    return {
        "lancamentos": resultado.lancamentos,
        "alegacoes_criadas": resultado.alegacoes_criadas,
        "substituiu": resultado.substituiu,
        "mensagem": f"{resultado.lancamentos} lançamentos registrados"
                    + (" (a leitura anterior deixou de alimentar os Gastos)" if resultado.substituiu else ""),
        "link": {"rotulo": "ver gastos", "url": "/gastos/"},
    }


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
        return JsonResponse(_resposta_registro(resultado))


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
        return JsonResponse(_resposta_registro(resultado))


class CategorizarView(View):
    """Os estabelecimentos sem categoria, do maior gasto para o menor, cada um com a categoria
    sugerida (`categorias.Sugeridor`): a pessoa confirma uma a uma, ou aplica de uma vez as de
    alta confiança."""

    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        lancamentos.garantir_categorias(org)
        linhas = categorias.pendentes(org)
        return render(request, "financeiro/categorizar.html", {
            "org": org,
            "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "linhas": linhas,
            "categorias": CategoriaGasto.objects.filter(tenant=org),
            "n_alta": sum(1 for l in linhas if l["sugestao"] and l["sugestao"].confianca == categorias.ALTA),
            "total_pendente": sum(l["total"] for l in linhas),
            "categorizados": Estabelecimento.objects.filter(tenant=org, categoria__isnull=False).count(),
            "aplicadas": request.GET.get("aplicadas"),
        })

    def post(self, request):
        org = _org(request, request.POST.get("organizacao"))
        destino = f"{request.path}?{urlencode({'organizacao': org.pk})}"
        if request.POST.get("acao") == "aplicar_alta":
            destino += f"&aplicadas={categorias.aplicar_alta(org)}"
        elif request.POST.get("acao") == "categoria":
            est = get_object_or_404(Estabelecimento, pk=request.POST.get("estabelecimento"), tenant=org)
            if cat_id := request.POST.get("categoria"):
                est.categoria = get_object_or_404(CategoriaGasto, pk=cat_id, tenant=org)
                est.save(update_fields=["categoria"])
        return redirect(destino)


class EstadoView(View):
    """Exportar (qualquer membro) e importar (dono ou administrador) categorias, estabelecimentos
    e contas num JSON, para voltar ao mesmo ponto depois de zerar o sistema (`estado.py`)."""

    TAMANHO_MAXIMO = 10 * 1024 * 1024

    def _contexto(self, request, org, **extra):
        return {
            "org": org,
            "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "n_categorias": CategoriaGasto.objects.filter(tenant=org).count(),
            "n_estabelecimentos": Estabelecimento.objects.filter(tenant=org).count(),
            "n_categorizados": Estabelecimento.objects.filter(tenant=org, categoria__isnull=False).count(),
            "n_descritores": DescritorEstabelecimento.objects.filter(tenant=org).count(),
            "n_contas": Conta.objects.filter(tenant=org).count(),
            "pode_importar": eh_admin(request.user, org),
            **extra,
        }

    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        if request.GET.get("baixar"):
            corpo = json.dumps(estado.exportar(org), ensure_ascii=False, indent=2)
            resp = HttpResponse(corpo, content_type="application/json; charset=utf-8")
            nome = f"gastos-estado-{org.slug or org.pk}-{date.today().isoformat()}.json"
            resp["Content-Disposition"] = f'attachment; filename="{nome}"'
            return resp
        return render(request, "financeiro/estado.html", self._contexto(request, org))

    def post(self, request):
        org = _org(request, request.POST.get("organizacao"))
        exige_admin(request.user, org)
        arquivo = request.FILES.get("arquivo")
        erro, resumo = "", None
        if arquivo is None:
            erro = "Escolha o arquivo exportado."
        elif arquivo.size > self.TAMANHO_MAXIMO:
            erro = "Arquivo grande demais para um estado dos Gastos (máximo 10 MB)."
        else:
            try:
                resumo = estado.importar(org, json.loads(arquivo.read().decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                erro = "O arquivo não é um JSON válido."
            except ValueError as exc:
                erro = f"Arquivo recusado: {exc}."
        return render(request, "financeiro/estado.html", self._contexto(request, org, erro=erro, resumo=resumo),
                      status=400 if erro else 200)

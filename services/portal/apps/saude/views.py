"""Tela `/saude/`: os resultados de exames por analito, com o histórico entre laudos.

Tudo aqui é `confidencial` (piso de `saude:Exame`), e confidencial exige auditoria
(`docs/seguranca/classificacao.md`): cada abertura da tela grava um `AuditLog`. Isolada por
organização; objeto de outra organização é 404. Nada aqui chama LLM.
"""

from collections import defaultdict

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.accounts.models import Organization
from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import AuditLog, EstruturacaoLLM, ExtracaoPagina

from . import exames
from .models import ResultadoExame


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


class ExamesView(View):
    def get(self, request):
        org = _org(request, request.GET.get("organizacao"))
        resultados = list(ResultadoExame.objects.filter(tenant=org).select_related("artefato"))
        por_analito = defaultdict(list)
        for r in resultados:
            por_analito[r.analito_chave].append(r)
        analitos = [{"nome": rs[-1].analito, "ultimo": rs[-1], "historico": rs}
                    for _, rs in sorted(por_analito.items())]
        AuditLog.objects.create(
            user=request.user, organization=org, operation="saude.ver_resultados", outcome="permitido",
            reason="tela de resultados de exames (confidencial)",
            metadata={"resultados": len(resultados), "laudos": len({r.artefato_id for r in resultados})},
        )
        return render(request, "saude/exames.html", {
            "org": org, "organizacoes": list(orgs_do_usuario(request.user).order_by("name")),
            "analitos": analitos, "n_laudos": len({r.artefato_id for r in resultados}),
        })


def _resposta(registro) -> JsonResponse:
    if not registro.resultados:
        return JsonResponse({"error": "esta leitura não tem lista de resultados de exame"}, status=400)
    return JsonResponse({
        "resultados": registro.resultados, "alegacoes_criadas": registro.alegacoes_criadas,
        "mensagem": f"{registro.resultados} resultados de exame registrados (o laudo agora é confidencial)",
        "link": {"rotulo": "ver na Saúde", "url": "/saude/"},
    })


class RegistrarEstruturacaoView(View):
    def post(self, request, estruturacao_id):
        execucao = get_object_or_404(EstruturacaoLLM.objects.select_related("document_text__document"),
                                     pk=estruturacao_id, tenant__in=orgs_do_usuario(request.user))
        try:
            return _resposta(exames.registrar(execucao, user_id=request.user.id))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class RegistrarExtracaoView(View):
    def post(self, request, extracao_id):
        extracao = get_object_or_404(ExtracaoPagina.objects.select_related("document_text__document", "schema"),
                                     pk=extracao_id, tenant__in=orgs_do_usuario(request.user))
        try:
            return _resposta(exames.registrar_extracao(extracao, user_id=request.user.id))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

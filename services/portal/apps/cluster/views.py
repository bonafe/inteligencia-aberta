
from django.shortcuts import render
from django.views import View

from apps.accounts.views import orgs_do_usuario

from .models import Maquina


class PainelClusterView(View):
    """Tela `/cluster/` — lista as máquinas do cluster (das organizações do
    usuário logado), status de recursos e capacidade de LLM de cada uma.
    Autenticação de sessão de sempre (não está em EXEMPT_PREFIXES)."""

    #: Janela do resumo de chamadas por máquina — só uma leitura recente,
    #: não um relatório de custo completo (isso pede uma tela própria).
    JANELA_RESUMO_DIAS = 7

    def get(self, request):
        maquinas = (
            Maquina.objects.filter(organizacao__in=orgs_do_usuario(request.user))
            .select_related("status")
            .prefetch_related("modelos_ollama")
            .order_by("apelido")
        )

        from django.db.models import Count, IntegerField, Q, Sum
        from django.db.models.functions import Coalesce
        from django.utils import timezone

        from apps.events.models import ChamadaLLM

        maquinas = list(maquinas)

        desde = timezone.now() - timezone.timedelta(days=self.JANELA_RESUMO_DIAS)
        resumo_por_maquina = {
            linha["maquina"]: linha
            for linha in ChamadaLLM.objects.filter(
                maquina__in=[m.id for m in maquinas], ocorreu_em__gte=desde,
            )
            .values("maquina")
            .annotate(
                chamadas=Count("id"),
                tokens=Coalesce(Sum("tokens_entrada"), 0, output_field=IntegerField())
                + Coalesce(Sum("tokens_saida"), 0, output_field=IntegerField()),
                falhas=Count("id", filter=Q(sucesso=False)),
            )
        }
        # Anexado direto no objeto — o template não tem um filtro de lookup em
        # dict por chave dinâmica, e criar um só para isto seria mais código
        # do que o problema pede.
        for maquina in maquinas:
            maquina.resumo_chamadas_llm = resumo_por_maquina.get(maquina.id)

        return render(request, "cluster/painel.html", {
            "maquinas": maquinas,
            "janela_resumo_dias": self.JANELA_RESUMO_DIAS,
        })

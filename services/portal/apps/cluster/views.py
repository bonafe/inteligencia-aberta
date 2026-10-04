import hashlib
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.views import orgs_do_usuario

from .models import Maquina
from .replicacao import eventos_para_peer

logger = logging.getLogger(__name__)


def _maquina_do_token(token: str) -> Maquina | None:
    """Resolve o token em claro do header pra uma `Maquina` ativa.

    Não há como indexar por token em claro (só guardamos o hash), então isto
    varre as máquinas ativas comparando hash a hash — aceitável no volume
    esperado de máquinas de um cluster pessoal (dezenas, não milhares).
    `constant_time_compare` evita vazar por timing qual prefixo do hash bateu.
    """
    if not token:
        return None
    hash_recebido = hashlib.sha256(token.encode()).hexdigest()
    for maquina in Maquina.objects.filter(ativa=True):
        if constant_time_compare(maquina.token_hash, hash_recebido):
            return maquina
    return None


@method_decorator(csrf_exempt, name="dispatch")
class ReplicacaoEventosAPIView(View):
    """Canal máquina-a-máquina: um peer puxa eventos de replicação novos.

    Autenticação por `X-Machine-Token`, um segredo por máquina (não um único
    segredo compartilhado como `X-Internal-Token`) — necessário porque aqui
    precisamos saber QUAL peer está pedindo, não só que é um chamador
    confiável (ver `eventos_para_peer`, o ponto onde isso importa a partir da
    fase 2).
    """

    def get(self, request):
        token = request.headers.get("X-Machine-Token", "")
        peer = _maquina_do_token(token)
        if peer is None:
            logger.warning("replicação recusada — X-Machine-Token ausente ou inválido")
            return JsonResponse({"error": "Não autorizado"}, status=401)

        try:
            desde = int(request.GET.get("desde", "0"))
        except ValueError:
            return JsonResponse({"error": "'desde' deve ser um inteiro"}, status=400)

        eventos = eventos_para_peer(peer, desde)
        return JsonResponse({
            "eventos": [
                {
                    "id": str(e.id),
                    "sequence": e.sequence,
                    "tipo": e.tipo,
                    "organizacao_id": str(e.organizacao_id),
                    "objeto_id": str(e.objeto_id),
                    "payload": e.payload,
                    "ocorrido_em": e.ocorrido_em.isoformat(),
                }
                for e in eventos
            ],
        })


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

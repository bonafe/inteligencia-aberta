import hashlib
import json
import logging
import socket

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.views import orgs_do_usuario

from .models import Maquina
from .provisionamento import ProvisionamentoError, criar_maquina
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


class StatusPublicoView(View):
    """`GET /cluster/api/v1/status/` — sem autenticação: só diz se este
    processo hospeda a infraestrutura compartilhada, pra
    `scripts/entrar_no_cluster.py` achar o nó certo perguntando a cada peer
    do tailnet (a maioria recusa a conexão ou não roda este projeto — isso é
    esperado, não erro). Informação de baixo risco (não expõe dado nenhum) e
    só alcançável por quem já está na VPN do usuário."""

    def get(self, request):
        hospeda = getattr(settings, "CLUSTER_HOSPEDA_INFRA", True)
        return JsonResponse({"hospeda_infra_compartilhada": hospeda, "apelido": socket.gethostname()})


@method_decorator(csrf_exempt, name="dispatch")
class JoinAPIView(View):
    """`POST /cluster/api/v1/join/` — autorregistro de máquina nova, usado
    por `scripts/entrar_no_cluster.py` depois de descobrir o nó de infra via
    `StatusPublicoView`. Autenticado por um segredo único do cluster
    (`X-Cluster-Join-Secret`), não por máquina — a máquina ainda não tem
    identidade própria neste ponto, é isto que a está criando.
    """

    def post(self, request):
        esperado = getattr(settings, "CLUSTER_JOIN_SECRET", "")
        if not esperado:
            return JsonResponse({"error": "join desabilitado"}, status=404)

        recebido = request.headers.get("X-Cluster-Join-Secret", "")
        if not constant_time_compare(recebido, esperado):
            logger.warning("join de máquina recusado — X-Cluster-Join-Secret ausente ou inválido")
            return JsonResponse({"error": "Não autorizado"}, status=401)

        try:
            corpo = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        apelido = corpo.get("apelido")
        organizacao_slug = corpo.get("organizacao")
        modo = corpo.get("modo")
        if not apelido or not organizacao_slug or modo not in Maquina.Modo.values:
            return JsonResponse(
                {"error": "'apelido', 'organizacao' e 'modo' (compute|replica) são obrigatórios"}, status=400,
            )

        try:
            maquina, token = criar_maquina(
                apelido=apelido, organizacao_slug=organizacao_slug, modo=modo,
                hostname=corpo.get("hostname", ""), ollama_endpoint=corpo.get("ollama_endpoint", ""),
            )
        except ProvisionamentoError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"machine_id": str(maquina.id), "machine_token": token}, status=201)


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
            .order_by("-hospeda_infra_compartilhada", "apelido")
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

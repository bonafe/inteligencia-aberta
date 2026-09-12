"""Painel de eventos e endpoint de ingestão entre serviços.

Views no mesmo estilo do app `artifacts` (`django.views.View` puras, não DRF),
todas filtrando por `orgs_do_usuario` — o isolamento de tenant é feito na
queryset, não na apresentação.
"""

import json
import logging
import uuid as uuid_lib

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import Organization, User
from apps.accounts.views import orgs_do_usuario

from .agregados import painel as agregados_painel
from .context import set_correlation_id, set_tenant_id
from .emit import emit
from .models import PipelineEvent, PipelineRun
from .projecao import TRILHA

logger = logging.getLogger(__name__)

PAGINA_EVENTOS = 200
#: Eventos já carregados no primeiro paint do fluxo.
PAGINA_EVENTOS_INICIAIS = 60
PAGINA_EXECUCOES = 50
#: Teto de eventos por requisição do endpoint de ingestão.
LOTE_MAX = 200


class PainelEventosView(View):
    """A tela principal: o que está acontecendo agora e as execuções recentes."""

    def get(self, request):
        orgs = orgs_do_usuario(request.user)
        execucoes = (
            PipelineRun.objects.filter(tenant__in=orgs)
            .select_related("artifact")[:PAGINA_EXECUCOES]
        )
        # Tudo que o painel precisa para o primeiro paint vai em um único bloco
        # JSON (`json_script` no template): evita o piscar de trilhas vazias
        # enquanto o primeiro fetch não volta, e não injeta repr de dict do
        # Python dentro de <script>.
        return render(request, "events/painel.html", {
            "dados_iniciais": {
                "trilha": [{"stage": stage, "rotulo": rotulo} for stage, rotulo in TRILHA],
                "agregados": agregados_painel(orgs),
                "execucoes": [_run_json(r) for r in execucoes],
                # Semear o fluxo com os últimos eventos: abrir o painel e ver
                # "aguardando eventos" não ajuda ninguém a entender o que o
                # sistema andou fazendo.
                "eventos": [
                    e.para_websocket()
                    for e in PipelineEvent.objects.filter(tenant__in=orgs)
                    .order_by("-sequence")[:PAGINA_EVENTOS_INICIAIS]
                ],
                "ultima_sequence": _ultima_sequence(orgs),
            },
        })


class ExecucaoDetailView(View):
    """Timeline completa de uma captura — todo o histórico, do clique ao Qdrant."""

    def get(self, request, correlation_id):
        orgs = orgs_do_usuario(request.user)
        run = get_object_or_404(
            PipelineRun, correlation_id=correlation_id, tenant__in=orgs
        )
        eventos = (
            PipelineEvent.objects.filter(correlation_id=correlation_id)
            .order_by("sequence")
        )
        return render(request, "events/execucao.html", {
            "run": run,
            "eventos": eventos,
            "trilha": [{"stage": s, "rotulo": r} for s, r in TRILHA],
        })


class EventosAPIView(View):
    """Backfill e fallback quando o WebSocket não conecta.

    `?desde=<sequence>` é o cursor; os demais parâmetros filtram.
    """

    def get(self, request):
        orgs = orgs_do_usuario(request.user)
        eventos = PipelineEvent.objects.filter(tenant__in=orgs)

        desde = request.GET.get("desde")
        if desde:
            try:
                eventos = eventos.filter(sequence__gt=int(desde))
            except ValueError:
                return JsonResponse({"error": "desde inválido"}, status=400)

        for campo in ("stage", "status", "source", "correlation_id"):
            valor = request.GET.get(campo)
            if valor:
                eventos = eventos.filter(**{campo: valor})

        eventos = eventos.order_by("sequence")[:PAGINA_EVENTOS]
        dados = [e.para_websocket() for e in eventos]
        return JsonResponse({
            "eventos": dados,
            "ultima_sequence": dados[-1]["sequence"] if dados else desde,
        })


class AgregadosAPIView(View):
    """Os números da faixa 'Agora'. Consultado sob demanda, não por evento."""

    def get(self, request):
        return JsonResponse(agregados_painel(orgs_do_usuario(request.user)))


class ExecucoesAPIView(View):
    """Lista de execuções em JSON, para o painel atualizar sem recarregar."""

    def get(self, request):
        orgs = orgs_do_usuario(request.user)
        runs = PipelineRun.objects.filter(tenant__in=orgs)[:PAGINA_EXECUCOES]
        return JsonResponse({"execucoes": [_run_json(r) for r in runs]})


class ReprocessarView(View):
    """Reexecuta a extração de uma captura, emitindo eventos.

    É a resposta operacional para "por que este artefato não gerou X?": força o
    reprocessamento e a timeline passa a dizer, etapa por etapa, o que
    aconteceu.
    """

    def post(self, request, correlation_id):
        from apps.artifacts.models import Artifact
        from apps.artifacts.tasks import extract_text_from_mhtml

        orgs = orgs_do_usuario(request.user)
        run = get_object_or_404(
            PipelineRun, correlation_id=correlation_id, tenant__in=orgs
        )
        if not run.artifact_id:
            return JsonResponse(
                {"error": "Esta execução não tem artefato associado."}, status=400
            )
        # Refaz a checagem de tenant no artefato: a projeção é derivada e não
        # pode ser a única guardiã do acesso.
        artefato = get_object_or_404(Artifact, id=run.artifact_id, tenant__in=orgs)

        emit(
            "reprocessamento.solicitado",
            "ok",
            correlation_id=run.correlation_id,
            subject_type="artifact",
            subject_id=artefato.id,
            tenant_id=artefato.tenant_id,
            user_id=request.user.id,
            source="portal",
            message=f"reprocessamento pedido por {request.user.username}",
        )
        # O contextvar precisa estar definido ANTES do .delay(): é dele que o
        # signal `before_task_publish` tira a correlação para o header da task.
        set_correlation_id(run.correlation_id)
        set_tenant_id(artefato.tenant_id)
        extract_text_from_mhtml.delay(str(artefato.id), forcar=True)
        return JsonResponse({"status": "enfileirado", "artifact_id": str(artefato.id)})


@method_decorator(csrf_exempt, name="dispatch")
class EventoIngestAPIView(View):
    """Recebe eventos do orchestrator e do MCP.

    Mesmo padrão de autenticação de `ArtefatoCreateAPIView`: canal
    serviço-a-serviço protegido por `X-Internal-Token`, com
    `constant_time_compare` contra timing attack. Aceita lote para que um
    serviço possa despachar vários eventos em uma requisição.
    """

    def post(self, request):
        expected = getattr(settings, "INTERNAL_API_TOKEN", "")
        provided = request.headers.get("X-Internal-Token", "")
        if not expected or not constant_time_compare(provided, expected):
            logger.warning("evento recusado — X-Internal-Token ausente ou inválido")
            return JsonResponse({"error": "Não autorizado"}, status=403)

        try:
            corpo = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "JSON inválido"}, status=400)

        lote = corpo.get("eventos") or [corpo]
        if len(lote) > LOTE_MAX:
            return JsonResponse(
                {"error": f"lote acima do limite de {LOTE_MAX} eventos"}, status=400
            )

        aceitos = 0
        for bruto in lote:
            if self._gravar(bruto):
                aceitos += 1
        return JsonResponse({"aceitos": aceitos, "recebidos": len(lote)}, status=201)

    def _gravar(self, bruto: dict) -> bool:
        stage = bruto.get("stage")
        status = bruto.get("status")
        if not stage or not status:
            logger.warning("evento ingerido sem stage/status — descartado")
            return False

        ocorrido = bruto.get("occurred_at")
        evento = emit(
            stage,
            status,
            correlation_id=bruto.get("correlation_id"),
            source=bruto.get("source") or "orchestrator",
            subject_type=bruto.get("subject_type", ""),
            subject_id=_uuid(bruto.get("subject_id")),
            message=bruto.get("message", ""),
            payload=bruto.get("payload"),
            error=bruto.get("error", ""),
            duration_ms=bruto.get("duration_ms"),
            tenant_id=_existe(Organization, bruto.get("tenant_id")),
            user_id=_existe(User, bruto.get("user_id")),
            causation_id=_uuid(bruto.get("causation_id")),
            occurred_at=parse_datetime(ocorrido) if ocorrido else None,
        )
        return evento is not None


# ── auxiliares ───────────────────────────────────────────────────────────────

def _uuid(valor):
    if not valor:
        return None
    try:
        return uuid_lib.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        return None


def _existe(modelo, valor):
    """Só devolve o id se a FK existir — evita derrubar o insert por id inválido."""
    identificador = _uuid(valor)
    if identificador is None:
        return None
    return identificador if modelo.objects.filter(id=identificador).exists() else None


def _ultima_sequence(orgs) -> int:
    ultimo = (
        PipelineEvent.objects.filter(tenant__in=orgs)
        .order_by("-sequence").values_list("sequence", flat=True).first()
    )
    return ultimo or 0


def _run_json(run) -> dict:
    return {
        "correlation_id": str(run.correlation_id),
        "artifact_id": str(run.artifact_id) if run.artifact_id else None,
        "url": run.url,
        "titulo": run.titulo,
        "status": run.status,
        "status_label": run.get_status_display(),
        "etapas": run.etapas,
        "iniciado_em": run.iniciado_em.isoformat(),
        "atualizado_em": run.atualizado_em.isoformat(),
        "duracao_ms": run.duracao_ms,
        "total_eventos": run.total_eventos,
        "total_falhas": run.total_falhas,
    }

"""Emissão de eventos do pipeline.

Regra absoluta deste módulo: **nada aqui pode levantar exceção para quem chama**.
Observabilidade que derruba o pipeline é pior do que a falta dela. Todo o corpo
das funções públicas está sob `try/except`, e uma falha na gravação vira um
`logger.exception` — nunca uma task quebrada.

A publicação no WebSocket é um segundo passo, também isolado: se o Redis estiver
fora, o evento continua gravado no Postgres e o painel o recupera no próximo
backfill.
"""

import json
import logging
import traceback
import uuid as uuid_lib
from contextlib import contextmanager
from time import perf_counter

from django.db import connection, transaction
from django.utils import timezone

from .context import (
    get_correlation_id,
    get_tenant_id,
    identidade_execucao,
    source_padrao,
)

logger = logging.getLogger(__name__)

#: Teto do payload serializado. Acima disso, o conteúdo é substituído por um
#: resumo — o evento nunca deixa de ser gravado por causa do tamanho.
PAYLOAD_MAX_BYTES = 8 * 1024

#: Chaves proibidas no payload: conteúdo capturado e segredos nunca entram no
#: log de eventos. Ver a restrição correspondente no ADR 005.
CHAVES_PROIBIDAS = frozenset({
    "html", "html_content", "mhtml", "mhtml_bytes", "text", "texto",
    "dom_representation", "skeleton", "api_key", "token", "password", "senha",
    "secret", "authorization",
})


def _proximo_sequence() -> int:
    """Lê o próximo valor da sequência do Postgres.

    Feito explicitamente (e não por `db_default`) porque o emissor precisa do
    número em mãos para publicar no WebSocket junto com o evento.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('pipeline_event_seq')")
        return cursor.fetchone()[0]


def _sanitizar(payload) -> dict:
    """Remove chaves proibidas e garante que o payload cabe no teto."""
    if not payload:
        return {}
    if not isinstance(payload, dict):
        payload = {"valor": payload}

    limpo = {
        k: v for k, v in payload.items()
        if k.lower() not in CHAVES_PROIBIDAS
    }
    descartadas = len(payload) - len(limpo)
    if descartadas:
        limpo["_chaves_omitidas"] = descartadas

    try:
        bruto = json.dumps(limpo, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"_ilegivel": True}

    if len(bruto.encode("utf-8")) <= PAYLOAD_MAX_BYTES:
        # Volta pelo json para garantir que tudo é serializável pelo JSONField.
        return json.loads(bruto)

    # Acima do teto: preserva só os escalares, que são o que costuma importar
    # para diagnóstico (contagens, percentuais, nomes).
    escalares = {
        k: v for k, v in limpo.items()
        if isinstance(v, (str, int, float, bool, type(None))) and len(str(v)) < 500
    }
    escalares["_truncado"] = True
    escalares["_bytes_originais"] = len(bruto.encode("utf-8"))
    return json.loads(json.dumps(escalares, default=str, ensure_ascii=False))


def _publicar(evento) -> None:
    """Empurra o evento para o painel via channel layer. Nunca levanta."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        grupo = grupo_do_tenant(evento.tenant_id)
        async_to_sync(layer.group_send)(
            grupo, {"type": "evento.novo", "evento": evento.para_websocket()}
        )
    except Exception:
        # Redis fora, Channels não instalado, loop já rodando — nada disso pode
        # desfazer a gravação que já aconteceu.
        logger.debug("falha ao publicar evento no channel layer", exc_info=True)


def grupo_do_tenant(tenant_id) -> str:
    """Nome do grupo do channel layer. Isolamento multi-tenant do painel."""
    if not tenant_id:
        return "eventos.sistema"
    return f"eventos.tenant.{str(tenant_id).replace('-', '')}"


def emit(
    stage: str,
    status: str,
    *,
    correlation_id=None,
    source: str | None = None,
    subject_type: str = "",
    subject_id=None,
    message: str = "",
    payload: dict | None = None,
    error: str = "",
    duration_ms: int | None = None,
    tenant_id=None,
    user_id=None,
    causation_id=None,
    celery_task_id: str = "",
    celery_task_name: str = "",
    occurred_at=None,
):
    """Grava um evento e o publica. Devolve o evento, ou None se algo falhou.

    Nunca levanta.
    """
    try:
        from .models import PipelineEvent
        from .projecao import aplicar_evento

        correlation = correlation_id or get_correlation_id()
        # Eventos de infraestrutura (worker subindo, varredura do catch-up,
        # chamada de ferramenta avulsa) não pertencem a nenhuma captura. Eles
        # são gravados no log normalmente, mas com correlação própria e sem
        # gerar projeção — senão o painel encheria de "execuções" de um evento
        # só, que não são execução de coisa nenhuma.
        avulso = correlation is None
        if avulso:
            correlation = uuid_lib.uuid4()
        elif not isinstance(correlation, uuid_lib.UUID):
            correlation = uuid_lib.UUID(str(correlation))

        if tenant_id is None:
            tenant_id = get_tenant_id()

        hostname, pid = identidade_execucao()

        with transaction.atomic():
            evento = PipelineEvent(
                sequence=_proximo_sequence(),
                correlation_id=correlation,
                causation_id=causation_id,
                tenant_id=tenant_id,
                user_id=user_id,
                source=source or source_padrao(),
                stage=stage,
                status=status,
                subject_type=subject_type,
                subject_id=subject_id,
                message=message,
                payload=_sanitizar(payload),
                error=error,
                duration_ms=duration_ms,
                hostname=hostname,
                process_id=pid,
                celery_task_id=celery_task_id or "",
                celery_task_name=celery_task_name or "",
                occurred_at=occurred_at or timezone.now(),
                avulso=avulso,
            )
            evento.save(force_insert=True)
            if not evento.avulso:
                aplicar_evento(evento)

        _publicar(evento)
        return evento
    except Exception:
        logger.exception("emit falhou — stage=%s status=%s", stage, status)
        return None


class _Etapa:
    """Acumulador do resultado de uma etapa, preenchido dentro do `with`."""

    __slots__ = ("status", "message", "payload", "subject_id", "subject_type")

    def __init__(self, subject_type: str, subject_id):
        self.status = None
        self.message = ""
        self.payload = {}
        self.subject_type = subject_type
        self.subject_id = subject_id

    def ok(self, message: str = "", **payload):
        self.status = "ok"
        if message:
            self.message = message
        self.payload.update(payload)

    def vazio(self, message: str = "", **payload):
        """Rodou e não produziu resultado — diferente de ter falhado."""
        self.status = "vazio"
        if message:
            self.message = message
        self.payload.update(payload)

    def ignorado(self, message: str = "", **payload):
        self.status = "ignorado"
        if message:
            self.message = message
        self.payload.update(payload)


@contextmanager
def etapa(
    stage: str,
    *,
    subject_type: str = "",
    subject_id=None,
    correlation_id=None,
    tenant_id=None,
    message: str = "",
    **extra,
):
    """Cronometra uma etapa e emite exatamente um evento ao final.

    Uso:

        with etapa("extracao.dom2parser", subject_id=artifact_id) as e:
            ...
            e.vazio("nenhum registro atingiu precision/recall 1.0",
                    registros_no_spec=7, registros_verificados=0)

    Sucesso sem chamada explícita vira `ok`. Exceção vira `falhou` com traceback
    e **é re-levantada** — este gerenciador observa, não engole erro.
    """
    reg = _Etapa(subject_type, subject_id)
    t0 = perf_counter()
    try:
        yield reg
    except Exception as exc:
        emit(
            stage,
            "falhou",
            correlation_id=correlation_id,
            subject_type=reg.subject_type,
            subject_id=reg.subject_id,
            tenant_id=tenant_id,
            message=reg.message or f"{type(exc).__name__}: {exc}",
            payload=reg.payload,
            error=traceback.format_exc(),
            duration_ms=int((perf_counter() - t0) * 1000),
            **extra,
        )
        raise
    else:
        emit(
            stage,
            reg.status or "ok",
            correlation_id=correlation_id,
            subject_type=reg.subject_type,
            subject_id=reg.subject_id,
            tenant_id=tenant_id,
            message=reg.message or message,
            payload=reg.payload,
            duration_ms=int((perf_counter() - t0) * 1000),
            **extra,
        )

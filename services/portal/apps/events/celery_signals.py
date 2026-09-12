"""Instrumentação automática de toda task Celery.

O ganho aqui é não precisar tocar na assinatura de nenhuma task: os signals do
Celery dão fila, início, fim, falha e retry de graça, e o
`correlation_id` viaja nos **headers** da mensagem — por isso a cadeia
`extract_text_from_mhtml` → `fragment_text` → `embed_fragment` aparece na
timeline como uma execução só, sem que nenhuma delas passe o id adiante à mão.

Os eventos de `worker_ready`/`worker_shutting_down` são o inventário de nós:
com mais de um worker, é por eles que se sabe quem estava de pé quando.
"""

import logging

from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_revoked,
    worker_ready,
    worker_shutting_down,
)

from .context import (
    get_correlation_id,
    get_tenant_id,
    set_correlation_id,
    set_tenant_id,
)
from .emit import emit

logger = logging.getLogger(__name__)

#: Header onde o correlation_id viaja entre processos.
HEADER_CORRELACAO = "ia_correlation_id"
HEADER_TENANT = "ia_tenant_id"

#: Tasks cujo ciclo de vida não vale um evento por execução — o Beat roda a cada
#: 2 minutos e encheria a timeline de ruído. A varredura em si emite o seu
#: próprio evento (`catchup.varredura`) quando encontra algo.
TASKS_SILENCIOSAS = {
    "apps.artifacts.tasks.scan_unprocessed_documents",
}


def _nome_curto(nome: str | None) -> str:
    return (nome or "").rsplit(".", 1)[-1]


@before_task_publish.connect
def marcar_correlacao(sender=None, headers=None, **kwargs):
    """Injeta o correlation_id do processo que enfileira dentro da mensagem."""
    try:
        if headers is None:
            return
        correlacao = get_correlation_id()
        if correlacao is not None:
            headers[HEADER_CORRELACAO] = str(correlacao)
        tenant = get_tenant_id()
        if tenant is not None:
            headers[HEADER_TENANT] = str(tenant)

        if sender in TASKS_SILENCIOSAS:
            return
        emit(
            "task.enfileirada",
            "ok",
            correlation_id=correlacao,
            message=f"{_nome_curto(sender)} enfileirada",
            payload={"task": sender},
            celery_task_id=(headers or {}).get("id", ""),
            celery_task_name=sender or "",
        )
    except Exception:
        logger.debug("before_task_publish: falha ao marcar correlação", exc_info=True)


@task_prerun.connect
def task_comecou(task_id=None, task=None, **kwargs):
    try:
        correlacao = getattr(task.request, HEADER_CORRELACAO, None) if task else None
        set_correlation_id(correlacao)
        set_tenant_id(getattr(task.request, HEADER_TENANT, None) if task else None)

        if task is not None and task.name in TASKS_SILENCIOSAS:
            return
        emit(
            "task.iniciada",
            "iniciado",
            message=f"{_nome_curto(getattr(task, 'name', ''))} iniciada",
            payload={"task": getattr(task, "name", ""), "tentativa": _tentativa(task)},
            celery_task_id=task_id or "",
            celery_task_name=getattr(task, "name", ""),
        )
    except Exception:
        logger.debug("task_prerun: falha ao emitir", exc_info=True)


@task_postrun.connect
def task_terminou(task_id=None, task=None, retval=None, state=None, **kwargs):
    try:
        if task is not None and task.name in TASKS_SILENCIOSAS:
            return
        # O resultado das tasks do pipeline é um dict {"status": ...} — vale a
        # pena carregá-lo: é o que distingue "success" de "skipped"/"already_done".
        resultado = retval if isinstance(retval, dict) else {"retorno": str(retval)[:200]}
        # RETRY não é falha: a task vai tentar de novo. Contá-la como falha
        # inflava `total_falhas` (nove fragmentos com o Qdrant fora viravam
        # dezenas de "falhas") e fazia a projeção mentir sobre a gravidade.
        status = {"SUCCESS": "ok", "RETRY": "retentando"}.get(state, "falhou")
        emit(
            "task.concluida",
            status,
            message=f"{_nome_curto(getattr(task, 'name', ''))} terminou em {state}",
            payload={"task": getattr(task, "name", ""), "state": state, **resultado},
            celery_task_id=task_id or "",
            celery_task_name=getattr(task, "name", ""),
        )
    except Exception:
        logger.debug("task_postrun: falha ao emitir", exc_info=True)
    finally:
        set_correlation_id(None)
        set_tenant_id(None)


@task_failure.connect
def task_falhou(task_id=None, exception=None, traceback=None, einfo=None, sender=None, **kwargs):
    """Traceback de task que morre — hoje isso não fica registrado em lugar nenhum."""
    try:
        emit(
            "task.falhou",
            "falhou",
            message=f"{_nome_curto(getattr(sender, 'name', ''))}: {type(exception).__name__}: {exception}",
            payload={"task": getattr(sender, "name", ""), "excecao": type(exception).__name__},
            error=str(einfo) if einfo else "",
            celery_task_id=task_id or "",
            celery_task_name=getattr(sender, "name", ""),
        )
    except Exception:
        logger.debug("task_failure: falha ao emitir", exc_info=True)


@task_retry.connect
def task_retentou(request=None, reason=None, sender=None, **kwargs):
    try:
        emit(
            "task.retentada",
            "retentando",
            message=f"{_nome_curto(getattr(sender, 'name', ''))} vai retentar: {reason}",
            payload={
                "task": getattr(sender, "name", ""),
                "tentativa": getattr(request, "retries", None),
                "motivo": str(reason)[:500],
            },
            celery_task_id=getattr(request, "id", "") or "",
            celery_task_name=getattr(sender, "name", ""),
        )
    except Exception:
        logger.debug("task_retry: falha ao emitir", exc_info=True)


@task_revoked.connect
def task_revogada(request=None, terminated=None, expired=None, **kwargs):
    try:
        emit(
            "task.revogada",
            "falhou",
            message="task revogada",
            payload={"terminated": terminated, "expired": expired},
            celery_task_id=getattr(request, "id", "") or "",
            celery_task_name=getattr(request, "name", "") or "",
        )
    except Exception:
        logger.debug("task_revoked: falha ao emitir", exc_info=True)


@worker_ready.connect
def worker_subiu(sender=None, **kwargs):
    emit("worker.pronto", "ok", message="worker disponível",
         payload={"nome": str(getattr(sender, "hostname", ""))})


@worker_shutting_down.connect
def worker_caiu(sender=None, **kwargs):
    emit("worker.encerrando", "ok", message="worker encerrando",
         payload={"nome": str(sender or "")})


def _tentativa(task):
    try:
        return task.request.retries
    except Exception:
        return None

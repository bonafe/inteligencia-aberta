"""Contexto de correlação propagado implicitamente pelo processo.

Existe para que instrumentar o pipeline não exija mudar a assinatura de nenhuma
função já escrita. Quem inicia uma unidade de trabalho (a view que recebe o
artefato, a task que começa a extrair) define o `correlation_id` uma vez; todo
`emit()` posterior no mesmo contexto o herda.

Entre processos a propagação é explícita: por header da task Celery
(`celery_signals.py`) e por campo no corpo do POST entre serviços.
"""

import contextvars
import os
import socket
import uuid

#: Namespace fixo para derivar correlation_id determinístico de um artefato.
#: Usar uuid5 em vez de uuid4 faz com que reprocessamentos e capturas legadas
#: (que nasceram antes deste log existir) caiam na mesma execução, em vez de
#: criarem uma linha nova a cada tentativa.
NAMESPACE_CAPTURA = uuid.UUID("6f1b0e5a-4a3d-5c2f-9b7e-1d2c3a4b5c6d")

_correlation_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "ia_correlation_id", default=None
)
_source: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ia_source", default=None
)
# O tenant acompanha a correlação. Sem ele, os eventos automáticos dos signals
# do Celery — que são justamente os que registram falha de task — nasciam sem
# organização e ficavam invisíveis no painel, que filtra por tenant.
_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ia_tenant_id", default=None
)

_HOSTNAME = socket.gethostname()


def correlacao_de_artefato(artifact_id) -> uuid.UUID:
    """Correlation id estável derivado do id do artefato."""
    return uuid.uuid5(NAMESPACE_CAPTURA, str(artifact_id))


def set_correlation_id(valor) -> uuid.UUID | None:
    if valor is None:
        _correlation_id.set(None)
        return None
    if not isinstance(valor, uuid.UUID):
        try:
            valor = uuid.UUID(str(valor))
        except (ValueError, AttributeError, TypeError):
            return None
    _correlation_id.set(valor)
    return valor


def get_correlation_id() -> uuid.UUID | None:
    return _correlation_id.get()


def set_tenant_id(valor) -> None:
    _tenant_id.set(str(valor) if valor else None)


def get_tenant_id():
    return _tenant_id.get()


def set_source(valor: str | None) -> None:
    _source.set(valor)


def get_source() -> str | None:
    return _source.get()


def limpar() -> None:
    _correlation_id.set(None)
    _source.set(None)
    _tenant_id.set(None)


def identidade_execucao() -> tuple[str, int]:
    """(hostname, pid) — quem está executando. Essencial em cluster."""
    return _HOSTNAME, os.getpid()


def source_padrao() -> str:
    """Deduz a origem quando não foi declarada explicitamente.

    O worker e o beat rodam a partir do mesmo código do portal; a única
    diferença visível no processo é o argv do Celery.
    """
    import sys

    definido = get_source()
    if definido:
        return definido
    argv = " ".join(sys.argv)
    if "beat" in argv:
        return "beat"
    if "celery" in argv or "worker" in argv:
        return "worker"
    return "portal"

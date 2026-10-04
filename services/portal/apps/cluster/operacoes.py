"""Instalar e remover modelos do Ollama de uma máquina — Marco D2 (plano de controle das instâncias).

Duas formas, uma só interface (`solicitar`):

- **máquina local:** cria a `OperacaoModeloOllama` e despacha a task `executar_operacao_modelo`, que fala com
  o Ollama por `apps.cluster.ollama_admin` e vai gravando o progresso;
- **par:** a **máquina offline falha na hora** (nada é criado); online, o comando vai pelo canal assinado
  (`apps.federacao`) e aqui fica um **espelho** da operação, cujo progresso volta por pull
  (`acompanhar_espelhos`).

Regras que valem nos dois casos: só dono/administrador manda (o servidor confere sempre); no máximo **uma
operação ativa por (máquina, modelo)** e um teto de operações ativas por máquina; nome de modelo validado
(só o registry oficial por padrão); toda ação deixa `AuditLog` e todo resultado deixa um `PipelineEvent`
`cluster.modelo`, **sem conteúdo**. Quem pede por um par é um **ator afirmado**: o receptor registra o que
a origem disse, sem poder verificá-lo (ver ADR de controle de instâncias).
"""

import logging
import time
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.permissoes import exige_admin, papel_do_usuario
from apps.artifacts.models import AuditLog
from apps.events.emit import emit
from apps.federacao.canal import ParInacessivel, RespostaInvalida, chamar_par

from . import ollama_admin
from .models import Maquina, MaquinaModeloOllama, MaquinaStatus, OperacaoModeloOllama

logger = logging.getLogger(__name__)

Op = OperacaoModeloOllama
FILA = "ollama_admin"
CAMINHO_OPERACOES = "/federacao/controle/v1/ollama/operacoes/"
LIMITE_ATIVAS_POR_MAQUINA = 3
PROGRESSO_INTERVALO_S = 1.5
SEM_PROGRESSO_S = 300          # operação executando sem novidade: o worker provavelmente caiu
PENDENTE_S = 600               # operação que nem começou: não há worker na fila ollama_admin?
ESPELHO_SEM_NOTICIAS_S = 600   # espelho de um par que não responde
MARGEM_DISCO = 1.1


class OperacaoRecusada(Exception):
    """A operação não pode ser feita agora. `mensagem` é segura para mostrar; `http` é o status a devolver."""

    def __init__(self, mensagem: str, http: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.http = http


class Conflito(OperacaoRecusada):
    def __init__(self, mensagem: str):
        super().__init__(mensagem, 409)


class MaquinaOffline(OperacaoRecusada):
    def __init__(self, mensagem: str):
        super().__init__(mensagem, 503)


class _Cancelada(Exception):
    pass


def _auditar(operacao: str, organizacao, usuario, motivo: str = "", **meta) -> None:
    try:
        AuditLog.objects.create(
            organization=organizacao, user=usuario if getattr(usuario, "pk", None) else None,
            operation=operacao, outcome="permitido", reason=motivo, metadata=meta,
        )
    except Exception:
        logger.exception("falha ao gravar auditoria de %s", operacao)


def _evento(op: Op, status: str, mensagem: str) -> None:
    emit("cluster.modelo", status, source="portal", subject_type="maquina", subject_id=op.maquina_id,
         tenant_id=op.maquina.organizacao_id, message=mensagem,
         payload={"operacao": str(op.id), "tipo": op.tipo, "modelo": op.modelo, "status": op.status})


def serializar(op: Op) -> dict:
    """O que se mostra de uma operação (a quem pede, pelo canal ou pela tela). Sem endereços internos."""
    return {
        "id": str(op.id), "tipo": op.tipo, "modelo": op.modelo, "status": op.status,
        "bytes_total": op.bytes_total, "bytes_concluidos": op.bytes_concluidos, "percentual": op.percentual,
        "fase": op.fase, "erro": (op.erro or "")[:300],
        "criada_em": op.criada_em.isoformat() if op.criada_em else None,
        "finalizada_em": op.finalizada_em.isoformat() if op.finalizada_em else None,
    }


def avisos_remocao(maquina: Maquina, modelo: str) -> list[str]:
    """Avisos que a tela mostra **antes** de remover (não bloqueiam)."""
    import os

    avisos = []
    if MaquinaModeloOllama.objects.filter(maquina=maquina, nome_modelo=modelo, carregado=True).exists():
        avisos.append("Este modelo está carregado na memória agora; remover o descarrega.")
    if maquina.eh_local and modelo in (os.environ.get("OLLAMA_MODELOS", "") or "").split():
        avisos.append("Este modelo está em OLLAMA_MODELOS e voltará no próximo `docker compose up`.")
    return avisos


# ── solicitar ───────────────────────────────────────────────────────────────

def solicitar(maquina: Maquina, tipo: str, modelo: str, *, usuario=None, origem_par: Maquina | None = None,
              ator: dict | None = None) -> Op:
    """Pede para instalar/remover `modelo` em `maquina` e devolve a operação.

    `PermissionDenied` se `usuario` não administra a organização da máquina; `OperacaoRecusada` (e as
    filhas `Conflito` e `MaquinaOffline`) para o resto. Quando o comando **veio de um par** (`origem_par`), a
    autorização já foi feita pela rota do canal; aqui só se aplicam as regras da operação.
    """
    if tipo not in Op.Tipo.values:
        raise OperacaoRecusada("tipo de operação inválido")
    try:
        modelo = ollama_admin.validar_nome_modelo(modelo)
    except ValueError as exc:
        raise OperacaoRecusada(str(exc)) from None
    if usuario is not None:
        exige_admin(usuario, maquina.organizacao)
    if usuario is not None and not ator:
        ator = {"user": str(usuario.pk), "papel": papel_do_usuario(usuario, maquina.organizacao) or "",
                "org": str(maquina.organizacao_id)}

    op = _solicitar_local(maquina, tipo, modelo, usuario, origem_par, ator) if maquina.eh_local \
        else _solicitar_remota(maquina, tipo, modelo, usuario, ator)
    _auditar(f"modelo_ollama.{tipo}_solicitado", maquina.organizacao, usuario, modelo=modelo, maquina=str(maquina.id),
             operacao_id=str(op.id), origem_par=str(origem_par.id) if origem_par else None, ator=ator or {})
    return op


def _checar_capacidade_local(maquina: Maquina, tipo: str) -> None:
    if not getattr(settings, "OLLAMA_HOST", ""):
        raise OperacaoRecusada("o Ollama não está configurado nesta instância", 409)
    status = MaquinaStatus.objects.filter(maquina=maquina).first()
    if status is not None and status.ollama_disponivel is False:
        raise MaquinaOffline("o Ollama desta máquina não está respondendo")
    if tipo == Op.Tipo.PULL and status is not None and status.disco_ollama_livre_gb is not None:
        reserva = float(getattr(settings, "OLLAMA_RESERVA_DISCO_GB", 10))
        if status.disco_ollama_livre_gb < reserva:
            raise OperacaoRecusada(
                f"pouco espaço livre para os modelos ({status.disco_ollama_livre_gb:.1f} GB; a reserva mínima é {reserva:.0f} GB)", 507)


def _solicitar_local(maquina, tipo, modelo, usuario, origem_par, ator) -> Op:
    _checar_capacidade_local(maquina, tipo)
    if maquina.operacoes_modelo.filter(status__in=Op.ATIVOS).count() >= LIMITE_ATIVAS_POR_MAQUINA:
        raise Conflito(f"já há {LIMITE_ATIVAS_POR_MAQUINA} operações em andamento nesta máquina; espere uma terminar")
    try:
        with transaction.atomic():
            op = Op.objects.create(maquina=maquina, tipo=tipo, modelo=modelo, solicitado_por=usuario,
                                   origem_par=origem_par, ator_afirmado=ator or {})
    except IntegrityError:
        raise Conflito(f"já existe uma operação em andamento para {modelo} nesta máquina") from None
    despachar(op)
    return op


def despachar(op: Op) -> None:
    """Enfileira a execução (fila `ollama_admin`). Se o broker falhar, a operação falha na hora."""
    from .tasks import executar_operacao_modelo

    try:
        resultado = executar_operacao_modelo.apply_async(args=[str(op.id)], queue=FILA)
    except Exception:
        logger.exception("não foi possível enfileirar a operação %s", op.id)
        _finalizar(op, Op.Status.FALHOU, "Não foi possível enfileirar a operação (o Redis está de pé?).")
        raise OperacaoRecusada("não foi possível enfileirar a operação", 503) from None
    Op.objects.filter(pk=op.pk).update(celery_task_id=resultado.id or "")


def _solicitar_remota(par, tipo, modelo, usuario, ator) -> Op:
    if par.tipo != Maquina.Tipo.PROPRIO or par.estado != Maquina.Estado.CONFIRMADO or not par.ativa:
        raise OperacaoRecusada("só é possível controlar pares próprios e confirmados", 403)
    if not par.esta_online:                                      # falha na hora, sem criar nada
        raise MaquinaOffline(f"“{par.apelido}” está offline")
    if par.operacoes_modelo.filter(status__in=Op.ATIVOS, modelo=modelo).exists():
        raise Conflito(f"já existe uma operação em andamento para {modelo} em “{par.apelido}”")
    try:
        resposta = chamar_par(par, "POST", CAMINHO_OPERACOES, {"tipo": tipo, "modelo": modelo}, ator=ator)
    except (ParInacessivel, RespostaInvalida):
        raise MaquinaOffline(f"“{par.apelido}” não respondeu") from None
    mensagem = str(resposta.json.get("erro") or "")[:200]
    if resposta.status == 409:
        raise Conflito(mensagem or "o par já tem uma operação em andamento para este modelo")
    if resposta.status in (400, 507):
        raise OperacaoRecusada(mensagem or "o par recusou a operação", resposta.status)
    if resposta.status in (403, 404):
        raise OperacaoRecusada(
            "o par não aceita comandos desta instância (ele precisa tê-la marcada como par próprio e confirmado)", 403)
    if resposta.status != 200 or not resposta.assinada:
        raise MaquinaOffline(f"“{par.apelido}” não respondeu como esperado (HTTP {resposta.status})")
    try:
        remoto = uuid.UUID(str(resposta.json.get("id")))
    except ValueError:
        raise OperacaoRecusada("a resposta do par não identificou a operação", 502) from None
    try:
        with transaction.atomic():
            return Op.objects.create(
                maquina=par, tipo=tipo, modelo=modelo, solicitado_por=usuario, ator_afirmado=ator or {},
                operacao_remota_id=remoto, ultimo_progresso_em=timezone.now(),
                status=_status_valido(resposta.json.get("status")) or Op.Status.PENDENTE,
            )
    except IntegrityError:
        raise Conflito(f"já existe uma operação em andamento para {modelo} em “{par.apelido}”") from None


def _status_valido(valor) -> str | None:
    return valor if valor in Op.Status.values else None


# ── execução (a task chama isto) ────────────────────────────────────────────

def _finalizar(op: Op, status: str, erro: str = "") -> None:
    """Fecha a operação **só se ainda estiver ativa** (uma cancelada continua cancelada)."""
    agora = timezone.now()
    campos = {"status": status, "erro": erro[:500], "finalizada_em": agora, "ultimo_progresso_em": agora}
    if status == Op.Status.CONCLUIDA:
        campos["fase"] = "concluída"
        if op.tipo == Op.Tipo.PULL:
            campos["bytes_concluidos"] = (Op.objects.filter(pk=op.pk).values_list("bytes_total", flat=True).first() or 0)
    if Op.objects.filter(pk=op.pk, status__in=Op.ATIVOS).update(**campos):
        op.refresh_from_db()
        _evento(op, "ok" if status == Op.Status.CONCLUIDA else "falhou", f"{op.get_tipo_display()} {op.modelo}: {status}")


def _checar_disco(total_bytes: int) -> None:
    from .tasks import _disco_ollama_livre_gb

    livre = _disco_ollama_livre_gb()
    if livre is None:                                            # desconhecido: não bloqueia
        return
    reserva = float(getattr(settings, "OLLAMA_RESERVA_DISCO_GB", 10))
    necessario = total_bytes / 1e9 * MARGEM_DISCO + reserva
    if livre < necessario:
        raise ollama_admin.DiscoCheio(f"o modelo precisa de cerca de {necessario:.1f} GB (com a reserva) e há {livre:.1f} GB livres")


def _executar_pull(op: Op) -> None:
    camadas: dict[str, list[int]] = {}
    ultimo_envio, ultimo_pct, disco_checado = 0.0, -1, False
    gerador = ollama_admin.pull_stream(op.modelo)
    try:
        for evento in gerador:
            digest, total, feito = evento.get("digest"), evento.get("total"), evento.get("completed")
            if digest and isinstance(total, int) and total >= 0:
                anterior = camadas.get(digest, [0, 0])
                camadas[digest] = [total, feito if isinstance(feito, int) and feito >= 0 else anterior[1]]
            bytes_total = sum(t for t, _ in camadas.values())
            bytes_feitos = sum(min(f, t) for t, f in camadas.values())
            if bytes_total and not disco_checado:
                disco_checado = True
                _checar_disco(bytes_total)
            pct = int(bytes_feitos * 100 / bytes_total) if bytes_total else -1
            agora = time.monotonic()
            if agora - ultimo_envio >= PROGRESSO_INTERVALO_S or pct != ultimo_pct and pct in (0, 100):
                ultimo_envio, ultimo_pct = agora, pct
                atualizadas = Op.objects.filter(pk=op.pk, status=Op.Status.EXECUTANDO).update(
                    bytes_total=bytes_total or None, bytes_concluidos=bytes_feitos,
                    fase=str(evento.get("status") or "")[:120], ultimo_progresso_em=timezone.now())
                if not atualizadas:                              # cancelada (ou fechada) por fora
                    raise _Cancelada()
    finally:
        gerador.close()
    Op.objects.filter(pk=op.pk, status=Op.Status.EXECUTANDO).update(
        bytes_total=sum(t for t, _ in camadas.values()) or None, bytes_concluidos=sum(t for t, _ in camadas.values()))


def executar(operacao_id) -> str:
    """Executa uma operação **local**. Idempotente: só roda se estiver `pendente`. Nunca levanta."""
    with transaction.atomic():
        op = Op.objects.select_for_update().select_related("maquina").filter(pk=operacao_id).first()
        if op is None or op.status != Op.Status.PENDENTE:
            return "ignorada"
        if not op.maquina.eh_local:
            return "nao_local"
        agora = timezone.now()
        op.status, op.iniciada_em, op.ultimo_progresso_em = Op.Status.EXECUTANDO, agora, agora
        op.save(update_fields=["status", "iniciada_em", "ultimo_progresso_em"])
    _evento(op, "iniciado", f"{op.get_tipo_display()} {op.modelo} iniciada")

    try:
        if op.tipo == Op.Tipo.PULL:
            _executar_pull(op)
        else:
            ollama_admin.delete(op.modelo)
    except _Cancelada:
        return "cancelada"
    except ollama_admin.ModeloNaoEncontrado:
        _finalizar(op, Op.Status.FALHOU, "O Ollama não conhece este modelo (confira o nome e a tag)."
                   if op.tipo == Op.Tipo.PULL else "O modelo não está instalado.")
    except ollama_admin.DiscoCheio as exc:
        _finalizar(op, Op.Status.FALHOU, f"Espaço insuficiente: {exc}")
    except ollama_admin.OllamaIndisponivel as exc:
        _finalizar(op, Op.Status.FALHOU, f"O Ollama ficou inacessível ou a conexão caiu ({exc}). O que já foi baixado é "
                   "reaproveitado: tente instalar de novo.")
    except ollama_admin.ErroOllama as exc:
        _finalizar(op, Op.Status.FALHOU, str(exc))
    except Exception:
        logger.exception("erro inesperado na operação %s", op.id)
        _finalizar(op, Op.Status.FALHOU, "Erro inesperado (veja o log do worker).")
    else:
        _finalizar(op, Op.Status.CONCLUIDA)
    _atualizar_inventario_local(op.maquina)
    return op.status


def _atualizar_inventario_local(maquina: Maquina) -> None:
    """Depois de instalar/remover, a tela deve ver o resultado sem esperar o próximo heartbeat."""
    from .tasks import _inventario_local

    try:
        _inventario_local(maquina)
    except Exception:
        logger.exception("falha ao atualizar o inventário depois da operação")


# ── cancelar ────────────────────────────────────────────────────────────────

def cancelar(op: Op, *, usuario=None, origem_par: Maquina | None = None) -> Op:
    """Cancela uma operação ativa. Idempotente. Num par, o par precisa estar online (senão falha na hora)."""
    if usuario is not None:
        exige_admin(usuario, op.maquina.organizacao)
    op.refresh_from_db()
    if not op.ativa:
        return op
    if op.maquina.eh_local:
        estava = op.status
        agora = timezone.now()
        if Op.objects.filter(pk=op.pk, status__in=Op.ATIVOS).update(
                status=Op.Status.CANCELADA, erro="Cancelada.", finalizada_em=agora, ultimo_progresso_em=agora):
            if op.celery_task_id:
                try:
                    from celery import current_app

                    current_app.control.revoke(op.celery_task_id, terminate=estava == Op.Status.EXECUTANDO, signal="SIGKILL")
                except Exception:
                    logger.exception("falha ao revogar a task da operação %s", op.id)
    else:
        par = op.maquina
        if not par.esta_online:
            raise MaquinaOffline(f"“{par.apelido}” está offline")
        try:
            resposta = chamar_par(par, "POST", f"{CAMINHO_OPERACOES}{op.operacao_remota_id}/cancelar/", {},
                                  ator=op.ator_afirmado or None)
        except (ParInacessivel, RespostaInvalida):
            raise MaquinaOffline(f"“{par.apelido}” não respondeu") from None
        if resposta.status not in (200, 404):
            raise OperacaoRecusada("o par recusou o cancelamento", resposta.status if resposta.status in (403, 409) else 502)
        agora = timezone.now()
        Op.objects.filter(pk=op.pk, status__in=Op.ATIVOS).update(
            status=Op.Status.CANCELADA, erro="Cancelada.", finalizada_em=agora, ultimo_progresso_em=agora)
    op.refresh_from_db()
    _auditar("modelo_ollama.cancelado", op.maquina.organizacao, usuario, modelo=op.modelo, operacao_id=str(op.id),
             maquina=str(op.maquina_id), origem_par=str(origem_par.id) if origem_par else None)
    _evento(op, "ignorado", f"{op.get_tipo_display()} {op.modelo} cancelada")
    return op


# ── acompanhamento: espelhos de operações remotas e operações travadas ──────

def _espelhar(op: Op, dados: dict) -> None:
    status = _status_valido(dados.get("status"))
    if status is None:
        return
    campos = {"status": status, "fase": str(dados.get("fase") or "")[:120], "ultimo_progresso_em": timezone.now()}
    for nome in ("bytes_total", "bytes_concluidos"):
        valor = dados.get(nome)
        if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
            campos[nome] = valor
    if status not in Op.ATIVOS:
        campos["finalizada_em"] = timezone.now()
        campos["erro"] = str(dados.get("erro") or "")[:500]
    if Op.objects.filter(pk=op.pk, status__in=Op.ATIVOS).update(**campos) and status not in Op.ATIVOS:
        op.refresh_from_db()
        _evento(op, "ok" if status == Op.Status.CONCLUIDA else ("ignorado" if status == Op.Status.CANCELADA else "falhou"),
                f"{op.get_tipo_display()} {op.modelo} em {op.maquina.apelido}: {status}")


def acompanhar_espelhos(*, agora=None, chamar=chamar_par) -> dict:
    """Traz do par o progresso das operações espelhadas. Sem notícias por muito tempo, a operação falha."""
    agora = agora or timezone.now()
    resumo = {"consultadas": 0, "sem_noticias": 0}
    for op in Op.objects.filter(status__in=Op.ATIVOS, operacao_remota_id__isnull=False, maquina__eh_local=False).select_related("maquina"):
        resumo["consultadas"] += 1
        try:
            resposta = chamar(op.maquina, "GET", f"{CAMINHO_OPERACOES}{op.operacao_remota_id}/")
        except (ParInacessivel, RespostaInvalida):
            resposta = None
        if resposta is not None and resposta.status == 404:
            _finalizar(op, Op.Status.FALHOU, "A operação não existe mais no par.")
        elif resposta is not None and resposta.status == 200 and resposta.assinada:
            _espelhar(op, resposta.json)
        else:
            referencia = op.ultimo_progresso_em or op.criada_em
            if (agora - referencia).total_seconds() > ESPELHO_SEM_NOTICIAS_S:
                resumo["sem_noticias"] += 1
                _finalizar(op, Op.Status.FALHOU, f"Sem notícias de “{op.maquina.apelido}” há mais de {ESPELHO_SEM_NOTICIAS_S // 60} min; "
                           "a operação pode ter continuado nele.")
    return resumo


def varrer_travadas(*, agora=None) -> int:
    """Marca como falhas as operações **locais** que ninguém está executando. Devolve quantas."""
    agora = agora or timezone.now()
    total = 0
    for op in Op.objects.filter(status__in=Op.ATIVOS, maquina__eh_local=True).select_related("maquina"):
        if op.status == Op.Status.PENDENTE and (agora - op.criada_em).total_seconds() > PENDENTE_S:
            _finalizar(op, Op.Status.FALHOU, "A operação não chegou a começar. O worker da fila `ollama_admin` está de pé?")
            total += 1
        elif op.status == Op.Status.EXECUTANDO and (agora - (op.ultimo_progresso_em or op.iniciada_em or op.criada_em)).total_seconds() > SEM_PROGRESSO_S:
            _finalizar(op, Op.Status.FALHOU, "A operação ficou sem progresso (o worker caiu?). Tente de novo.")
            total += 1
    return total

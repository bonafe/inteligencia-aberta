"""Projeção do log de eventos em `PipelineRun`.

`aplicar_evento` é a única função que escreve em `PipelineRun`. Ela é chamada
de dois lugares e precisa produzir o mesmo resultado nos dois:

1. incrementalmente, de dentro de `emit()`, na mesma transação do evento;
2. em lote, por `manage.py reconstruir_projecoes`, relendo o log inteiro.

Essa equivalência é o que garante que a projeção é de fato derivada do log —
e está coberta por teste (`tests/test_events.py`).

A função **não** deduplica: cada evento deve ser passado a ela uma única vez.
No caminho incremental isso é garantido por construção (a projeção acontece na
mesma transação que insere o evento); na reconstrução, por o comando apagar as
projeções antes de reaplicar. Uma guarda por `sequence` aqui seria pior que
inútil — com vários workers, eventos chegam fora de ordem, e ela descartaria
eventos legítimos.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

#: Ordem de exibição da trilha de etapas no painel. Cada item é
#: (stage, rótulo curto). Stages fora desta lista continuam sendo gravados em
#: `PipelineRun.etapas` e aparecem na timeline detalhada — apenas não ganham
#: uma marca própria na trilha resumida.
TRILHA = [
    ("captura.recebida", "captura"),
    ("captura.armazenada", "minio"),
    ("captura.registrada", "artefato"),
    ("extracao.iniciada", "fila"),
    ("extracao.trafilatura", "texto"),
    ("extracao.dom2parser", "dom2parser"),
    ("extracao.extruct", "extruct"),
    ("deteccao.page_type", "detecção"),
    ("extracao.cascata", "estruturados"),
    ("extracao.concluida", "extração"),
    ("fragmentacao.concluida", "fragmentos"),
    ("embedding.concluido", "embeddings"),
]

#: Stages que acontecem uma vez por item (um fragmento, por exemplo). A
#: projeção conta **itens distintos**, não eventos: o catch-up do Beat e o
#: redespacho de tasks fazem o mesmo fragmento gerar vários eventos, e contar
#: eventos dava "embeddings 15" para nove fragmentos. Contar itens distintos é
#: o que expressa progresso.
STAGES_CONTAVEIS = {"embedding.concluido"}

#: Teto de identificadores guardados por etapa contável. Acima disso a contagem
#: passa a ser aproximada — um documento com mais de 2000 fragmentos é caso
#: patológico, e vale mais manter a projeção pequena.
MAX_ITENS_RASTREADOS = 2000

#: Um reprocessamento recomeça a trilha. Sem isto, o contador de embeddings
#: somava as tentativas ("embeddings 18" para 9 fragmentos) e a trilha misturava
#: o resultado de execuções diferentes. O histórico completo continua no log —
#: é só a projeção que passa a refletir a tentativa corrente.
STAGES_QUE_REINICIAM = {"extracao.reiniciada"}

#: O que sobrevive a um reinício: o que aconteceu antes da extração.
PREFIXOS_PRESERVADOS = ("captura.", "artefato.", "reprocessamento.")

#: Stages cujo fracasso condena a execução inteira. Uma falha em
#: `extracao.extruct`, por exemplo, degrada mas não impede a captura.
STAGES_CRITICOS = {
    "captura.armazenada",
    "captura.registrada",
    "extracao.minio",
    "extracao.concluida",
    "fragmentacao.concluida",
}


def _absorver_identificacao(run, evento) -> None:
    """Preenche url/título/artefato/tenant assim que algum evento os revelar."""
    payload = evento.payload or {}

    if evento.tenant_id and not run.tenant_id:
        run.tenant_id = evento.tenant_id
    if evento.user_id and not run.user_id:
        run.user_id = evento.user_id

    url = payload.get("url")
    if url and not run.url:
        run.url = str(url)[:2048]
    titulo = payload.get("titulo") or payload.get("title")
    if titulo and not run.titulo:
        run.titulo = str(titulo)[:500]

    if evento.subject_type == "artifact" and evento.subject_id and not run.artifact_id:
        run.artifact_id = evento.subject_id
    artifact_id = payload.get("artifact_id")
    if artifact_id and not run.artifact_id:
        run.artifact_id = artifact_id


def _identidade_do_item(evento):
    """Como identificar o item a que este evento se refere.

    `indice` (posição do fragmento) é preferido por ser curto; `subject_id`
    serve para etapas contáveis que não tenham índice.
    """
    payload = evento.payload or {}
    indice = payload.get("indice")
    if indice is not None:
        return indice
    return str(evento.subject_id) if evento.subject_id else None


def _contar_itens(anterior: dict, evento) -> tuple[list, int]:
    vistos = list(anterior.get("itens") or [])
    item = _identidade_do_item(evento)

    if item is None or len(vistos) >= MAX_ITENS_RASTREADOS:
        # Sem identidade ou acima do teto: volta a contar eventos.
        return vistos, (anterior.get("n") or 0) + 1

    if item not in vistos:
        vistos.append(item)
    # Ordenado para que a projeção não dependa da ORDEM DE CHEGADA dos eventos.
    # Sem isto, o caminho incremental (workers concorrentes) e a reconstrução
    # (estrita por sequence) produziam a mesma contagem em listas diferentes, e
    # a equivalência entre os dois deixava de ser verificável.
    vistos.sort(key=lambda x: (isinstance(x, str), x))
    return vistos, len(vistos)


def _recalcular_status(run) -> None:
    """Deduz o status geral da execução a partir das etapas já registradas."""
    etapas = run.etapas or {}

    critico_falhou = any(
        dados.get("status") == "falhou"
        for stage, dados in etapas.items()
        if stage in STAGES_CRITICOS
    )
    if critico_falhou:
        run.status = run.__class__.Status.FALHOU
        return

    if "captura.orfa" in etapas:
        run.status = run.__class__.Status.FALHOU
        return

    ignorada = etapas.get("extracao.ignorada", {}).get("status")
    if ignorada and "extracao.concluida" not in etapas:
        run.status = run.__class__.Status.IGNORADO
        return

    esperados = etapas.get("fragmentacao.concluida", {}).get("n") or 0
    feitos = etapas.get("embedding.concluido", {}).get("n") or 0

    if esperados and feitos >= esperados:
        run.status = (
            run.__class__.Status.PARCIAL if run.total_falhas
            else run.__class__.Status.CONCLUIDO
        )
        return

    run.status = run.__class__.Status.EM_ANDAMENTO


def aplicar_evento(evento, *, run=None):
    """Reflete um evento na projeção. Idempotente por `sequence`.

    Devolve o `PipelineRun` afetado, ou None se algo falhou — a projeção nunca
    pode derrubar a gravação do evento.
    """
    from .models import PipelineRun

    try:
        with transaction.atomic():
            if run is None:
                # select_for_update é obrigatório: em cluster, dois workers
                # podem estar aplicando eventos da mesma captura ao mesmo tempo.
                run = (
                    PipelineRun.objects.select_for_update()
                    .filter(correlation_id=evento.correlation_id)
                    .first()
                )
            if run is None:
                run = PipelineRun(
                    correlation_id=evento.correlation_id,
                    iniciado_em=evento.occurred_at,
                    atualizado_em=evento.occurred_at,
                )

            _absorver_identificacao(run, evento)

            etapas = dict(run.etapas or {})
            if evento.stage in STAGES_QUE_REINICIAM:
                etapas = {
                    k: v for k, v in etapas.items()
                    if k.startswith(PREFIXOS_PRESERVADOS)
                }
                run.total_falhas = 0
                run.concluido_em = None
            anterior = etapas.get(evento.stage, {})

            # Os campos descritivos vêm do evento de MAIOR `sequence` desta
            # etapa, não do último a ser aplicado. Com workers concorrentes as
            # duas ordens divergem, e sem esta regra a projeção passaria a
            # depender da ordem de chegada — deixando de ser função do log.
            mais_novo = evento.sequence >= (anterior.get("seq") or 0)
            entrada = dict(anterior)
            if mais_novo:
                entrada.update({
                    "seq": evento.sequence,
                    "status": evento.status,
                    "em": evento.occurred_at.isoformat(),
                    "ms": evento.duration_ms,
                    "msg": evento.message[:300],
                })

            if evento.stage in STAGES_CONTAVEIS:
                # A contagem é um conjunto: independe de ordem por construção.
                entrada["itens"], entrada["n"] = _contar_itens(anterior, evento)
                total = (evento.payload or {}).get("total")
                if total:
                    entrada["total"] = total
            elif evento.payload and mais_novo:
                for chave in ("n", "n_fragmentos", "total"):
                    if chave in evento.payload:
                        entrada["n"] = evento.payload[chave]
                        break
            etapas[evento.stage] = entrada
            run.etapas = etapas

            run.total_eventos += 1
            if evento.status == "falhou":
                run.total_falhas += 1
            # Marca d'água, não guarda de idempotência: com vários workers, um
            # evento de sequence menor pode chegar aqui depois de um maior, e
            # descartá-lo por isso perdia contagem (nove embeddings no log
            # viravam sete na trilha). Cada evento é projetado exatamente uma
            # vez porque `emit()` o projeta na mesma transação em que o insere;
            # a reconstrução garante a unicidade do seu lado.
            run.ultimo_evento_sequence = max(run.ultimo_evento_sequence, evento.sequence)
            # Extremos, não "o último aplicado": idem, independência de ordem.
            run.atualizado_em = max(run.atualizado_em, evento.occurred_at)
            run.iniciado_em = min(run.iniciado_em, evento.occurred_at)

            _recalcular_status(run)

            if run.status in (
                PipelineRun.Status.CONCLUIDO,
                PipelineRun.Status.PARCIAL,
                PipelineRun.Status.FALHOU,
                PipelineRun.Status.IGNORADO,
            ):
                run.concluido_em = run.atualizado_em
                delta = run.concluido_em - run.iniciado_em
                run.duracao_ms = int(delta.total_seconds() * 1000)
            else:
                run.concluido_em = None
                run.duracao_ms = None

            run.save()
            return run
    except Exception:
        logger.exception(
            "projeção falhou para o evento %s (stage=%s)", evento.id, evento.stage
        )
        return None

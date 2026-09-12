"""Garantias mínimas do log de eventos.

Três coisas não podem quebrar, porque sua quebra é silenciosa:

1. Emitir evento nunca derruba quem chamou — nem com o Redis fora.
2. Um payload grande demais não impede a gravação do evento.
3. A projeção reconstruída a partir do log é idêntica à incremental. Sem isso,
   "o log é a fonte da verdade" seria só uma frase.
"""

import uuid
from unittest import mock

import pytest
from django.utils import timezone

from apps.accounts.models import Organization, User
from apps.events.emit import PAYLOAD_MAX_BYTES, emit, etapa
from apps.events.models import PipelineEvent, PipelineRun
from apps.events.projecao import aplicar_evento

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(
        name="Org de Teste", slug="org-teste", org_type="individual", owner=dono
    )


def test_emit_grava_e_sequencia_avanca(tenant):
    a = emit("teste.um", "ok", tenant_id=tenant.id, message="primeiro")
    b = emit("teste.dois", "ok", tenant_id=tenant.id, message="segundo")

    assert a is not None and b is not None
    assert b.sequence > a.sequence
    assert PipelineEvent.objects.count() == 2


def test_emit_nao_levanta_com_channel_layer_fora(tenant):
    """Redis fora não pode impedir a gravação nem propagar exceção."""
    with mock.patch(
        "channels.layers.get_channel_layer", side_effect=OSError("redis fora")
    ):
        evento = emit("teste.redis", "ok", tenant_id=tenant.id)

    assert evento is not None
    assert PipelineEvent.objects.filter(stage="teste.redis").exists()


def test_payload_grande_e_truncado_mas_o_evento_e_gravado(tenant):
    evento = emit(
        "teste.payload", "ok", tenant_id=tenant.id,
        payload={"lixo": "x" * (PAYLOAD_MAX_BYTES * 2), "registros": 7, "nome": "abc"},
    )

    assert evento is not None
    assert evento.payload["_truncado"] is True
    # Os escalares sobrevivem: são eles que servem ao diagnóstico.
    assert evento.payload["registros"] == 7
    assert evento.payload["nome"] == "abc"
    assert "lixo" not in evento.payload


def test_payload_nunca_carrega_conteudo_capturado(tenant):
    evento = emit(
        "teste.sigilo", "ok", tenant_id=tenant.id,
        payload={"html": "<html>…</html>", "api_key": "segredo", "chars": 120},
    )

    assert "html" not in evento.payload
    assert "api_key" not in evento.payload
    assert evento.payload["chars"] == 120
    assert evento.payload["_chaves_omitidas"] == 2


def test_evento_e_imutavel(tenant):
    from apps.events.models import PipelineEventoImutavelError

    evento = emit("teste.imutavel", "ok", tenant_id=tenant.id)
    evento.message = "alterado"

    with pytest.raises(PipelineEventoImutavelError):
        evento.save()
    with pytest.raises(PipelineEventoImutavelError):
        evento.delete()


def test_etapa_marca_vazio_sem_confundir_com_falha(tenant):
    with etapa("teste.etapa", tenant_id=tenant.id) as e:
        e.vazio("nada encontrado", registros=0)

    evento = PipelineEvent.objects.get(stage="teste.etapa")
    assert evento.status == "vazio"
    assert evento.error == ""
    assert evento.payload["registros"] == 0
    assert evento.duration_ms is not None


def test_etapa_registra_falha_e_relanca(tenant):
    with pytest.raises(ValueError):
        with etapa("teste.falha", tenant_id=tenant.id):
            raise ValueError("estourou")

    evento = PipelineEvent.objects.get(stage="teste.falha")
    assert evento.status == "falhou"
    assert "ValueError" in evento.error


def test_projecao_reconstruida_e_igual_a_incremental(tenant):
    """O contrato central: PipelineRun é derivável do log, sempre."""
    correlacao = uuid.uuid4()
    for stage, status, payload in [
        ("captura.recebida", "ok", {"url": "https://exemplo.test/a", "titulo": "Exemplo"}),
        ("captura.armazenada", "ok", {}),
        ("extracao.dom2parser", "vazio", {"registros_utilizaveis": 0}),
        ("extracao.concluida", "ok", {}),
        ("fragmentacao.concluida", "ok", {"n": 2}),
        ("embedding.concluido", "ok", {"total": 2}),
        ("embedding.concluido", "ok", {"total": 2}),
    ]:
        emit(stage, status, correlation_id=correlacao, tenant_id=tenant.id, payload=payload)

    incremental = PipelineRun.objects.get(correlation_id=correlacao)
    antes = {
        "status": incremental.status,
        "etapas": incremental.etapas,
        "url": incremental.url,
        "titulo": incremental.titulo,
        "total_eventos": incremental.total_eventos,
        "total_falhas": incremental.total_falhas,
        "ultimo": incremental.ultimo_evento_sequence,
    }
    assert antes["status"] == PipelineRun.Status.CONCLUIDO
    assert antes["etapas"]["embedding.concluido"]["n"] == 2
    assert antes["etapas"]["extracao.dom2parser"]["status"] == "vazio"

    # Joga a projeção fora e reconstrói só a partir dos eventos.
    PipelineRun.objects.all().delete()
    for evento in PipelineEvent.objects.order_by("sequence"):
        aplicar_evento(evento)

    reconstruido = PipelineRun.objects.get(correlation_id=correlacao)
    assert {
        "status": reconstruido.status,
        "etapas": reconstruido.etapas,
        "url": reconstruido.url,
        "titulo": reconstruido.titulo,
        "total_eventos": reconstruido.total_eventos,
        "total_falhas": reconstruido.total_falhas,
        "ultimo": reconstruido.ultimo_evento_sequence,
    } == antes


def test_eventos_fora_de_ordem_nao_somem(tenant):
    """Com vários workers, eventos chegam à projeção fora de ordem.

    A regressão que este teste tranca: uma guarda `sequence <= último` fazia a
    trilha perder contagem (nove embeddings no log viravam sete na tela).
    """
    correlacao = uuid.uuid4()
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("fragmentacao.concluida", "ok", correlation_id=correlacao,
         tenant_id=tenant.id, payload={"n": 3})

    # Três eventos gravados, aplicados à projeção em ordem invertida.
    from apps.events.models import PipelineEvent as PE

    PipelineRun.objects.filter(correlation_id=correlacao).delete()
    eventos = list(
        PE.objects.filter(correlation_id=correlacao).order_by("sequence")
    )
    for evento in reversed(eventos):
        aplicar_evento(evento)

    run = PipelineRun.objects.get(correlation_id=correlacao)
    assert run.total_eventos == len(eventos)
    assert run.ultimo_evento_sequence == eventos[-1].sequence


def test_falha_em_etapa_critica_marca_execucao_como_falhou(tenant):
    correlacao = uuid.uuid4()
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("extracao.minio", "falhou", correlation_id=correlacao, tenant_id=tenant.id,
         error="MinIO indisponível")

    run = PipelineRun.objects.get(correlation_id=correlacao)
    assert run.status == PipelineRun.Status.FALHOU
    assert run.total_falhas == 1


# ── Canal ao vivo ────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_websocket_recusa_anonimo():
    """O painel é multi-tenant: ninguém entra sem sessão."""
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from config.asgi import application

    com = WebsocketCommunicator(application, "/ws/eventos/")
    com.scope["user"] = AnonymousUser()
    conectou, _ = await com.connect()
    assert conectou is False
    await com.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_websocket_so_entrega_eventos_do_proprio_tenant():
    """Isolamento feito na assinatura dos grupos, não na apresentação."""
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator

    from apps.accounts.models import Membership
    from config.asgi import application

    @sync_to_async
    def montar():
        alice = User.objects.create_user(username="alice", password="x")
        bob = User.objects.create_user(username="bob", password="x")
        org_a = Organization.objects.create(
            name="A", slug="org-a", org_type="individual", owner=alice
        )
        org_b = Organization.objects.create(
            name="B", slug="org-b", org_type="individual", owner=bob
        )
        Membership.objects.create(user=alice, organization=org_a, role="owner")
        Membership.objects.create(user=bob, organization=org_b, role="owner")
        return alice, org_a, org_b

    alice, org_a, org_b = await montar()

    com = WebsocketCommunicator(application, "/ws/eventos/")
    com.scope["user"] = alice
    conectou, _ = await com.connect()
    assert conectou is True
    assert (await com.receive_json_from())["tipo"] == "conectado"

    # Evento de outra organização: não pode chegar.
    await sync_to_async(emit)("teste.vizinho", "ok", tenant_id=org_b.id)
    # Evento da organização da Alice: tem de chegar.
    await sync_to_async(emit)("teste.meu", "ok", tenant_id=org_a.id, message="visível")

    recebido = await com.receive_json_from(timeout=5)
    assert recebido["evento"]["stage"] == "teste.meu"
    await com.disconnect()


def test_evento_de_infraestrutura_nao_vira_execucao(tenant):
    """Um worker subindo não é uma captura — não pode virar linha no painel."""
    antes = PipelineRun.objects.count()

    emit("worker.pronto", "ok", message="worker disponível")
    emit("catchup.varredura", "ok", source="beat", payload={"extracao": 2})

    assert PipelineEvent.objects.filter(stage="worker.pronto").exists()
    assert PipelineEvent.objects.filter(stage="catchup.varredura").exists()
    assert PipelineRun.objects.count() == antes


def test_retry_nao_conta_como_falha(tenant):
    """Um retry é uma tentativa a mais, não um fracasso da execução."""
    import uuid as _uuid
    correlacao = _uuid.uuid4()
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("task.retentada", "retentando", correlation_id=correlacao, tenant_id=tenant.id)
    emit("task.concluida", "retentando", correlation_id=correlacao, tenant_id=tenant.id)

    run = PipelineRun.objects.get(correlation_id=correlacao)
    assert run.total_falhas == 0
    assert run.status == PipelineRun.Status.EM_ANDAMENTO


def test_reprocessamento_reinicia_a_trilha(tenant):
    """A trilha mostra a tentativa atual; o histórico fica no log."""
    import uuid as _uuid
    correlacao = _uuid.uuid4()

    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id,
         payload={"url": "https://exemplo.test/x"})
    emit("extracao.dom2parser", "falhou", correlation_id=correlacao, tenant_id=tenant.id)
    emit("fragmentacao.concluida", "ok", correlation_id=correlacao, tenant_id=tenant.id,
         payload={"n": 3})
    for _ in range(3):
        emit("embedding.concluido", "ok", correlation_id=correlacao, tenant_id=tenant.id)

    run = PipelineRun.objects.get(correlation_id=correlacao)
    assert run.etapas["embedding.concluido"]["n"] == 3
    assert run.total_falhas == 1

    # Segunda tentativa.
    emit("extracao.reiniciada", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("fragmentacao.concluida", "ok", correlation_id=correlacao, tenant_id=tenant.id,
         payload={"n": 3})
    for _ in range(3):
        emit("embedding.concluido", "ok", correlation_id=correlacao, tenant_id=tenant.id)

    run.refresh_from_db()
    # Contador da tentativa corrente, não a soma das duas.
    assert run.etapas["embedding.concluido"]["n"] == 3
    # A falha da tentativa anterior não pesa mais sobre a atual...
    assert run.total_falhas == 0
    assert run.status == PipelineRun.Status.CONCLUIDO
    # ...mas continua no log.
    assert PipelineEvent.objects.filter(
        correlation_id=correlacao, stage="extracao.dom2parser", status="falhou"
    ).exists()
    # O que veio antes da extração sobrevive ao reinício.
    assert "captura.recebida" in run.etapas
    assert run.url == "https://exemplo.test/x"


def test_etapa_contavel_conta_itens_distintos_nao_eventos(tenant):
    """O catch-up redespacha tasks: o mesmo fragmento gera vários eventos.

    Contar eventos mostrava "embeddings 15" para nove fragmentos.
    """
    import uuid as _uuid
    correlacao = _uuid.uuid4()
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("fragmentacao.concluida", "ok", correlation_id=correlacao,
         tenant_id=tenant.id, payload={"n": 3})

    for indice in (2, 0, 1, 1, 0, 2, 2):  # fora de ordem e com repetição
        emit("embedding.concluido", "ok", correlation_id=correlacao,
             tenant_id=tenant.id, payload={"indice": indice, "total": 3})

    run = PipelineRun.objects.get(correlation_id=correlacao)
    entrada = run.etapas["embedding.concluido"]
    assert entrada["n"] == 3
    # Ordenado: a projeção não pode depender da ordem de chegada.
    assert entrada["itens"] == [0, 1, 2]
    assert entrada["total"] == 3
    assert run.status == PipelineRun.Status.CONCLUIDO


def test_reconstrucao_reproduz_o_incremental_inclusive_com_avulsos(tenant):
    """A promessa central: reconstruir o log devolve exatamente o mesmo estado.

    Os eventos de infraestrutura têm de ser ignorados dos dois lados — se só o
    caminho incremental os pula, a reconstrução inventa execuções que nunca
    existiram.
    """
    import uuid as _uuid
    correlacao = _uuid.uuid4()

    emit("worker.pronto", "ok")
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id,
         payload={"url": "https://exemplo.test/z"})
    emit("catchup.varredura", "ok", source="beat")
    emit("fragmentacao.concluida", "ok", correlation_id=correlacao,
         tenant_id=tenant.id, payload={"n": 1})
    emit("embedding.concluido", "ok", correlation_id=correlacao, tenant_id=tenant.id,
         payload={"indice": 0, "total": 1})
    emit("worker.encerrando", "ok")

    def retrato():
        return sorted(
            (str(r.correlation_id), r.status, r.total_eventos, str(r.etapas))
            for r in PipelineRun.objects.all()
        )

    antes = retrato()
    assert len(antes) == 1  # só a captura, nenhum fantasma

    from django.core.management import call_command
    from io import StringIO

    call_command("reconstruir_projecoes", stdout=StringIO())

    assert retrato() == antes


def test_projecao_independe_da_ordem_de_aplicacao(tenant):
    """A projeção é função do log, não da ordem em que os eventos chegam.

    Com workers concorrentes a ordem de aplicação diverge da ordem de
    `sequence`; se os campos descritivos viessem do último evento *aplicado*, o
    incremental e a reconstrução mostrariam coisas diferentes.
    """
    import json
    import uuid as _uuid

    from apps.events.models import PipelineEvent as PE

    correlacao = _uuid.uuid4()
    emit("captura.recebida", "ok", correlation_id=correlacao, tenant_id=tenant.id)
    emit("extracao.dom2parser", "falhou", correlation_id=correlacao, tenant_id=tenant.id,
         message="quebrou")
    emit("extracao.dom2parser", "vazio", correlation_id=correlacao, tenant_id=tenant.id,
         message="rodou e não achou nada")

    def retrato():
        r = PipelineRun.objects.get(correlation_id=correlacao)
        return json.dumps(r.etapas, sort_keys=True), r.status, r.total_eventos

    em_ordem = retrato()
    # O estado final da etapa é o do evento de maior sequence.
    etapa_d2p = PipelineRun.objects.get(correlation_id=correlacao).etapas["extracao.dom2parser"]
    assert etapa_d2p["status"] == "vazio"
    assert etapa_d2p["msg"] == "rodou e não achou nada"

    eventos = list(PE.objects.filter(correlation_id=correlacao).order_by("sequence"))
    PipelineRun.objects.filter(correlation_id=correlacao).delete()
    for evento in reversed(eventos):  # ordem de chegada invertida
        aplicar_evento(evento)

    assert retrato() == em_ordem

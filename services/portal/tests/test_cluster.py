"""Registro de máquina, heartbeat (evento → projeção), canal de saída da
replicação (`EventoReplicacao` + endpoint autenticado por X-Machine-Token),
roteador de LLM (`llm_router`) e o gateway compatível com OpenAI.
"""
import hashlib
import json
from unittest import mock

import pytest
from django.core.management import call_command

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact
from apps.cluster.llm_router import escolher_execucao
from apps.cluster.models import EventoReplicacao, Maquina, MaquinaModeloOllama, MaquinaStatus
from apps.cluster.projecao import aplicar_heartbeat, aplicar_metrica_llm
from apps.cluster.replicacao import eventos_para_peer
from apps.cluster.tasks import emitir_heartbeat_maquina
from apps.events.emit import emit

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="Org de Teste", slug="org-teste", org_type="individual", owner=dono)


def _artifact(tenant, **kw):
    content = {"url": "https://exemplo.org/pagina", "mhtml_path": "captura.mhtml", "title": "Página de Teste"}
    content.update(kw.pop("content", {}))
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT,
        content=content,
        tenant=tenant,
        info_type=Artifact.InfoType.FACT,
        **kw,
    )


def test_registrar_maquina_grava_hash_nunca_o_token_em_claro(tenant, capsys):
    call_command("registrar_maquina", apelido="notebook", organizacao=tenant.slug, modo="compute")
    saida = capsys.readouterr().out

    maquina = Maquina.objects.get(apelido="notebook")
    assert maquina.modo == "compute"
    assert maquina.dono_id == tenant.owner_id
    assert str(maquina.id) in saida

    token = [l for l in saida.splitlines() if l.startswith("CLUSTER_MACHINE_TOKEN=")][0].split("=", 1)[1]
    assert maquina.token_hash == hashlib.sha256(token.encode()).hexdigest()


def test_registrar_maquina_organizacao_inexistente_falha(db):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("registrar_maquina", apelido="x", organizacao="nao-existe", modo="compute")


def _heartbeat(maquina, tenant, **payload):
    """emit() sozinho não projeta heartbeat — apenas grava o evento (mesma
    separação de `aplicar_evento`/`PipelineRun`, ver apps/cluster/projecao.py).
    A task real chama os dois passos; o teste reproduz isso explicitamente."""
    evento = emit(
        "maquina.heartbeat", "ok",
        subject_type="maquina", subject_id=maquina.id,
        tenant_id=tenant.id, payload=payload,
    )
    assert evento is not None
    aplicar_heartbeat(evento)
    return evento


def test_heartbeat_atualiza_status_da_maquina(tenant):
    maquina = Maquina.objects.create(
        apelido="notebook", organizacao=tenant, dono=tenant.owner, modo="compute", token_hash="x",
    )
    _heartbeat(maquina, tenant, cpu_percent=12.5, cpu_count=8, ram_disponivel_mb=2048,
               disco_disponivel_gb=50.0, filas=["leve"])

    status = MaquinaStatus.objects.get(maquina=maquina)
    assert status.cpu_percent == 12.5
    assert status.filas == ["leve"]
    assert status.online is True


def test_heartbeat_reconstruivel_do_log(tenant):
    """manage.py reconstruir_status_maquinas deve chegar no mesmo estado que o
    caminho incremental — a prova de que a projeção é derivada do log."""
    maquina = Maquina.objects.create(
        apelido="notebook", organizacao=tenant, dono=tenant.owner, modo="compute", token_hash="x",
    )
    for cpu in (10.0, 20.0, 30.0):
        _heartbeat(maquina, tenant, cpu_percent=cpu)

    incremental = MaquinaStatus.objects.get(maquina=maquina).cpu_percent

    call_command("reconstruir_status_maquinas")
    reconstruido = MaquinaStatus.objects.get(maquina=maquina).cpu_percent

    assert incremental == reconstruido == 30.0


def test_heartbeat_ignora_sequence_mais_antiga(tenant):
    """Um heartbeat atrasado (sequence menor) não pode sobrescrever um mais novo."""
    maquina = Maquina.objects.create(
        apelido="notebook", organizacao=tenant, dono=tenant.owner, modo="compute", token_hash="x",
    )
    novo = _heartbeat(maquina, tenant, cpu_percent=99.0)
    atrasado = emit("maquina.heartbeat", "ok", subject_type="maquina", subject_id=maquina.id,
                     tenant_id=tenant.id, payload={"cpu_percent": 1.0})
    atrasado.sequence = novo.sequence - 1  # simula chegada fora de ordem

    aplicar_heartbeat(atrasado)

    assert MaquinaStatus.objects.get(maquina=maquina).cpu_percent == 99.0


# ─── Autorregistro do nó de infraestrutura (sem `registrar_maquina` manual) ─

def test_no_local_se_autorregistra_no_primeiro_heartbeat(tenant, monkeypatch):
    """Com uma única Organization e CLUSTER_LOCAL_APELIDO definido, a própria
    máquina que hospeda a infra cria sua Maquina sozinha — não deveria
    precisar de `registrar_maquina` contra si mesma."""
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "minha-maquina")
    with mock.patch("apps.artifacts.extractors.ollama_client.listar_modelos", return_value=[]):
        resultado = emitir_heartbeat_maquina()

    assert resultado == "ok"
    maquina = Maquina.objects.get(apelido="minha-maquina")
    assert maquina.organizacao_id == tenant.id
    assert maquina.dono_id == tenant.owner_id
    assert maquina.hospeda_infra_compartilhada is True
    assert MaquinaStatus.objects.get(maquina=maquina).online is True


def test_autorregistro_e_idempotente(tenant, monkeypatch):
    """Rodar o heartbeat várias vezes não pode criar Maquina duplicada —
    importante porque, dentro do Docker, isto roda a cada 30s pra sempre."""
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "minha-maquina")
    with mock.patch("apps.artifacts.extractors.ollama_client.listar_modelos", return_value=[]):
        emitir_heartbeat_maquina()
        emitir_heartbeat_maquina()

    assert Maquina.objects.filter(apelido="minha-maquina").count() == 1


def test_autorregistro_nao_acontece_com_mais_de_uma_organizacao(tenant, monkeypatch):
    """Ambíguo demais adivinhar a dona — exige CLUSTER_MACHINE_ID explícito
    nesse caso, não tenta escolher uma organização sozinho."""
    Organization.objects.create(
        name="Outra Org", slug="outra-org", org_type="individual", owner=tenant.owner,
    )
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "minha-maquina")

    resultado = emitir_heartbeat_maquina()

    assert "não aplicável" in resultado


def test_autorregistro_nao_acontece_sem_hospedar_infra(tenant, settings, monkeypatch):
    """CLUSTER_HOSPEDA_INFRA=false é uma máquina que só contribui
    processamento — não deve tentar virar dona da infra sozinha."""
    settings.CLUSTER_HOSPEDA_INFRA = False
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "minha-maquina")

    resultado = emitir_heartbeat_maquina()

    assert "não aplicável" in resultado
    assert Maquina.objects.count() == 0
    assert Maquina.objects.count() == 0


def test_post_save_de_artifact_gera_evento_de_replicacao(tenant):
    artefato = _artifact(tenant)
    artefato.save()  # o create() acima já dispara — save() explícito prova idempotência do teste

    eventos = EventoReplicacao.objects.filter(objeto_id=artefato.id, tipo="artifact.upsert")
    assert eventos.exists()
    assert eventos.first().organizacao_id == tenant.id


def test_eventos_para_peer_respeita_desde(tenant):
    peer = Maquina.objects.create(
        apelido="replica", organizacao=tenant, dono=tenant.owner, modo="replica", token_hash="x",
    )
    a1 = _artifact(tenant)
    a2 = _artifact(tenant)

    todos = eventos_para_peer(peer, desde=0)
    sequences = [e.sequence for e in todos]
    assert sequences == sorted(sequences)

    ultimo = todos[-1].sequence
    assert eventos_para_peer(peer, desde=ultimo) == []


def test_endpoint_replicacao_recusa_sem_token(client):
    resp = client.get("/cluster/api/v1/replicacao/eventos/?desde=0")
    assert resp.status_code == 401


def test_endpoint_replicacao_recusa_token_invalido(tenant, client):
    Maquina.objects.create(
        apelido="replica", organizacao=tenant, dono=tenant.owner, modo="replica",
        token_hash=hashlib.sha256(b"token-certo").hexdigest(),
    )
    resp = client.get("/cluster/api/v1/replicacao/eventos/?desde=0", HTTP_X_MACHINE_TOKEN="token-errado")
    assert resp.status_code == 401


def test_endpoint_replicacao_devolve_eventos_com_token_valido(tenant, client):
    Maquina.objects.create(
        apelido="replica", organizacao=tenant, dono=tenant.owner, modo="replica",
        token_hash=hashlib.sha256(b"token-certo").hexdigest(),
    )
    artefato = _artifact(tenant)

    resp = client.get("/cluster/api/v1/replicacao/eventos/?desde=0", HTTP_X_MACHINE_TOKEN="token-certo")
    assert resp.status_code == 200
    corpo = resp.json()
    assert any(e["objeto_id"] == str(artefato.id) for e in corpo["eventos"])


# ─── Modelos Ollama por máquina (heartbeat) e aprendizado de velocidade ─────

def _maquina_ollama(tenant, apelido="notebook", endpoint="http://10.0.0.1:11434"):
    m = Maquina.objects.create(
        apelido=apelido, organizacao=tenant, dono=tenant.owner, modo="compute",
        token_hash="x", ollama_endpoint=endpoint,
    )
    _heartbeat(m, tenant, cpu_percent=5.0, cpu_count=8)  # deixa a máquina "online"
    return m


def test_heartbeat_com_modelos_ollama_cria_capacidade(tenant):
    maquina = _maquina_ollama(tenant)
    _heartbeat(maquina, tenant, modelos_ollama=["qwen3.5:9b", "llama3.1:8b"])

    nomes = set(MaquinaModeloOllama.objects.filter(maquina=maquina).values_list("nome_modelo", flat=True))
    assert nomes == {"qwen3.5:9b", "llama3.1:8b"}
    capacidade = MaquinaModeloOllama.objects.get(maquina=maquina, nome_modelo="qwen3.5:9b")
    assert capacidade.tokens_por_segundo_medio is None
    assert capacidade.amostras_n == 0


def test_aplicar_metrica_llm_calcula_media_corrida(tenant):
    maquina = _maquina_ollama(tenant)
    for tokens_por_segundo in (10.0, 20.0, 30.0):
        evento = emit("llm.chamada_ollama", "ok", subject_type="maquina", subject_id=maquina.id,
                       tenant_id=tenant.id,
                       payload={"modelo": "qwen3.5:9b", "tokens_por_segundo": tokens_por_segundo, "num_thread": 8})
        aplicar_metrica_llm(evento)

    capacidade = MaquinaModeloOllama.objects.get(maquina=maquina, nome_modelo="qwen3.5:9b")
    assert capacidade.amostras_n == 3
    assert capacidade.tokens_por_segundo_medio == pytest.approx(20.0)
    assert capacidade.num_thread_observado == 8


def test_reconstruir_status_maquinas_reproduz_media_de_llm(tenant):
    maquina = _maquina_ollama(tenant)
    for tokens_por_segundo in (10.0, 30.0):
        evento = emit("llm.chamada_ollama", "ok", subject_type="maquina", subject_id=maquina.id,
                       tenant_id=tenant.id,
                       payload={"modelo": "qwen3.5:9b", "tokens_por_segundo": tokens_por_segundo})
        aplicar_metrica_llm(evento)

    incremental = MaquinaModeloOllama.objects.get(maquina=maquina, nome_modelo="qwen3.5:9b").tokens_por_segundo_medio

    call_command("reconstruir_status_maquinas")
    reconstruido = MaquinaModeloOllama.objects.get(maquina=maquina, nome_modelo="qwen3.5:9b").tokens_por_segundo_medio

    assert incremental == reconstruido == pytest.approx(20.0)


def test_escolher_execucao_prefere_maquina_nunca_testada(tenant):
    testada = _maquina_ollama(tenant, apelido="testada", endpoint="http://10.0.0.1:11434")
    _heartbeat(testada, tenant, modelos_ollama=["qwen3.5:9b"])
    evento = emit("llm.chamada_ollama", "ok", subject_type="maquina", subject_id=testada.id,
                  tenant_id=tenant.id, payload={"modelo": "qwen3.5:9b", "tokens_por_segundo": 99.0})
    aplicar_metrica_llm(evento)

    nova = _maquina_ollama(tenant, apelido="nova", endpoint="http://10.0.0.2:11434")
    _heartbeat(nova, tenant, modelos_ollama=["qwen3.5:9b"])

    execucao = escolher_execucao("qwen3.5:9b")
    assert execucao.maquina_id == str(nova.id)


def test_escolher_execucao_entre_testadas_prefere_mais_rapida(tenant):
    lenta = _maquina_ollama(tenant, apelido="lenta", endpoint="http://10.0.0.1:11434")
    rapida = _maquina_ollama(tenant, apelido="rapida", endpoint="http://10.0.0.2:11434")
    for maquina, velocidade in ((lenta, 5.0), (rapida, 50.0)):
        _heartbeat(maquina, tenant, modelos_ollama=["qwen3.5:9b"])
        evento = emit("llm.chamada_ollama", "ok", subject_type="maquina", subject_id=maquina.id,
                      tenant_id=tenant.id, payload={"modelo": "qwen3.5:9b", "tokens_por_segundo": velocidade})
        aplicar_metrica_llm(evento)

    execucao = escolher_execucao("qwen3.5:9b")
    assert execucao.maquina_id == str(rapida.id)
    assert execucao.host == "http://10.0.0.2:11434"


def test_escolher_execucao_sem_candidata_devolve_none(tenant):
    assert escolher_execucao("modelo-que-ninguem-tem") is None


# ─── Gateway compatível com OpenAI ──────────────────────────────────────────

def test_gateway_sem_token_configurado_devolve_404(settings, client):
    settings.LLM_GATEWAY_TOKEN = ""
    resp = client.post("/v1/chat/completions", data="{}", content_type="application/json")
    assert resp.status_code == 404


def test_gateway_recusa_sem_bearer_correto(settings, client):
    settings.LLM_GATEWAY_TOKEN = "segredo"
    resp = client.post("/v1/chat/completions", data="{}", content_type="application/json",
                       HTTP_AUTHORIZATION="Bearer errado")
    assert resp.status_code == 401


# ─── Descoberta automática (StatusPublicoView + JoinAPIView) ───────────────

def test_status_publico_reporta_hospeda_infra_por_padrao(client):
    resp = client.get("/cluster/api/v1/status/")
    assert resp.status_code == 200
    assert resp.json()["hospeda_infra_compartilhada"] is True


def test_status_publico_reporta_nao_hospeda_infra_quando_configurado(settings, client):
    settings.CLUSTER_HOSPEDA_INFRA = False
    resp = client.get("/cluster/api/v1/status/")
    assert resp.json()["hospeda_infra_compartilhada"] is False


def test_join_sem_segredo_configurado_devolve_404(settings, client):
    settings.CLUSTER_JOIN_SECRET = ""
    resp = client.post("/cluster/api/v1/join/", data="{}", content_type="application/json")
    assert resp.status_code == 404


def test_join_recusa_segredo_errado(settings, client):
    settings.CLUSTER_JOIN_SECRET = "segredo-certo"
    resp = client.post("/cluster/api/v1/join/", data="{}", content_type="application/json",
                       HTTP_X_CLUSTER_JOIN_SECRET="segredo-errado")
    assert resp.status_code == 401


def test_join_cria_maquina_e_devolve_token(tenant, settings, client):
    settings.CLUSTER_JOIN_SECRET = "segredo-certo"
    corpo = json.dumps({"apelido": "nova-maquina", "organizacao": tenant.slug, "modo": "compute"})
    resp = client.post("/cluster/api/v1/join/", data=corpo, content_type="application/json",
                       HTTP_X_CLUSTER_JOIN_SECRET="segredo-certo")
    assert resp.status_code == 201
    corpo_resp = resp.json()

    maquina = Maquina.objects.get(apelido="nova-maquina")
    assert str(maquina.id) == corpo_resp["machine_id"]
    assert maquina.token_hash == hashlib.sha256(corpo_resp["machine_token"].encode()).hexdigest()


def test_join_organizacao_inexistente_devolve_400(settings, client):
    settings.CLUSTER_JOIN_SECRET = "segredo-certo"
    corpo = json.dumps({"apelido": "x", "organizacao": "nao-existe", "modo": "compute"})
    resp = client.post("/cluster/api/v1/join/", data=corpo, content_type="application/json",
                       HTTP_X_CLUSTER_JOIN_SECRET="segredo-certo")
    assert resp.status_code == 400


def test_gateway_responde_no_formato_openai_sem_cluster(settings, client):
    """Sem nenhuma máquina registrada, cai no Ollama local (mesmo fallback de
    llm_common.gerar_texto) — aqui só testamos o formato da resposta, mockando
    a chamada real ao Ollama."""
    settings.LLM_GATEWAY_TOKEN = "segredo"
    resposta_ollama = {
        "message": {"role": "assistant", "content": "oi"},
        "prompt_eval_count": 10, "eval_count": 3, "eval_duration": 300_000_000,
    }
    with mock.patch("apps.cluster.gateway.gerar_chat", return_value=resposta_ollama) as gerar_chat:
        resp = client.post(
            "/v1/chat/completions",
            data='{"model": "qwen3.5:9b", "messages": [{"role": "user", "content": "oi"}]}',
            content_type="application/json", HTTP_AUTHORIZATION="Bearer segredo",
        )
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["object"] == "chat.completion"
    assert corpo["choices"][0]["message"]["content"] == "oi"
    assert corpo["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    gerar_chat.assert_called_once()

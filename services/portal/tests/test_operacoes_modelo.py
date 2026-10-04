"""Marco D2: instalar e remover modelos do Ollama (local e em pares), cancelamento, espelhos e rotas do canal."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import AuditLog
from apps.cluster import ollama_admin, operacoes, pares, tasks
from apps.cluster.models import Maquina, MaquinaModeloOllama, MaquinaStatus, OperacaoModeloOllama as Op
from apps.cluster.operacoes import Conflito, MaquinaOffline, OperacaoRecusada
from apps.events.models import PipelineEvent
from apps.federacao import canal
from apps.federacao.canal import ParInacessivel, RespostaPar, chamar_par
from apps.federacao.chaves import garantir_chave_ativa
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db

ENDPOINT_LOCAL = "http://10.0.0.5:8000"
OPERACOES = "/federacao/controle/v1/ollama/operacoes/"


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "operacoes-testes"}}
    settings.FEDERACAO_ENDPOINT_ANUNCIADO = ENDPOINT_LOCAL
    settings.OLLAMA_HOST = "http://ollama:11434"
    settings.OLLAMA_RESERVA_DISCO_GB = 10
    from django.core.cache import cache

    cache.clear()


@pytest.fixture(autouse=True)
def sem_broker():
    """Nenhum teste enfileira de verdade."""
    with mock.patch("apps.cluster.tasks.executar_operacao_modelo.apply_async", return_value=SimpleNamespace(id="tarefa-1")) as m:
        yield m


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def admin(org):
    return org.owner


@pytest.fixture
def membro(org):
    u = User.objects.create_user(username="membro", password="x")
    Membership.objects.create(user=u, organization=org, role="member")
    return u


@pytest.fixture
def local(org, monkeypatch):
    garantir_chave_ativa()
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    return pares.garantir_maquina_local(org)


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def _par(org, remota, **kw):
    base = dict(organizacao=org, dono=org.owner, apelido="notebook-b", did=remota.did, tipo="proprio", estado="confirmado",
                endpoint_controle="http://10.0.0.9:8000")
    base.update(kw)
    par = Maquina.objects.create(**base)
    MaquinaStatus.objects.create(maquina=par, ultimo_heartbeat_em=timezone.now())      # online
    return par


def _op(maquina, modelo="qwen3.5:9b", tipo="pull", status="pendente", **kw):
    return Op.objects.create(maquina=maquina, tipo=tipo, modelo=modelo, status=status, **kw)


# ═══ solicitar (máquina local) ══════════════════════════════════════════════

def test_solicitar_local_cria_a_operacao_enfileira_e_audita(org, admin, local, sem_broker):
    op = operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    assert (op.status, op.tipo, op.modelo, op.maquina_id, op.solicitado_por_id) == ("pendente", "pull", "qwen3.5:9b", local.id, admin.id)
    assert op.ator_afirmado == {"user": str(admin.id), "papel": "owner", "org": str(org.id)}
    op.refresh_from_db()
    assert op.celery_task_id == "tarefa-1"
    assert sem_broker.call_args.kwargs == {"args": [str(op.id)], "queue": "ollama_admin"}
    assert AuditLog.objects.filter(operation="modelo_ollama.pull_solicitado", user=admin, organization=org).exists()


def test_so_dono_e_administrador_solicitam(org, membro, local):
    with pytest.raises(PermissionDenied):
        operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=membro)
    assert not Op.objects.exists()


@pytest.mark.parametrize("tipo,modelo", [("apagar", "qwen3.5:9b"), ("pull", "../etc/passwd"), ("pull", "Nome Ruim"),
                                         ("pull", "registry.exemplo.com/x/y:1"), ("delete", "")])
def test_pedido_invalido(local, admin, tipo, modelo):
    with pytest.raises(OperacaoRecusada):
        operacoes.solicitar(local, tipo, modelo, usuario=admin)
    assert not Op.objects.exists()


def test_sem_ollama_configurado_ou_fora_do_ar(local, admin, settings):
    settings.OLLAMA_HOST = ""
    with pytest.raises(OperacaoRecusada) as erro:
        operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    assert erro.value.http == 409
    settings.OLLAMA_HOST = "http://ollama:11434"
    MaquinaStatus.objects.update_or_create(maquina=local, defaults={"ollama_disponivel": False})
    with pytest.raises(MaquinaOffline):
        operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)


def test_nao_ha_duas_operacoes_ativas_para_o_mesmo_modelo(local, admin):
    operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    for tipo in ("pull", "delete"):                              # nem outro pull, nem remover durante o pull
        with pytest.raises(Conflito):
            operacoes.solicitar(local, tipo, "qwen3.5:9b", usuario=admin)
    operacoes.solicitar(local, "pull", "llama3.1:8b", usuario=admin)         # outro modelo, tudo bem
    assert Op.objects.count() == 2


def test_terminada_a_operacao_pode_se_pedir_de_novo(local, admin):
    op = operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    Op.objects.filter(pk=op.pk).update(status="falhou")
    assert operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin).pk != op.pk


def test_ha_um_teto_de_operacoes_ativas_por_maquina(local, admin):
    for n in range(operacoes.LIMITE_ATIVAS_POR_MAQUINA):
        operacoes.solicitar(local, "pull", f"modelo{n}:1", usuario=admin)
    with pytest.raises(Conflito, match="operações em andamento"):
        operacoes.solicitar(local, "pull", "um-a-mais:1", usuario=admin)


def test_pouco_espaco_recusa_mas_espaco_desconhecido_nao_bloqueia(local, admin):
    MaquinaStatus.objects.update_or_create(maquina=local, defaults={"disco_ollama_livre_gb": 5.0})
    with pytest.raises(OperacaoRecusada) as erro:
        operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    assert erro.value.http == 507
    operacoes.solicitar(local, "delete", "qwen3.5:9b", usuario=admin)           # remover libera espaço: nunca bloqueia
    MaquinaStatus.objects.filter(maquina=local).update(disco_ollama_livre_gb=None)
    operacoes.solicitar(local, "pull", "llama3.1:8b", usuario=admin)


def test_falha_ao_enfileirar_falha_na_hora_e_deixa_a_operacao_fechada(local, admin, sem_broker):
    sem_broker.side_effect = RuntimeError("redis fora")
    with pytest.raises(OperacaoRecusada) as erro:
        operacoes.solicitar(local, "pull", "qwen3.5:9b", usuario=admin)
    assert erro.value.http == 503
    op = Op.objects.get()
    assert op.status == "falhou" and "enfileirar" in op.erro and not op.ativa


# ═══ executar ═══════════════════════════════════════════════════════════════

def _pull(*eventos):
    def gerador(nome, host=None):
        yield from eventos
    return gerador


@pytest.fixture
def instantaneo(monkeypatch):
    monkeypatch.setattr(operacoes, "PROGRESSO_INTERVALO_S", 0)


def test_pull_agrega_as_camadas_grava_o_progresso_e_conclui(local, instantaneo):
    op = _op(local)
    eventos = [{"status": "pulling manifest"},
               {"status": "pulling a", "digest": "a", "total": 600, "completed": 100},
               {"status": "pulling b", "digest": "b", "total": 400, "completed": 0},
               {"status": "pulling a", "digest": "a", "total": 600, "completed": 600},
               {"status": "pulling b", "digest": "b", "total": 400, "completed": 400}, {"status": "success"}]
    visto = []
    original = operacoes._finalizar

    def espiar(o, status, erro=""):
        visto.append(Op.objects.get(pk=o.pk))
        original(o, status, erro)

    with mock.patch.object(ollama_admin, "pull_stream", _pull(*eventos)), mock.patch.object(operacoes, "_finalizar", espiar), \
            mock.patch.object(operacoes, "_atualizar_inventario_local") as inventario:
        assert operacoes.executar(op.pk) == "concluida"
    op.refresh_from_db()
    assert op.status == "concluida" and op.bytes_total == 1000 and op.bytes_concluidos == 1000 and op.percentual == 100
    assert op.iniciada_em and op.finalizada_em and op.fase == "concluída" and op.erro == ""
    assert visto[0].bytes_total == 1000                           # somou as duas camadas antes de fechar
    inventario.assert_called_once()                               # a tela vê o resultado sem esperar o heartbeat
    assert [e.status for e in PipelineEvent.objects.filter(stage="cluster.modelo").order_by("sequence")] == ["iniciado", "ok"]


def test_o_progresso_e_limitado_a_um_envio_por_intervalo(local):
    op = _op(local)
    eventos = [{"status": "x", "digest": "a", "total": 1000, "completed": n * 10} for n in range(1, 50)] + [{"status": "success"}]
    with mock.patch.object(ollama_admin, "pull_stream", _pull(*eventos)), mock.patch.object(operacoes, "_atualizar_inventario_local"), \
            mock.patch.object(Op.objects, "filter", wraps=Op.objects.filter) as filtro:
        operacoes.executar(op.pk)
    atualizacoes = [c for c in filtro.call_args_list if c.kwargs.get("status") == "executando"]
    assert len(atualizacoes) <= 3                                 # 49 eventos em menos de 1,5 s → quase nenhuma escrita


@pytest.mark.parametrize("excecao,trecho", [
    (ollama_admin.ModeloNaoEncontrado("x"), "não conhece este modelo"),
    (ollama_admin.DiscoCheio("sem espaço"), "Espaço insuficiente"),
    (ollama_admin.OllamaIndisponivel("caiu"), "tente instalar de novo"),
    (ollama_admin.ErroOllama("erro estranho"), "erro estranho"),
    (RuntimeError("bug"), "Erro inesperado"),
])
def test_cada_falha_vira_uma_mensagem_util(local, excecao, trecho):
    op = _op(local)
    with mock.patch.object(ollama_admin, "pull_stream", side_effect=excecao), mock.patch.object(operacoes, "_atualizar_inventario_local"):
        assert operacoes.executar(op.pk) == "falhou"
    op.refresh_from_db()
    assert op.status == "falhou" and trecho in op.erro and op.finalizada_em
    assert PipelineEvent.objects.filter(stage="cluster.modelo", status="falhou").exists()


def test_remover_funciona_e_modelo_ausente_vira_mensagem_clara(local):
    op = _op(local, tipo="delete")
    with mock.patch.object(ollama_admin, "delete") as rm, mock.patch.object(operacoes, "_atualizar_inventario_local"):
        assert operacoes.executar(op.pk) == "concluida"
    rm.assert_called_once_with("qwen3.5:9b")
    outra = _op(local, "outro:1", "delete")
    with mock.patch.object(ollama_admin, "delete", side_effect=ollama_admin.ModeloNaoEncontrado("x")), \
            mock.patch.object(operacoes, "_atualizar_inventario_local"):
        operacoes.executar(outra.pk)
    outra.refresh_from_db()
    assert outra.status == "falhou" and "não está instalado" in outra.erro


def test_so_executa_o_que_esta_pendente_e_e_local(local, org, remota):
    for status in ("executando", "concluida", "falhou", "cancelada"):
        op = _op(local, f"m-{status}:1", status=status)
        assert operacoes.executar(op.pk) == "ignorada"
    assert operacoes.executar(uuid.uuid4()) == "ignorada"
    espelho = _op(_par(org, remota), operacao_remota_id=uuid.uuid4())
    assert operacoes.executar(espelho.pk) == "nao_local"
    espelho.refresh_from_db()
    assert espelho.status == "pendente"


def test_cancelamento_no_meio_do_pull_para_o_download_e_nao_e_sobrescrito(local, instantaneo):
    op = _op(local)
    fechado = []

    def gerador(nome, host=None):
        try:
            yield {"status": "a", "digest": "d", "total": 100, "completed": 10}
            Op.objects.filter(pk=op.pk).update(status="cancelada")        # alguém cancelou
            yield {"status": "b", "digest": "d", "total": 100, "completed": 20}
            yield {"status": "success"}
        finally:
            fechado.append(True)

    with mock.patch.object(ollama_admin, "pull_stream", gerador), mock.patch.object(operacoes, "_atualizar_inventario_local"):
        assert operacoes.executar(op.pk) == "cancelada"
    op.refresh_from_db()
    assert op.status == "cancelada" and fechado == [True]


def test_disco_insuficiente_aborta_assim_que_o_tamanho_aparece(local, instantaneo):
    op = _op(local)
    with mock.patch.object(ollama_admin, "pull_stream", _pull({"status": "a", "digest": "d", "total": 50_000_000_000, "completed": 0})), \
            mock.patch("apps.cluster.tasks._disco_ollama_livre_gb", return_value=20.0), mock.patch.object(operacoes, "_atualizar_inventario_local"):
        assert operacoes.executar(op.pk) == "falhou"
    op.refresh_from_db()
    assert "Espaço insuficiente" in op.erro


def test_disco_desconhecido_nao_bloqueia_o_pull(local, instantaneo):
    op = _op(local)
    with mock.patch.object(ollama_admin, "pull_stream", _pull({"status": "a", "digest": "d", "total": 50_000_000_000, "completed": 1},
                                                              {"status": "success"})), \
            mock.patch("apps.cluster.tasks._disco_ollama_livre_gb", return_value=None), mock.patch.object(operacoes, "_atualizar_inventario_local"):
        assert operacoes.executar(op.pk) == "concluida"


def test_finalizar_nao_reabre_uma_operacao_cancelada(local):
    op = _op(local, status="cancelada")
    operacoes._finalizar(op, "concluida")
    op.refresh_from_db()
    assert op.status == "cancelada"


def test_a_task_chama_o_servico(local):
    op = _op(local)
    with mock.patch.object(ollama_admin, "delete"), mock.patch.object(operacoes, "_atualizar_inventario_local"):
        op.tipo = "delete"
        op.save()
        assert tasks.executar_operacao_modelo(str(op.pk)) == "concluida"


# ═══ cancelar ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status,terminar", [("pendente", False), ("executando", True)])
def test_cancelar_local_marca_e_revoga_a_task(local, admin, status, terminar):
    op = _op(local, status=status, celery_task_id="tarefa-9")
    with mock.patch("celery.current_app.control.revoke") as revoke:
        operacoes.cancelar(op, usuario=admin)
    op.refresh_from_db()
    assert op.status == "cancelada" and op.finalizada_em
    revoke.assert_called_once_with("tarefa-9", terminate=terminar, signal="SIGKILL")
    assert AuditLog.objects.filter(operation="modelo_ollama.cancelado").exists()


def test_cancelar_e_idempotente_e_exige_papel(local, admin, membro):
    op = _op(local, status="concluida")
    with mock.patch("celery.current_app.control.revoke") as revoke:
        assert operacoes.cancelar(op, usuario=admin).status == "concluida"
    revoke.assert_not_called()
    ativa = _op(local, "outro:1", celery_task_id="t")
    with pytest.raises(PermissionDenied):
        operacoes.cancelar(ativa, usuario=membro)
    ativa.refresh_from_db()
    assert ativa.status == "pendente"


# ═══ pares: falha na hora e espelho ═════════════════════════════════════════

def _resposta(status=200, json_=None, assinada=True):
    return RespostaPar(status, json_ if json_ is not None else {}, assinada)


def test_par_offline_falha_na_hora_sem_chamar_ninguem_e_sem_criar_nada(org, admin, remota):
    par = _par(org, remota)
    MaquinaStatus.objects.filter(maquina=par).update(ultimo_heartbeat_em=timezone.now() - timedelta(minutes=10))
    par = Maquina.objects.get(pk=par.pk)                          # a tela/rota sempre lê o par do banco
    with mock.patch.object(operacoes, "chamar_par", side_effect=AssertionError("não deveria chamar")):
        with pytest.raises(MaquinaOffline, match="offline"):
            operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    assert not Op.objects.exists()


def test_par_que_nunca_foi_visto_tambem_esta_offline(org, admin, remota):
    par = Maquina.objects.create(organizacao=org, dono=admin, apelido="x", did=remota.did, tipo="proprio", estado="confirmado",
                                 endpoint_controle="http://10.0.0.9:8000")
    with pytest.raises(MaquinaOffline):
        operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)


@pytest.mark.parametrize("mudanca", [dict(tipo="terceiro"), dict(estado="pendente"), dict(estado="revogado"), dict(ativa=False)])
def test_so_se_controla_par_proprio_e_confirmado(org, admin, remota, mudanca):
    par = _par(org, remota, **mudanca)
    with pytest.raises(OperacaoRecusada) as erro:
        operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    assert erro.value.http == 403


def test_membro_nao_comanda_par(org, membro, remota):
    with pytest.raises(PermissionDenied):
        operacoes.solicitar(_par(org, remota), "pull", "qwen3.5:9b", usuario=membro)


def test_par_online_recebe_o_comando_com_o_ator_e_gera_um_espelho(org, admin, remota):
    par = _par(org, remota)
    remoto = uuid.uuid4()
    with mock.patch.object(operacoes, "chamar_par", return_value=_resposta(200, {"id": str(remoto), "status": "pendente"})) as chamar:
        op = operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    args, kwargs = chamar.call_args
    assert args == (par, "POST", OPERACOES, {"tipo": "pull", "modelo": "qwen3.5:9b"})
    assert kwargs["ator"] == {"user": str(admin.id), "papel": "owner", "org": str(org.id)}
    assert (op.maquina_id, op.operacao_remota_id, op.status, op.solicitado_por_id) == (par.id, remoto, "pendente", admin.id)
    assert not op.maquina.eh_local


@pytest.mark.parametrize("resposta,excecao,http", [
    (_resposta(409, {"erro": "já existe"}), Conflito, 409),
    (_resposta(403, {"erro": "x"}, assinada=False), OperacaoRecusada, 403),
    (_resposta(404, {}, assinada=False), OperacaoRecusada, 403),
    (_resposta(400, {"erro": "nome inválido"}, assinada=False), OperacaoRecusada, 400),
    (_resposta(507, {"erro": "pouco espaço"}, assinada=False), OperacaoRecusada, 507),
    (_resposta(500, {}, assinada=False), MaquinaOffline, 503),
    (_resposta(200, {"id": "x"}, assinada=False), MaquinaOffline, 503),            # 200 sem assinatura: não é confiável
    (_resposta(200, {"id": "nao-e-uuid"}), OperacaoRecusada, 502),
])
def test_respostas_do_par_viram_erros_claros_e_nunca_criam_espelho(org, admin, remota, resposta, excecao, http):
    par = _par(org, remota)
    with mock.patch.object(operacoes, "chamar_par", return_value=resposta):
        with pytest.raises(excecao) as erro:
            operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    assert erro.value.http == http and not Op.objects.exists()


def test_par_que_nao_responde_falha_na_hora(org, admin, remota):
    par = _par(org, remota)
    with mock.patch.object(operacoes, "chamar_par", side_effect=ParInacessivel("timeout")):
        with pytest.raises(MaquinaOffline):
            operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    assert not Op.objects.exists()


def test_espelho_duplicado_e_recusado_antes_de_chamar_o_par(org, admin, remota):
    par = _par(org, remota)
    _op(par, operacao_remota_id=uuid.uuid4())
    with mock.patch.object(operacoes, "chamar_par", side_effect=AssertionError("não deveria chamar")):
        with pytest.raises(Conflito):
            operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)


def test_cancelar_no_par_exige_par_online_e_cancela_o_espelho(org, admin, remota):
    par = _par(org, remota)
    op = _op(par, status="executando", operacao_remota_id=uuid.uuid4())
    with mock.patch.object(operacoes, "chamar_par", return_value=_resposta(200, {})) as chamar:
        operacoes.cancelar(op, usuario=admin)
    assert chamar.call_args.args[:3] == (par, "POST", f"{OPERACOES}{op.operacao_remota_id}/cancelar/")
    op.refresh_from_db()
    assert op.status == "cancelada"
    MaquinaStatus.objects.filter(maquina=par).update(ultimo_heartbeat_em=timezone.now() - timedelta(minutes=10))
    par = Maquina.objects.get(pk=par.pk)
    outro = _op(par, "m2:1", operacao_remota_id=uuid.uuid4())
    with pytest.raises(MaquinaOffline):
        operacoes.cancelar(outro, usuario=admin)
    outro.refresh_from_db()
    assert outro.status == "pendente"                             # offline: nada muda


def test_cancelamento_recusado_pelo_par_nao_marca_o_espelho(org, admin, remota):
    par = _par(org, remota)
    op = _op(par, status="executando", operacao_remota_id=uuid.uuid4())
    with mock.patch.object(operacoes, "chamar_par", return_value=_resposta(403, {}, assinada=False)):
        with pytest.raises(OperacaoRecusada):
            operacoes.cancelar(op, usuario=admin)
    op.refresh_from_db()
    assert op.status == "executando"


# ═══ acompanhar espelhos e varrer travadas ══════════════════════════════════

def test_o_espelho_acompanha_o_progresso_e_o_fim_da_operacao_no_par(org, remota):
    par = _par(org, remota)
    op = _op(par, status="pendente", operacao_remota_id=uuid.uuid4())
    chamar = mock.Mock(return_value=_resposta(200, {"status": "executando", "bytes_total": 1000, "bytes_concluidos": 400, "fase": "pulling x"}))
    assert operacoes.acompanhar_espelhos(chamar=chamar)["consultadas"] == 1
    op.refresh_from_db()
    assert (op.status, op.bytes_total, op.bytes_concluidos, op.fase, op.percentual) == ("executando", 1000, 400, "pulling x", 40)
    chamar.return_value = _resposta(200, {"status": "concluida", "bytes_total": 1000, "bytes_concluidos": 1000})
    operacoes.acompanhar_espelhos(chamar=chamar)
    op.refresh_from_db()
    assert op.status == "concluida" and op.finalizada_em and not op.ativa
    assert PipelineEvent.objects.filter(stage="cluster.modelo", status="ok").exists()
    assert chamar.call_args.args[:3] == (par, "GET", f"{OPERACOES}{op.operacao_remota_id}/")


def test_o_espelho_ignora_o_que_o_par_diz_de_errado(org, remota):
    par = _par(org, remota)
    op = _op(par, status="pendente", operacao_remota_id=uuid.uuid4())
    for lixo in ({"status": "inventado"}, {"status": "executando", "bytes_total": -5, "bytes_concluidos": "muito"}):
        operacoes.acompanhar_espelhos(chamar=lambda *a, **k: _resposta(200, lixo))
    op.refresh_from_db()
    assert op.status in ("pendente", "executando") and op.bytes_total is None and op.bytes_concluidos == 0
    operacoes.acompanhar_espelhos(chamar=lambda *a, **k: _resposta(200, {"status": "concluida"}, assinada=False))
    op.refresh_from_db()
    assert op.ativa                                               # sem assinatura, não vale


def test_operacao_que_o_par_nao_conhece_mais_falha(org, remota):
    op = _op(_par(org, remota), operacao_remota_id=uuid.uuid4())
    operacoes.acompanhar_espelhos(chamar=lambda *a, **k: _resposta(404, {}, assinada=False))
    op.refresh_from_db()
    assert op.status == "falhou" and "não existe mais" in op.erro


def test_par_calado_por_muito_tempo_derruba_o_espelho_mas_um_instante_nao(org, remota):
    par = _par(org, remota)
    op = _op(par, operacao_remota_id=uuid.uuid4(), ultimo_progresso_em=timezone.now())
    inacessivel = mock.Mock(side_effect=ParInacessivel("x"))
    assert operacoes.acompanhar_espelhos(chamar=inacessivel)["sem_noticias"] == 0
    op.refresh_from_db()
    assert op.ativa
    resumo = operacoes.acompanhar_espelhos(agora=timezone.now() + timedelta(minutes=11), chamar=inacessivel)
    op.refresh_from_db()
    assert resumo["sem_noticias"] == 1 and op.status == "falhou" and "Sem notícias" in op.erro


def test_so_os_espelhos_sao_acompanhados(org, remota, local):
    _op(local)                                                    # local: não é espelho
    _op(_par(org, remota), "m2:1", status="concluida", operacao_remota_id=uuid.uuid4())   # já terminada
    chamar = mock.Mock()
    assert operacoes.acompanhar_espelhos(chamar=chamar)["consultadas"] == 0
    chamar.assert_not_called()


def test_varrer_travadas_fecha_so_o_que_ninguem_executa(org, remota, local):
    agora = timezone.now()
    velha_pendente = _op(local, "a:1")
    Op.objects.filter(pk=velha_pendente.pk).update(criada_em=agora - timedelta(minutes=11))
    nova_pendente = _op(local, "b:1")
    parada = _op(local, "c:1", status="executando", ultimo_progresso_em=agora - timedelta(minutes=6))
    andando = _op(local, "d:1", status="executando", ultimo_progresso_em=agora - timedelta(seconds=30))
    espelho = _op(_par(org, remota), "e:1", operacao_remota_id=uuid.uuid4())
    Op.objects.filter(pk=espelho.pk).update(criada_em=agora - timedelta(hours=2))
    assert operacoes.varrer_travadas(agora=agora) == 2
    status = {o.modelo: o.status for o in Op.objects.all()}
    assert status == {"a:1": "falhou", "b:1": "pendente", "c:1": "falhou", "d:1": "executando", "e:1": "pendente"}
    assert "ollama_admin" in Op.objects.get(pk=velha_pendente.pk).erro and nova_pendente.pk and parada.pk and andando.pk


def test_a_task_de_acompanhamento_sai_cedo_quando_nada_esta_ativo(local):
    assert tasks.acompanhar_operacoes() is None
    _op(local)
    assert tasks.acompanhar_operacoes() == {"travadas": 0, "consultadas": 0, "sem_noticias": 0}


def test_avisos_de_remocao(local, monkeypatch):
    MaquinaModeloOllama.objects.create(maquina=local, nome_modelo="qwen3.5:9b", carregado=True)
    monkeypatch.setenv("OLLAMA_MODELOS", "qwen3.5:9b llama3.1:8b")
    avisos = operacoes.avisos_remocao(local, "qwen3.5:9b")
    assert any("carregado" in a for a in avisos) and any("OLLAMA_MODELOS" in a for a in avisos)
    assert operacoes.avisos_remocao(local, "outro:1") == []


def test_serializar_nao_expoe_enderecos_nem_o_ator(local):
    op = _op(local, bytes_total=200, bytes_concluidos=50, erro="x" * 1000, ator_afirmado={"user": "u"}, celery_task_id="t")
    dados = operacoes.serializar(op)
    assert dados["percentual"] == 25 and len(dados["erro"]) == 300
    assert set(dados) == {"id", "tipo", "modelo", "status", "bytes_total", "bytes_concluidos", "percentual", "fase", "erro",
                          "criada_em", "finalizada_em"}


# ═══ rotas do canal (lado que recebe o comando) ═════════════════════════════

def _chamar(remota, local, metodo, caminho, corpo=None, client=None):
    bruto = json.dumps(corpo).encode() if corpo is not None else b""
    headers = canal.assinar_requisicao(metodo, caminho, bruto, local.did, chave=remota)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items()}
    return (client or Client()).generic(metodo, caminho, data=bruto, content_type="application/json", **extra), headers


ATOR = {"user": "u-1", "papel": "admin", "org": "o-1"}


def test_par_proprio_com_ator_administrador_instala_um_modelo_aqui(org, local, remota):
    par = _par(org, remota)
    resp, headers = _chamar(remota, local, "POST", OPERACOES, {"tipo": "pull", "modelo": "qwen3.5:9b", "ator": ATOR})
    assert resp.status_code == 200
    canal.verificar_resposta(resp.content, headers[canal.H_NONCE], local.did, resp[canal.H_ASSINATURA_RESPOSTA])
    dados = json.loads(resp.content)
    op = Op.objects.get(pk=dados["id"])
    assert (op.maquina_id, op.origem_par_id, op.tipo, op.modelo, op.status) == (local.id, par.id, "pull", "qwen3.5:9b", "pendente")
    assert op.ator_afirmado == {"user": "u-1", "papel": "admin", "org": "o-1"} and op.solicitado_por_id is None
    assert "ator" not in dados and "celery_task_id" not in dados
    log = AuditLog.objects.get(operation="modelo_ollama.pull_solicitado")
    assert log.metadata["origem_par"] == str(par.id) and log.metadata["ator"]["papel"] == "admin"


@pytest.mark.parametrize("ator", [None, "x", {}, {"user": "u", "papel": "member"}, {"user": "u", "papel": "guest"},
                                  {"user": "", "papel": "admin"}, {"papel": "admin"}, {"user": "u" * 100, "papel": "owner"},
                                  {"user": 5, "papel": "admin"}])
def test_sem_ator_administrador_afirmado_nao_ha_comando(org, local, remota, ator):
    _par(org, remota)
    corpo = {"tipo": "pull", "modelo": "qwen3.5:9b"}
    if ator is not None:
        corpo["ator"] = ator
    resp, _ = _chamar(remota, local, "POST", OPERACOES, corpo)
    assert resp.status_code == 403 and not Op.objects.exists()


def test_par_terceiro_nao_comanda_mesmo_com_ator_administrador(org, local, remota):
    _par(org, remota, tipo="terceiro")
    resp, _ = _chamar(remota, local, "POST", OPERACOES, {"tipo": "pull", "modelo": "qwen3.5:9b", "ator": ATOR})
    assert resp.status_code == 403 and not Op.objects.exists()


@pytest.mark.parametrize("corpo,status", [
    ({"tipo": "pull", "modelo": "../x"}, 400), ({"tipo": "apagar", "modelo": "qwen3.5:9b"}, 400), ({"modelo": "qwen3.5:9b"}, 400),
    ({"tipo": "pull", "modelo": "registry.exemplo.com/a/b:1"}, 400), ({"tipo": "pull"}, 400),
])
def test_pedido_ruim_vira_erro_json_do_servico(org, local, remota, corpo, status):
    _par(org, remota)
    resp, _ = _chamar(remota, local, "POST", OPERACOES, {**corpo, "ator": ATOR})
    assert resp.status_code == status and set(json.loads(resp.content)) == {"erro"} and not Op.objects.exists()


def test_conflito_e_ollama_ausente_viram_409(org, local, remota, settings):
    _par(org, remota)
    corpo = {"tipo": "pull", "modelo": "qwen3.5:9b", "ator": ATOR}
    assert _chamar(remota, local, "POST", OPERACOES, corpo)[0].status_code == 200
    assert _chamar(remota, local, "POST", OPERACOES, corpo)[0].status_code == 409
    settings.OLLAMA_HOST = ""
    assert _chamar(remota, local, "POST", OPERACOES, {**corpo, "modelo": "outro:1"})[0].status_code == 409


def test_instancia_sem_maquina_local_recusa_com_409(org, remota):
    garantir_chave_ativa()
    _par(org, remota)
    local_did = garantir_chave_ativa()[0]
    resp, _ = _chamar(remota, local_did, "POST", OPERACOES, {"tipo": "pull", "modelo": "qwen3.5:9b", "ator": ATOR})
    assert resp.status_code == 409


def test_o_par_so_consulta_e_cancela_o_que_ele_mesmo_pediu(org, local, remota):
    dono = _par(org, remota)
    outro_par = ChaveOutraInstancia()
    _par(org, outro_par, apelido="outro-par")
    minha = _op(local, origem_par=dono, celery_task_id="t")
    alheia = _op(local, "alheia:1", origem_par=None)
    url = f"{OPERACOES}{minha.id}/"
    resp, _ = _chamar(remota, local, "GET", url)
    assert resp.status_code == 200 and json.loads(resp.content)["id"] == str(minha.id)
    assert _chamar(outro_par, local, "GET", url)[0].status_code == 404                   # de outro par
    assert _chamar(remota, local, "GET", f"{OPERACOES}{alheia.id}/")[0].status_code == 404   # pedida por um usuário daqui
    assert _chamar(remota, local, "GET", f"{OPERACOES}{uuid.uuid4()}/")[0].status_code == 404
    assert _chamar(remota, local, "POST", url + "cancelar/", {})[0].status_code == 403       # sem ator administrador
    with mock.patch("celery.current_app.control.revoke"):
        assert _chamar(remota, local, "POST", url + "cancelar/", {"ator": ATOR})[0].status_code == 200
        assert _chamar(outro_par, local, "POST", url + "cancelar/", {"ator": ATOR})[0].status_code == 404
    minha.refresh_from_db()
    assert minha.status == "cancelada"


def test_ida_e_volta_completa_do_comando_pelo_canal(org, admin, local, remota, monkeypatch):
    """O cliente (`solicitar` num par) fala com o servidor de verdade (as rotas acima), só sem rede no meio."""
    par = _par(org, remota)
    cliente = Client()

    def ponte(metodo, url, corpo, headers, timeout):
        caminho = url[len(ENDPOINT_LOCAL):]
        extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in headers.items() if k != "Content-Type"}
        resp = cliente.generic(metodo, caminho, data=corpo, content_type="application/json", **extra)
        return resp.status_code, resp.content, resp.headers

    alvo = SimpleNamespace(did=local.did, endpoint_controle=ENDPOINT_LOCAL)
    monkeypatch.setattr(operacoes, "chamar_par",
                        lambda p, metodo, caminho, corpo=None, **kw: chamar_par(alvo, metodo, caminho, corpo, chave=remota, transporte=ponte, **kw))
    espelho = operacoes.solicitar(par, "pull", "qwen3.5:9b", usuario=admin)
    real = Op.objects.get(maquina=local)
    assert espelho.operacao_remota_id == real.id and real.origem_par_id == par.id and espelho.maquina_id == par.id
    # o progresso volta por pull
    Op.objects.filter(pk=real.pk).update(status="executando", bytes_total=1000, bytes_concluidos=300, fase="pulling")
    operacoes.acompanhar_espelhos(chamar=operacoes.chamar_par)       # o `chamar_par` ligado à ponte acima
    espelho.refresh_from_db()
    assert (espelho.status, espelho.bytes_concluidos, espelho.percentual) == ("executando", 300, 30)
    with mock.patch("celery.current_app.control.revoke"):
        operacoes.cancelar(espelho, usuario=admin)
    real.refresh_from_db()
    espelho.refresh_from_db()
    assert real.status == "cancelada" and espelho.status == "cancelada"

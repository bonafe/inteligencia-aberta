"""Marco C: inventário do Ollama, estado vindo de um par, pull periódico e o que o roteador passa a enxergar."""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from apps.accounts.models import Membership, Organization, User
from apps.cluster import ollama_admin, pares, pull, tasks
from apps.cluster.llm_router import escolher_execucao
from apps.cluster.models import Maquina, MaquinaModeloOllama, MaquinaStatus
from apps.cluster.projecao import aplicar_estado_remoto, aplicar_inventario, sanear_modelos
from apps.events.models import PipelineEvent
from apps.federacao.canal import ParInacessivel, RespostaInvalida, RespostaPar
from apps.federacao.chaves import garantir_chave_ativa
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


def _par(org, nome="par", **kw):
    base = dict(organizacao=org, dono=org.owner, apelido=nome, did=ChaveOutraInstancia().did, tipo="proprio",
                estado="confirmado", endpoint_controle="http://10.0.0.9:8000")
    base.update(kw)
    return Maquina.objects.create(**base)


def _modelo(nome, **kw):
    return {"nome": nome, "tamanho_bytes": 100, "digest": "d", "familia": "f", "parametros": "9B", "quantizacao": "Q4",
            "carregado": False, **kw}


# ── saneamento do que vem de fora ───────────────────────────────────────────

def test_sanear_modelos_limita_tipos_e_tamanhos():
    bruto = [
        {"nome": "ok:1", "tamanho_bytes": 5, "carregado": True, "familia": "x" * 500},
        {"nome": "  espaco  ", "tamanho_bytes": -1}, {"nome": "", "tamanho_bytes": 1}, {"nome": None}, "texto", 7, None,
        {"nome": "bool", "tamanho_bytes": True, "carregado": "true"}, {"nome": "grande", "tamanho_bytes": 2 ** 70},
        {"nome": "ctrl\x00\x07rl"}, {"nome": {"a": 1}},
    ]
    saida = {m["nome"]: m for m in sanear_modelos(bruto)}
    assert saida["ok:1"]["tamanho_bytes"] == 5 and saida["ok:1"]["carregado"] is True and len(saida["ok:1"]["familia"]) == 60
    assert saida["espaco"]["tamanho_bytes"] is None                 # negativo
    assert saida["bool"]["tamanho_bytes"] is None and saida["bool"]["carregado"] is False
    assert saida["grande"]["tamanho_bytes"] is None
    assert "ctrlrl" in saida and set(saida) == {"ok:1", "espaco", "bool", "grande", "ctrlrl"}


def test_sanear_modelos_nao_aceita_lista_gigante_nem_nao_lista():
    assert len(sanear_modelos([{"nome": f"m{i}"} for i in range(5000)])) == 500
    assert sanear_modelos("nao-e-lista") == [] and sanear_modelos(None) == []


# ── inventário: snapshot ────────────────────────────────────────────────────

def test_inventario_e_um_snapshot_e_preserva_a_velocidade_aprendida(org):
    m = _par(org)
    MaquinaModeloOllama.objects.create(maquina=m, nome_modelo="velho:1", tokens_por_segundo_medio=10.0, amostras_n=3)
    MaquinaModeloOllama.objects.create(maquina=m, nome_modelo="fica:1", tokens_por_segundo_medio=25.0, amostras_n=7)
    aplicar_inventario(m, [_modelo("fica:1", tamanho_bytes=900, carregado=True), _modelo("novo:2")], versao="0.35.1", disco_ollama_livre_gb=42.5)
    nomes = set(MaquinaModeloOllama.objects.filter(maquina=m).values_list("nome_modelo", flat=True))
    assert nomes == {"fica:1", "novo:2"}                               # o que sumiu saiu
    fica = MaquinaModeloOllama.objects.get(maquina=m, nome_modelo="fica:1")
    assert fica.tokens_por_segundo_medio == 25.0 and fica.amostras_n == 7      # aprendizado intacto
    assert fica.tamanho_bytes == 900 and fica.carregado is True and fica.visto_pela_ultima_vez
    st = MaquinaStatus.objects.get(maquina=m)
    assert st.ollama_disponivel is True and st.ollama_versao == "0.35.1" and st.disco_ollama_livre_gb == 42.5


@pytest.mark.parametrize("disponivel", [False, None])
def test_ollama_fora_do_ar_nao_apaga_o_que_se_sabia(org, disponivel):
    m = _par(org)
    aplicar_inventario(m, [_modelo("a:1")])
    aplicar_inventario(m, [], disponivel=disponivel)
    assert MaquinaModeloOllama.objects.filter(maquina=m, nome_modelo="a:1").exists()
    assert MaquinaStatus.objects.get(maquina=m).ollama_disponivel is disponivel


# ── estado vindo de um par ──────────────────────────────────────────────────

def _estado(**kw):
    base = {"v": 1, "nome": "notebook", "versao": "abc123",
            "ollama": {"configurado": True, "disponivel": True, "versao": "0.35.1"},
            "recursos": {"cpu_percent": 12.5, "cpu_count": 8, "ram_total_mb": 16000, "ram_disponivel_mb": 9000,
                         "disco_disponivel_gb": 120.0, "disco_ollama_livre_gb": 80.0},
            "modelos": [_modelo("qwen3.5:9b", carregado=True)], "gateway_endpoint": "http://10.0.0.9:8000"}
    base.update(kw)
    return base


def test_aplicar_estado_remoto_grava_status_modelos_e_marca_o_pull(org):
    par = _par(org)
    agora = timezone.now()
    aplicar_estado_remoto(par, _estado(), agora=agora)
    par.refresh_from_db()
    st = par.status
    assert (st.cpu_count, st.ram_disponivel_mb, st.disco_disponivel_gb, st.disco_ollama_livre_gb) == (8, 9000, 120.0, 80.0)
    assert st.ultimo_heartbeat_em == agora and st.online and par.esta_online
    assert par.ultimo_pull_em == agora and par.pull_falhas == 0 and par.ultimo_pull_erro == ""
    assert par.gateway_endpoint == "http://10.0.0.9:8000" and par.capacidades_json["nome"] == "notebook"
    assert list(par.modelos_ollama.values_list("nome_modelo", "carregado")) == [("qwen3.5:9b", True)]


def test_o_gateway_anunciado_so_vale_no_mesmo_host_do_endpoint_conferido(org):
    par = _par(org)
    for anunciado in ("http://10.0.0.99:8000", "http://169.254.169.254", "http://atacante.exemplo.org", "lixo", 123, ""):
        aplicar_estado_remoto(par, _estado(gateway_endpoint=anunciado))
        par.refresh_from_db()
        assert par.gateway_endpoint == "", anunciado
    aplicar_estado_remoto(par, _estado(gateway_endpoint="http://10.0.0.9:9999"))      # mesma máquina, outra porta
    par.refresh_from_db()
    assert par.gateway_endpoint == "http://10.0.0.9:9999"
    aplicar_estado_remoto(par, _estado(gateway_endpoint=None))                          # parou de anunciar → limpa
    par.refresh_from_db()
    assert par.gateway_endpoint == ""


def test_estado_remoto_malformado_nao_quebra_nem_grava_lixo(org):
    par = _par(org)
    aplicar_estado_remoto(par, {"recursos": {"cpu_count": "oito", "ram_total_mb": -5, "cpu_percent": True,
                                             "disco_disponivel_gb": 10 ** 12}, "ollama": "x", "modelos": {"a": 1}})
    st = par.status
    assert (st.cpu_count, st.ram_total_mb, st.cpu_percent, st.disco_disponivel_gb) == (None, None, None, None)
    assert st.ollama_disponivel is None and not par.modelos_ollama.exists()
    aplicar_estado_remoto(par, "nao-e-dict")
    aplicar_estado_remoto(par, None)


def test_par_sem_ollama_configurado_fica_com_disponibilidade_desconhecida(org):
    par = _par(org)
    aplicar_estado_remoto(par, _estado(ollama={"configurado": False}, modelos=[]))
    assert par.status.ollama_disponivel is None and not par.modelos_ollama.exists()


def test_par_com_ollama_fora_do_ar_mantem_os_modelos_conhecidos(org):
    par = _par(org)
    aplicar_estado_remoto(par, _estado())
    aplicar_estado_remoto(par, _estado(ollama={"configurado": True, "disponivel": False}, modelos=[]))
    assert par.modelos_ollama.count() == 1 and MaquinaStatus.objects.get(maquina=par).ollama_disponivel is False


def test_online_depende_do_ultimo_pull(org):
    par = _par(org)
    assert not par.esta_online
    aplicar_estado_remoto(par, _estado(), agora=timezone.now() - timedelta(minutes=10))
    par.refresh_from_db()
    assert not par.esta_online                                           # estado velho: offline, mas mantido
    assert par.modelos_ollama.count() == 1


# ── pull ────────────────────────────────────────────────────────────────────

def _resposta(estado=None, status=200, assinada=True):
    return RespostaPar(status, estado if estado is not None else _estado(), assinada)


def test_puxar_par_aplica_o_estado(org):
    par = _par(org)
    assert pull.puxar_par(par, chamar=lambda *a, **k: _resposta()) is True
    par.refresh_from_db()
    assert par.ultimo_pull_em and par.status.cpu_count == 8


def test_o_pull_chama_a_rota_de_estado_do_par(org):
    par = _par(org)
    visto = {}

    def chamar(p, metodo, caminho, *a, **k):
        visto.update(par=p, metodo=metodo, caminho=caminho)
        return _resposta()

    pull.puxar_par(par, chamar=chamar)
    assert visto == {"par": par, "metodo": "GET", "caminho": "/federacao/controle/v1/estado/"}


@pytest.mark.parametrize("erro", [ParInacessivel("timeout"), RespostaInvalida("sem assinatura")])
def test_falha_registra_o_motivo_mantem_o_ultimo_estado_e_avisa_uma_vez(org, erro):
    par = _par(org)
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    assert pull.puxar_par(par, chamar=mock.Mock(side_effect=erro)) is False
    assert pull.puxar_par(par, chamar=mock.Mock(side_effect=erro)) is False
    par.refresh_from_db()
    assert par.pull_falhas == 2 and type(erro).__name__ in par.ultimo_pull_erro and par.ultimo_pull_em
    assert par.modelos_ollama.count() == 1                                # o último estado conhecido fica
    eventos = PipelineEvent.objects.filter(stage="cluster.par")
    assert [e.status for e in eventos] == ["falhou"]                      # só a transição, não cada falha


def test_resposta_nao_assinada_ou_com_status_ruim_conta_como_falha(org):
    par = _par(org)
    assert pull.puxar_par(par, chamar=lambda *a, **k: _resposta(status=403, estado={"erro": "x"}, assinada=False)) is False
    assert pull.puxar_par(par, chamar=lambda *a, **k: _resposta(assinada=False)) is False
    par.refresh_from_db()
    assert par.pull_falhas == 2 and not par.modelos_ollama.exists()


def test_erro_inesperado_nunca_escapa(org):
    par = _par(org)
    assert pull.puxar_par(par, chamar=mock.Mock(side_effect=RuntimeError("bug"))) is False
    par.refresh_from_db()
    assert par.pull_falhas == 1 and par.ultimo_pull_erro == "RuntimeError"


def test_voltar_gera_o_evento_de_recuperacao_e_zera_as_falhas(org):
    par = _par(org)
    pull.puxar_par(par, chamar=mock.Mock(side_effect=ParInacessivel("x")))
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    par.refresh_from_db()
    assert par.pull_falhas == 0 and par.ultimo_pull_erro == ""
    assert [e.status for e in PipelineEvent.objects.filter(stage="cluster.par").order_by("sequence")] == ["falhou", "ok"]


def test_um_pull_que_deu_certo_nao_vira_evento(org):
    par = _par(org)
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    assert not PipelineEvent.objects.filter(stage="cluster.par").exists()


def test_puxar_pares_so_consulta_proprios_confirmados_ativos_e_nao_a_si_mesma(org, monkeypatch):
    garantir_chave_ativa()
    ok = _par(org, "ok")
    _par(org, "terceiro", tipo="terceiro")
    _par(org, "pendente", estado="pendente")
    _par(org, "revogado", estado="revogado")
    _par(org, "inativo", ativa=False)
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "eu")
    pares.garantir_maquina_local(org)
    chamados = []

    def chamar(par, *a, **k):
        chamados.append(par.apelido)
        return _resposta()

    resumo = pull.puxar_pares(chamar=chamar)
    assert chamados == ["ok"] and resumo == {"consultados": 1, "ok": 1, "falhas": 0, "adiados": 0}
    assert ok.pk


def test_um_par_com_defeito_nao_atrasa_os_outros(org):
    ruim, bom = _par(org, "a-ruim"), _par(org, "b-bom")

    def chamar(par, *a, **k):
        if par.pk == ruim.pk:
            raise RuntimeError("explodiu")
        return _resposta()

    resumo = pull.puxar_pares(chamar=chamar)
    assert resumo["ok"] == 1 and resumo["falhas"] == 1
    bom.refresh_from_db()
    assert bom.ultimo_pull_em


def test_backoff_adia_o_par_que_falhou_e_cresce_ate_o_teto(org):
    par = _par(org)
    agora = timezone.now()
    falha = mock.Mock(side_effect=ParInacessivel("x"))
    assert pull.puxar_pares(agora=agora, chamar=falha)["falhas"] == 1
    assert pull.puxar_pares(agora=agora + timedelta(seconds=10), chamar=falha) == {"consultados": 0, "ok": 0, "falhas": 0, "adiados": 1}
    assert pull.puxar_pares(agora=agora + timedelta(seconds=61), chamar=falha)["falhas"] == 1     # 1 falha → espera 60 s
    assert [pull.espera_do_backoff(n) for n in (0, 1, 2, 3, 4, 20)] == [0, 60, 120, 240, 300, 300]
    par.refresh_from_db()
    assert par.pull_falhas == 2


def test_a_task_do_pull_devolve_o_resumo(org):
    with mock.patch("apps.cluster.pull.chamar_par", side_effect=ParInacessivel("x")):
        assert tasks.puxar_pares() == {"consultados": 0, "ok": 0, "falhas": 0, "adiados": 0}      # nenhum par
        _par(org)
        assert tasks.puxar_pares()["consultados"] == 1


def test_o_pull_esta_agendado_e_e_silencioso():
    from django.conf import settings

    from apps.events.celery_signals import TASKS_SILENCIOSAS

    assert settings.CELERY_BEAT_SCHEDULE["puxar-pares"]["task"] == "apps.cluster.tasks.puxar_pares"
    assert "apps.cluster.tasks.puxar_pares" in TASKS_SILENCIOSAS


# ── inventário da máquina local ─────────────────────────────────────────────

@pytest.fixture
def eu(org, monkeypatch):
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    return pares.garantir_maquina_local(org)


def test_inventario_local_grava_detalhes_carregados_versao_e_disco(eu, settings, tmp_path):
    settings.OLLAMA_HOST = "http://ollama:11434"
    settings.OLLAMA_DATA_DIR_NA_PORTAL = str(tmp_path)
    detalhes = [_modelo("qwen3.5:9b"), _modelo("llama3.1:8b")]
    with mock.patch.object(ollama_admin, "listar_detalhado", return_value=detalhes), \
            mock.patch.object(ollama_admin, "ps", return_value=[{"nome": "qwen3.5:9b"}]), \
            mock.patch.object(ollama_admin, "versao", return_value="0.35.1"):
        tasks._inventario_local(eu)
    por_nome = {m.nome_modelo: m for m in eu.modelos_ollama.all()}
    assert por_nome["qwen3.5:9b"].carregado is True and por_nome["llama3.1:8b"].carregado is False
    st = MaquinaStatus.objects.get(maquina=eu)
    assert st.ollama_disponivel is True and st.ollama_versao == "0.35.1" and st.disco_ollama_livre_gb > 0


def test_inventario_local_com_ollama_fora_do_ar_marca_indisponivel_e_mantem_modelos(eu, settings):
    settings.OLLAMA_HOST = "http://ollama:11434"
    aplicar_inventario(eu, [_modelo("a:1")])
    tasks._inventario_local(eu)                                  # a fixture global faz o Ollama falhar
    assert MaquinaStatus.objects.get(maquina=eu).ollama_disponivel is False and eu.modelos_ollama.filter(nome_modelo="a:1").exists()


def test_inventario_local_sem_ollama_configurado(eu, settings):
    settings.OLLAMA_HOST = ""
    tasks._inventario_local(eu)
    assert MaquinaStatus.objects.get(maquina=eu).ollama_disponivel is None


def test_falha_parcial_de_ps_ou_versao_nao_impede_o_inventario(eu, settings):
    settings.OLLAMA_HOST = "http://ollama:11434"
    with mock.patch.object(ollama_admin, "listar_detalhado", return_value=[_modelo("a:1")]):
        tasks._inventario_local(eu)                              # ps e versao falham pela fixture global
    st = MaquinaStatus.objects.get(maquina=eu)
    assert st.ollama_disponivel is True and st.ollama_versao == ""
    assert eu.modelos_ollama.get().carregado is False


def test_disco_do_ollama_desconhecido_sem_o_volume_montado(settings, tmp_path):
    settings.OLLAMA_DATA_DIR_NA_PORTAL = ""
    assert tasks._disco_ollama_livre_gb() is None
    settings.OLLAMA_DATA_DIR_NA_PORTAL = str(tmp_path / "nao-existe")
    assert tasks._disco_ollama_livre_gb() is None


def test_o_heartbeat_grava_o_inventario_local(eu, org, settings):
    settings.OLLAMA_HOST = "http://ollama:11434"
    with mock.patch.object(ollama_admin, "listar_detalhado", return_value=[_modelo("qwen3.5:9b")]):
        assert tasks.emitir_heartbeat_maquina() == "ok"
    assert eu.modelos_ollama.filter(nome_modelo="qwen3.5:9b").exists()


# ── o roteador passa a enxergar os pares ────────────────────────────────────

def test_o_roteador_escolhe_um_par_cujo_estado_foi_puxado(org, settings):
    settings.LLM_GATEWAY_TOKEN = "segredo"
    par = _par(org)
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    execucao = escolher_execucao("qwen3.5:9b")
    assert execucao is not None and execucao.maquina_id == str(par.pk)
    assert execucao.gateway == "http://10.0.0.9:8000"                    # pelo gateway anunciado, não pelo Ollama


def test_par_que_ficou_offline_deixa_de_ser_escolhido(org, settings):
    settings.LLM_GATEWAY_TOKEN = "segredo"
    par = _par(org)
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta())
    MaquinaStatus.objects.filter(maquina=par).update(ultimo_heartbeat_em=timezone.now() - timedelta(minutes=10))
    assert escolher_execucao("qwen3.5:9b") is None


def test_par_sem_gateway_anunciado_nem_endpoint_nao_e_candidato(org, settings):
    settings.LLM_GATEWAY_TOKEN = "segredo"
    par = _par(org)
    pull.puxar_par(par, chamar=lambda *a, **k: _resposta(_estado(gateway_endpoint=None)))
    assert escolher_execucao("qwen3.5:9b") is None

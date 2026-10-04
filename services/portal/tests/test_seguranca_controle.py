"""Segurança do controle entre instâncias (Marco F): o que um atacante, um par comprometido ou um erro de configuração
**não** deveriam conseguir — e os limites que o desenho assume e que ficam documentados em teste."""
import inspect
import json
import re
from unittest import mock

import pytest
from django.test import Client
from django.urls import get_resolver

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import AuditLog
from apps.cluster import pares
from apps.cluster.models import Maquina, OperacaoModeloOllama as Op
from apps.federacao import canal, views_controle
from apps.federacao.chaves import garantir_chave_ativa
from tests._instancias import ChaveOutraInstancia

pytestmark = pytest.mark.django_db

PING = "/federacao/controle/v1/ping/"
ESTADO = "/federacao/controle/v1/estado/"
OPERACOES = "/federacao/controle/v1/ollama/operacoes/"
ATOR = {"user": "u-1", "papel": "admin", "org": "o-1"}


@pytest.fixture(autouse=True)
def ambiente(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "seguranca-testes"}}
    settings.OLLAMA_HOST = "http://ollama:11434"
    settings.FEDERACAO_LIMITE_POR_MINUTO = 1000
    from django.core.cache import cache

    cache.clear()


@pytest.fixture(autouse=True)
def sem_broker():
    with mock.patch("apps.cluster.tasks.executar_operacao_modelo.apply_async", return_value=mock.Mock(id="t")):
        yield


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="institutional", owner=dono)
    Membership.objects.create(user=dono, organization=org, role="owner")
    return org


@pytest.fixture
def local(org, monkeypatch):
    garantir_chave_ativa()
    monkeypatch.setenv("CLUSTER_LOCAL_APELIDO", "antares")
    pares.garantir_maquina_local(org)
    return garantir_chave_ativa()[0]


@pytest.fixture
def remota():
    return ChaveOutraInstancia()


def _par(org, remota, **kw):
    base = dict(organizacao=org, dono=org.owner, apelido="b", did=remota.did, tipo="proprio", estado="confirmado",
                endpoint_controle="http://10.0.0.9:8000")
    base.update(kw)
    return Maquina.objects.create(**base)


def _chamar(remota, local, metodo, caminho, corpo=None, **kw):
    bruto = json.dumps(corpo).encode() if corpo is not None else b""
    h = canal.assinar_requisicao(metodo, caminho, bruto, local.did, chave=remota, **kw)
    extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()}
    return Client().generic(metodo, caminho, data=bruto, content_type="application/json", **extra)


# ── o canal só expõe o que deve ─────────────────────────────────────────────

def test_as_rotas_do_canal_sao_exatamente_as_esperadas_e_nenhuma_repassa_ao_ollama():
    from apps.federacao import urls

    nomes = {p.name for p in urls.urlpatterns}
    assert nomes == {"convite_aceitar", "controle_ping", "controle_estado", "controle_ollama_solicitar",
                     "controle_ollama_operacao", "controle_ollama_cancelar"}
    fonte = inspect.getsource(views_controle)
    # nenhum cliente HTTP no módulo das rotas: uma rota não consegue repassar tráfego bruto a ninguém
    assert "requests" not in fonte and "urlopen" not in fonte and "httpx" not in fonte


def test_nenhuma_rota_do_canal_devolve_o_endereco_do_ollama(org, local, remota, settings):
    settings.OLLAMA_ENDPOINT_ANUNCIADO = "http://segredo-interno:11434"
    _par(org, remota)
    for caminho in (PING, ESTADO):
        corpo = _chamar(remota, local, "GET", caminho).content.decode()
        assert "segredo-interno" not in corpo and "11434" not in corpo


# ── revogar e rebaixar valem na hora ────────────────────────────────────────

def test_par_revogado_depois_de_confirmado_perde_o_canal_imediatamente(org, local, remota):
    par = _par(org, remota)
    assert _chamar(remota, local, "GET", ESTADO).status_code == 200
    pares.revogar(par, org.owner)
    for metodo, caminho, corpo in (("GET", PING, None), ("GET", ESTADO, None),
                                   ("POST", OPERACOES, {"tipo": "pull", "modelo": "a:1", "ator": ATOR})):
        assert _chamar(remota, local, metodo, caminho, corpo).status_code == 404
    assert not Op.objects.exists()


def test_rebaixar_para_terceiro_corta_o_poder_de_comando_na_hora(org, local, remota):
    par = _par(org, remota)
    corpo = {"tipo": "pull", "modelo": "a:1", "ator": ATOR}
    assert _chamar(remota, local, "POST", OPERACOES, corpo).status_code == 200
    pares.definir_tipo(par, org.owner, "terceiro")
    resp = _chamar(remota, local, "POST", OPERACOES, {**corpo, "modelo": "b:1"})
    assert resp.status_code == 403 and Op.objects.count() == 1


def test_par_desativado_nao_passa(org, local, remota):
    par = _par(org, remota)
    Maquina.objects.filter(pk=par.pk).update(ativa=False)
    assert _chamar(remota, local, "GET", PING).status_code == 404


# ── replay e janela ─────────────────────────────────────────────────────────

def test_o_mesmo_nonce_nao_vale_nem_em_outra_rota(org, local, remota):
    _par(org, remota)
    assert _chamar(remota, local, "GET", PING, nonce="reuso").status_code == 200
    assert _chamar(remota, local, "GET", ESTADO, nonce="reuso").status_code == 404


def test_requisicao_antiga_ou_do_futuro_e_recusada(org, local, remota):
    _par(org, remota)
    import time

    assert _chamar(remota, local, "GET", PING, agora=time.time() - 3600).status_code == 404
    assert _chamar(remota, local, "GET", PING, agora=time.time() + 3600).status_code == 404


# ── limites assumidos (documentados em teste) ───────────────────────────────

def test_LIMITE_um_par_proprio_comprometido_pode_afirmar_qualquer_ator_administrador(org, local, remota):
    """O receptor **não verifica** o papel que a origem afirma: quem controla a chave de um par próprio manda
    comandos. É o preço do rótulo "próprio" — mitigado por confirmação explícita, impressão digital conferida
    e, aqui, pelo **rastro forense**: ator afirmado e par de origem ficam no `AuditLog` e na operação."""
    par = _par(org, remota)
    ator_inventado = {"user": "qualquer-coisa", "papel": "owner", "org": "x"}
    resp = _chamar(remota, local, "POST", OPERACOES, {"tipo": "pull", "modelo": "a:1", "ator": ator_inventado})
    assert resp.status_code == 200
    op = Op.objects.get()
    assert op.origem_par_id == par.id and op.ator_afirmado["user"] == "qualquer-coisa"
    log = AuditLog.objects.get(operation="modelo_ollama.pull_solicitado")
    assert log.metadata["origem_par"] == str(par.id) and log.metadata["ator"]["user"] == "qualquer-coisa"


def test_o_dano_de_um_par_comprometido_se_limita_a_modelos_e_ao_teto_de_operacoes(org, local, remota):
    """Mesmo com o poder acima, o par só instala/remove modelos de nome válido do registry oficial, com no máximo
    N operações ativas e sem duplicar — não executa código nem lê dados."""
    _par(org, remota)
    maus = ["../../etc/passwd", "registry.atacante.com/x/y:1", "a;rm -rf /", "http://x/y", "Nome Com Espaço"]
    for nome in maus:
        assert _chamar(remota, local, "POST", OPERACOES, {"tipo": "pull", "modelo": nome, "ator": ATOR}).status_code == 400
    codigos = [_chamar(remota, local, "POST", OPERACOES, {"tipo": "pull", "modelo": f"m{n}:1", "ator": ATOR}).status_code for n in range(6)]
    assert codigos.count(200) == 3 and set(codigos) == {200, 409}          # teto de 3 operações ativas por máquina
    assert Op.objects.count() == 3


def test_a_resposta_de_recusa_nao_distingue_o_motivo(org, local, remota):
    """Um atacante sem chave não aprende nada: DID inexistente e DID revogado respondem igual."""
    revogado = ChaveOutraInstancia()
    _par(org, revogado, estado="revogado", apelido="rev")
    desconhecido = ChaveOutraInstancia()
    respostas = {_chamar(chave, local, "GET", ESTADO).content for chave in (revogado, desconhecido)}
    assert len(respostas) == 1


def test_o_texto_dos_avisos_de_risco_esta_na_tela_de_enrolamento(org, local):
    c = Client()
    c.force_login(org.owner)
    html = c.get("/cluster/pares/").content.decode()
    previa = c.post("/cluster/pares/convite/previsualizar/", {"organizacao": str(org.id), "codigo": pares._codificar(
        {"v": 1, "token": "t" * 24, "did": ChaveOutraInstancia().did, "endpoint": "http://10.0.0.9:8000", "nome": "x"})}).content.decode()
    assert "Próprio” dá poder" in previa and re.search(r"não prova|não prova que a outra instância é sua", previa)
    assert "Convidar outra instância" in html

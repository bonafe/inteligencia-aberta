"""IPED (ADR 019): caminhos permitidos, pedidos, protocolo de arquivos e o runner de verdade.

O runner (`infra/iped/runner.sh`) é executado de verdade, com um `java` falso no PATH; sem o
script visível (o contêiner do portal só monta `services/portal`), esses testes são pulados.
Para rodá-los: `-v $PWD/infra/iped:/infra-iped:ro` e `RUNNER_SH=/infra-iped/runner.sh`.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from apps.accounts.models import Organization, User
from apps.forense import caminhos, fila, servico, tasks
from apps.forense.models import ProcessamentoIPED

_aqui = Path(__file__).resolve()
_candidatos = [os.environ.get("RUNNER_SH", "")] + [str(a / "infra/iped/runner.sh") for a in _aqui.parents]
RUNNER = next((c for c in _candidatos if c and Path(c).exists()), None)


@pytest.fixture
def raiz(tmp_path, settings):
    r = tmp_path / "entrada"
    (r / "caso1").mkdir(parents=True)
    (r / "caso1" / "a.txt").write_text("x")
    settings.IPED_RAIZES_PERMITIDAS = [str(r)]
    settings.IPED_ENTRADA_RAIZ = str(r)
    settings.IPED_TRABALHO_DIR = str(tmp_path / "trabalho")
    return r


@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="d", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


# ── caminhos ─────────────────────────────────────────────────────────────────

def test_resolve_dentro_da_raiz(raiz):
    assert caminhos.resolver(str(raiz), "caso1") == (raiz / "caso1").resolve()
    assert caminhos.resolver(str(raiz), ".") == raiz.resolve()


@pytest.mark.parametrize("relativo", ["../fora", "caso1/../../fora", "/etc/passwd", "inexistente", "a\x00b"])
def test_recusa_fuga_absoluto_inexistente_e_nul(raiz, relativo):
    with pytest.raises(caminhos.CaminhoNaoPermitido):
        caminhos.resolver(str(raiz), relativo)


def test_recusa_link_simbolico_que_foge(raiz, tmp_path):
    fora = tmp_path / "segredo"
    fora.mkdir()
    (raiz / "atalho").symlink_to(fora)
    with pytest.raises(caminhos.CaminhoNaoPermitido):
        caminhos.resolver(str(raiz), "atalho")


def test_raiz_nao_listada_ou_proibida_e_recusada(raiz, settings, tmp_path):
    outra = tmp_path / "outra"
    outra.mkdir()
    with pytest.raises(caminhos.CaminhoNaoPermitido):
        caminhos.resolver(str(outra), ".")
    settings.IPED_RAIZES_PERMITIDAS = ["/", "/etc", "relativa"]
    assert caminhos.raizes_permitidas() == []
    with pytest.raises(caminhos.CaminhoNaoPermitido):
        caminhos.resolver("/etc", "passwd")


# ── pedido ───────────────────────────────────────────────────────────────────

def test_solicitar_valida_perfil_e_caminho(raiz, org):
    with pytest.raises(servico.ErroProcessamento, match="perfil"):
        servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", perfil="rm -rf", despachar=False)
    with pytest.raises(servico.ErroProcessamento):
        servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="../x", despachar=False)
    p = servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", despachar=False)
    assert p.estado == "pendente" and p.perfil == "fastmode"


def test_fila_recusa_caminho_estranho(raiz):
    for ruim in ("../x", "/abs", "-opcao", "a\nperfil=forensic", ""):
        with pytest.raises(ValueError):
            fila.enviar("11111111-1111-4111-8111-111111111111", perfil="fastmode", relativo=ruim)


def test_cancelar_pendente(raiz, org):
    p = servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", despachar=False)
    assert servico.cancelar(p).estado == "cancelado"


# ── runner de verdade ────────────────────────────────────────────────────────

@pytest.fixture
def runner(raiz, tmp_path, settings, monkeypatch):
    if RUNNER is None:
        pytest.skip("runner.sh não visível neste ambiente")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    iped = tmp_path / "iped"
    iped.mkdir()
    (iped / "iped.jar").write_text("")
    # `java` falso: acha o -o, cria a saída e dorme se a entrada tiver um arquivo `lento`.
    (bin_ / "java").write_text(
        '#!/bin/bash\nentrada=""; saida=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -d) entrada="$2"; shift;; -o) saida="$2"; shift;; esac; shift; done\n'
        'mkdir -p "$saida"; echo "processado $entrada" > "$saida/ok.txt"\n'
        '[ -e "$entrada/lento" ] && sleep 60\n[ -e "$entrada/quebra" ] && { echo "boom" >&2; exit 3; }\nexit 0\n'
    )
    (bin_ / "java").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "IPED_TRABALHO": settings.IPED_TRABALHO_DIR,
           "IPED_ENTRADA": str(raiz.resolve()), "IPED_SAIDA": str(tmp_path / "saida"), "IPED_DIR": str(iped)}
    proc = subprocess.Popen(["bash", RUNNER], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(tasks, "INTERVALO_S", 0.2)
    yield tmp_path / "saida"
    proc.terminate()
    proc.wait(10)


def _esperar(pid, finais=fila.ESTADOS_FINAIS, segundos=20):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        e = fila.estado(pid)
        if e in finais:
            return e
        time.sleep(0.2)
    raise AssertionError("runner não terminou")


def test_runner_processa_e_task_conclui(raiz, org, runner):
    p = servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", despachar=False)
    res = tasks.executar_processamento_iped(str(p.id))
    p.refresh_from_db()
    assert res["status"] == "concluido" and p.estado == "concluido" and p.codigo_saida == 0
    assert (runner / str(p.id) / "ok.txt").read_text().startswith("processado ")


def test_runner_falha_e_task_guarda_o_log(raiz, org, runner):
    (raiz / "caso1" / "quebra").write_text("")
    p = servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", despachar=False)
    tasks.executar_processamento_iped(str(p.id))
    p.refresh_from_db()
    assert p.estado == "falhou" and p.codigo_saida == 3 and "boom" in p.erro


def test_runner_recusa_pedido_forjado(raiz, runner):
    # alguém com acesso ao volume escreve um pedido malicioso direto: o runner revalida.
    base = fila.trabalho() / "pedidos"
    base.mkdir(parents=True, exist_ok=True)
    casos = {
        "22222222-2222-4222-8222-222222222222": "perfil=fastmode\nentrada=../../etc\n",
        "33333333-3333-4333-8333-333333333333": "perfil=fastmode; rm -rf /\nentrada=caso1\n",
        "44444444-4444-4444-8444-444444444444": "perfil=forensic\nentrada=/etc\n",
    }
    for pid, conteudo in casos.items():
        (base / f"{pid}.pedido").write_text(conteudo)
    for pid in casos:
        assert _esperar(pid) == "falhou"


def test_runner_cancela_em_andamento(raiz, org, runner):
    (raiz / "caso1" / "lento").write_text("")
    p = servico.solicitar(organizacao=org, usuario=None, raiz=str(raiz), relativo="caso1", despachar=False)
    import threading

    def cancelar_logo():
        _esperar(p.id, finais=("executando",))
        fila.pedir_cancelamento(p.id)

    t = threading.Thread(target=cancelar_logo)
    t.start()
    tasks.executar_processamento_iped(str(p.id))
    t.join()
    p.refresh_from_db()
    assert p.estado == "cancelado"

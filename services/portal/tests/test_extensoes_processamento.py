"""ADR 023, fase 2: fontes de ingestão, etapas de processamento e agendamentos declarados por extensões.

Validado com duas extensões de exemplo fora de `apps/`: `ia_email` (fonte + etapa) e `ia_transcricao`
(etapa em fila própria + agendamento)."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact
from apps.events.models import PipelineEvent
from apps.extensoes import dados, registro
from apps.extensoes.contrato import Agendamento, EtapaProcessamento, Extensao, FonteIngestao, Vocabulario
from apps.extensoes.tasks import executar_etapa

PORTAL = Path(__file__).resolve().parent.parent
APPS = ["ia_email.apps.EmailConfig", "ia_transcricao.apps.TranscricaoConfig"]
EML = b"From: ana@exemplo.invalid\r\nTo: rui@exemplo.invalid\r\nSubject: Oi\r\nDate: Mon, 02 Nov 2026 10:00:00 -0300\r\n\r\ncorpo\r\n"


@pytest.fixture
def com_exemplos(monkeypatch):
    monkeypatch.syspath_prepend(str(PORTAL / "exemplos"))
    registro.extensoes.cache_clear()
    try:
        with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, *APPS]):
            registro.extensoes.cache_clear()
            yield
    finally:
        registro.extensoes.cache_clear()
        for nome in ("ia_email", "ia_transcricao"):
            for m in [m for m in sys.modules if m == nome or m.startswith(nome + ".")]:
                sys.modules.pop(m, None)


@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="d", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


def _arquivo(org, nome, categoria, **extra):
    return Artifact.objects.create(
        artifact_type="arquivo", tenant=org, classification_level="restrito", allow_external_llm=False,
        content={"categoria": categoria, "nome": nome, "bucket": "b", "caminho": f"sha256/{nome}", **extra})


def _eventos(chave):
    return list(PipelineEvent.objects.filter(stage=f"extensao.etapa.{chave}"))


# ── contrato e registro ──────────────────────────────────────────────────────

def _manifesto(**kw):
    base = dict(nome="x", rotulo="X", versao="1", requer_api=3, vocabulario=Vocabulario("xx", "https://x.invalid/v1#"))
    return Extensao(**{**base, **kw})


def _instalar(monkeypatch, *manifestos):
    configs = [SimpleNamespace(name=f"pacote{i}", extensao=m) for i, m in enumerate(manifestos)]
    monkeypatch.setattr(registro.apps, "get_app_configs", lambda: configs)
    registro.extensoes.cache_clear()
    try:
        return registro.extensoes()
    finally:
        registro.extensoes.cache_clear()


def test_chave_de_etapa_fonte_ou_agendamento_leva_o_nome_da_extensao(monkeypatch):
    for campo, item in (("etapas", EtapaProcessamento("outra.coisa", "r", "m.f")),
                        ("fontes", FonteIngestao("outra.coisa", "r", "d", "m.f")),
                        ("agendamentos", Agendamento("outra.coisa", "m.t", 5.0))):
        with pytest.raises(ValueError, match="deve começar por 'x.'"):
            _instalar(monkeypatch, _manifesto(**{campo: (item,)}))


def test_chave_repetida_entre_extensoes_e_recusada(monkeypatch):
    a = _manifesto(etapas=(EtapaProcessamento("x.e", "r", "m.f"),))
    b = _manifesto(nome="y", vocabulario=Vocabulario("yy", "https://y.invalid/v1#"),
                   etapas=(EtapaProcessamento("y.e", "r", "m.f"),))
    assert [e.nome for e in _instalar(monkeypatch, a, b)] == ["x", "y"]
    with pytest.raises(ValueError, match="chave repetida"):
        _instalar(monkeypatch, _manifesto(etapas=(EtapaProcessamento("x.e", "r", "m.f"), EtapaProcessamento("x.e", "r", "m.g"))))


@pytest.mark.parametrize("nome,categoria,casa", [
    ("a.eml", "texto", True), ("A.EML", "texto", True), ("a.txt", "texto", False)])
def test_filtro_por_sufixo(com_exemplos, org, nome, categoria, casa):
    etapa = registro.etapa("email.cabecalhos")
    assert registro.casa(etapa, _arquivo(org, nome, categoria)) is casa


def test_filtro_por_tipo_e_categoria(com_exemplos, org):
    etapa = registro.etapa("transcricao.audio")
    assert registro.casa(etapa, _arquivo(org, "a.mp3", "audio"))
    assert registro.casa(etapa, _arquivo(org, "a.mp4", "video"))
    assert not registro.casa(etapa, _arquivo(org, "a.jpg", "foto"))
    documento = Artifact.objects.create(artifact_type="documento", tenant=org, classification_level="interno",
                                        content={"categoria": "audio"})
    assert not registro.casa(etapa, documento)


# ── despacho ao criar o artefato ─────────────────────────────────────────────

def test_artefato_novo_enfileira_so_as_etapas_que_combinam_e_na_fila_declarada(
        com_exemplos, org, django_capture_on_commit_callbacks):
    with mock.patch.object(executar_etapa, "apply_async") as enfileirar:
        with django_capture_on_commit_callbacks(execute=True):
            eml = _arquivo(org, "a.eml", "texto")
            audio = _arquivo(org, "a.mp3", "audio")
            _arquivo(org, "a.jpg", "foto")
    chamadas = {c.kwargs["args"][1]: c for c in enfileirar.call_args_list}
    assert set(chamadas) == {str(eml.id), str(audio.id)}
    assert chamadas[str(eml.id)].kwargs == {"args": ["email.cabecalhos", str(eml.id)], "queue": None}
    assert chamadas[str(audio.id)].kwargs["queue"] == "gpu"


def test_sem_extensoes_o_artefato_nao_dispara_nada(org, django_capture_on_commit_callbacks):
    registro.extensoes.cache_clear()
    with mock.patch.object(executar_etapa, "apply_async") as enfileirar:
        with django_capture_on_commit_callbacks(execute=True):
            _arquivo(org, "a.eml", "texto")
    enfileirar.assert_not_called()


def test_editar_o_artefato_nao_reenfileira(com_exemplos, org, django_capture_on_commit_callbacks):
    with mock.patch.object(executar_etapa, "apply_async") as enfileirar:
        with django_capture_on_commit_callbacks(execute=True):
            a = _arquivo(org, "a.eml", "texto")
        enfileirar.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            a.content = {**a.content, "x": 1}
            a.save()
    enfileirar.assert_not_called()


# ── a etapa roda e deixa o evento ────────────────────────────────────────────

def test_etapa_de_email_extrai_cabecalhos_e_emite_ok(com_exemplos, org):
    a = _arquivo(org, "a.eml", "texto")
    with mock.patch("apps.federacao.blobs.ler_blob", return_value=EML):
        assert executar_etapa("email.cabecalhos", str(a.id)) == "ok"
    a.refresh_from_db()
    assert dados.ler(a, "email")["subject"] == "Oi" and dados.ler(a, "email")["from"] == "ana@exemplo.invalid"
    (evento,) = _eventos("email.cabecalhos")
    assert evento.status == "ok" and evento.subject_id == a.id and evento.tenant_id == org.id


def test_etapa_que_levanta_vira_evento_falhou_e_nao_derruba_quem_chamou(com_exemplos, org):
    a = _arquivo(org, "a.eml", "texto")
    with mock.patch("apps.federacao.blobs.ler_blob", side_effect=RuntimeError("garage fora")):
        assert executar_etapa("email.cabecalhos", str(a.id)) == "falhou"
    (evento,) = _eventos("email.cabecalhos")
    assert evento.status == "falhou" and "garage fora" in evento.message


def test_etapa_vazia_e_distinta_de_falha(com_exemplos, org):
    a = _arquivo(org, "a.eml", "texto")
    with mock.patch("apps.federacao.blobs.ler_blob", return_value=b"\r\ncorpo sem cabecalho"):
        assert executar_etapa("email.cabecalhos", str(a.id)) == "vazio"
    assert _eventos("email.cabecalhos")[0].status == "vazio"


def test_transcricao_sem_motor_e_ignorada_e_com_motor_grava(com_exemplos, org):
    a = _arquivo(org, "a.mp3", "audio")
    with mock.patch("ia_transcricao.etapas._motor", return_value=None):
        assert executar_etapa("transcricao.audio", str(a.id)) == "ignorado"
    with mock.patch("ia_transcricao.etapas._motor", return_value=lambda caminho: " olá mundo "), \
         mock.patch("apps.federacao.blobs.ler_blob", return_value=b"audio"):
        assert executar_etapa("transcricao.audio", str(a.id)) == "ok"
        a.refresh_from_db()
        assert dados.ler(a, "transcricao")["texto"] == "olá mundo"
        assert executar_etapa("transcricao.audio", str(a.id)) == "ignorado"       # idempotente
    assert [e.status for e in _eventos("transcricao.audio")] == ["ignorado", "ok", "ignorado"]


def test_etapa_de_extensao_desinstalada_nao_quebra(org):
    registro.extensoes.cache_clear()
    a = _arquivo(org, "a.eml", "texto")
    assert executar_etapa("email.cabecalhos", str(a.id)) == "ignorado"


def test_cada_extensao_escreve_so_no_proprio_espaco(com_exemplos, org):
    a = _arquivo(org, "a.eml", "texto")
    dados.anexar(a, "email", {"a": 1})
    dados.anexar(a, "transcricao", {"b": 2})
    a.refresh_from_db()
    assert a.content["extensoes"] == {"email": {"a": 1}, "transcricao": {"b": 2}} and a.content["categoria"] == "texto"


# ── agendamento ──────────────────────────────────────────────────────────────

def test_varredura_enfileira_so_o_que_falta_transcrever(com_exemplos, org):
    pendente = _arquivo(org, "a.mp3", "audio")
    feito = _arquivo(org, "b.mp3", "audio")
    dados.anexar(feito, "transcricao", {"texto": "x"})
    from ia_transcricao.tasks import varrer_pendentes
    with mock.patch.object(executar_etapa, "apply_async") as enfileirar:
        assert varrer_pendentes() == 1
    enfileirar.assert_called_once_with(args=["transcricao.audio", str(pendente.id)], queue="gpu")


def test_beat_recebe_os_agendamentos_das_extensoes(com_exemplos):
    from config.celery import agendamentos_das_extensoes
    sender = mock.Mock()
    agendamentos_das_extensoes(sender=sender)
    (chamada,) = sender.scheduler.add.call_args_list
    assert chamada.kwargs["name"] == "extensao.transcricao.varrer"
    assert chamada.kwargs["task"] == "ia_transcricao.tasks.varrer_pendentes"
    assert chamada.kwargs["schedule"].run_every.total_seconds() == 300


# ── comandos ─────────────────────────────────────────────────────────────────

def test_importar_fonte_chama_a_funcao_da_extensao(com_exemplos, org, tmp_path):
    from io import StringIO
    saida = StringIO()
    with mock.patch("ia_email.fonte.acervo.importar_pasta") as importar:
        importar.return_value = SimpleNamespace(total_erros=0, novos=3, duplicados=0, pulados=0, arquivos=3,
                                                projetos=0, bytes_novos=10, erros=[])
        with mock.patch("ia_email.fonte.asdict", return_value={"novos": 3, "erros": []}):
            call_command("importar_fonte", "email.pasta", str(tmp_path), "--organizacao", "o", stdout=saida)
    assert importar.call_args.kwargs["organizacao"] == org and importar.call_args.kwargs["nivel"] == "restrito"
    assert '"novos": 3' in saida.getvalue()


def test_importar_fonte_desconhecida_e_recusada(com_exemplos, org, tmp_path):
    from django.core.management.base import CommandError
    with pytest.raises(CommandError, match="fonte desconhecida"):
        call_command("importar_fonte", "nada.nada", str(tmp_path), "--organizacao", "o")


def test_rodar_etapa_enfileira_so_os_que_combinam(com_exemplos, org):
    _arquivo(org, "a.eml", "texto")
    _arquivo(org, "b.txt", "texto")
    with mock.patch.object(executar_etapa, "apply_async") as enfileirar:
        call_command("rodar_etapa", "email.cabecalhos", "--organizacao", "o", "--simular")
        enfileirar.assert_not_called()
        call_command("rodar_etapa", "email.cabecalhos", "--organizacao", "o")
    assert enfileirar.call_count == 1

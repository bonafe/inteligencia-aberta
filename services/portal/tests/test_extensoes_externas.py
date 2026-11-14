"""ADR 023: uma extensão de fora de `apps/` é ligada pelo administrador (EXTENSOES), sem editar settings."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import override_settings

from apps.artifacts.alegacoes import prefixo_conhecido
from apps.extensoes import registro
from apps.extensoes.contrato import API_EXTENSOES, Extensao, Vocabulario
from apps.federacao import politica
from config.extensoes_externas import apps_das_extensoes

PORTAL = Path(__file__).resolve().parent.parent
APP_EXEMPLO = "ia_exemplo.apps.ExemploConfig"


def _ep(nome, valor):
    return SimpleNamespace(name=nome, value=valor)


# ── EXTENSOES → INSTALLED_APPS ───────────────────────────────────────────────

def test_sem_extensoes_nada_e_acrescentado():
    assert apps_das_extensoes("", []) == [] and apps_das_extensoes("  , ", []) == []


def test_nome_de_entry_point_vira_o_caminho_da_appconfig():
    eps = [_ep("exemplo", "ia_exemplo.apps:ExemploConfig")]
    assert apps_das_extensoes("exemplo", eps) == [APP_EXEMPLO]


def test_caminho_pontuado_passa_direto_e_sem_repetir():
    eps = [_ep("exemplo", "ia_exemplo.apps:ExemploConfig")]
    assert apps_das_extensoes("exemplo, ia_exemplo.apps.ExemploConfig,outra.apps.Config", eps) == [
        APP_EXEMPLO, "outra.apps.Config"]


def test_so_liga_o_que_o_administrador_listou():
    eps = [_ep("exemplo", "ia_exemplo.apps:ExemploConfig"), _ep("outra", "outra.apps:Config")]
    assert apps_das_extensoes("exemplo", eps) == [APP_EXEMPLO]


# ── a extensão de exemplo, fora de apps/, descoberta pelo núcleo ─────────────

@pytest.fixture
def com_exemplo(monkeypatch):
    monkeypatch.syspath_prepend(str(PORTAL / "exemplos"))
    registro.extensoes.cache_clear()
    try:
        with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, APP_EXEMPLO]):
            registro.extensoes.cache_clear()
            yield
    finally:
        registro.extensoes.cache_clear()
        sys.modules.pop("ia_exemplo", None)


def test_a_extensao_de_exemplo_e_descoberta_e_o_nucleo_a_usa(com_exemplo):
    nomes = [e.nome for e in registro.extensoes()]
    assert "exemplo" in nomes and "saude" in nomes          # a de fora convive com as do repositório
    assert prefixo_conhecido("ex")
    assert registro.tipo_do_predicado("ex:titulo").nome == "ex:Nota"
    assert "ex:Nota" in dict(politica.tipos_de_objeto())
    assert any(i.rotulo == "Exemplo" for i in registro.menu())
    assert any("exemplo/" in str(p.pattern) for p in registro.urlpatterns())


def test_sem_listar_a_extensao_o_nucleo_nao_a_conhece():
    registro.extensoes.cache_clear()
    assert "exemplo" not in [e.nome for e in registro.extensoes()]
    assert not prefixo_conhecido("ex")


def test_o_nucleo_nao_cita_a_extensao_de_exemplo():
    for arquivo in [*PORTAL.joinpath("apps").rglob("*.py"), *PORTAL.joinpath("config").rglob("*.py"),
                    *PORTAL.joinpath("templates").rglob("*.html")]:
        if arquivo.name == "extensoes_externas.py" or arquivo.name == "base.py" and arquivo.parent.name == "settings":
            continue
        assert "ia_exemplo" not in arquivo.read_text(encoding="utf-8"), arquivo


# ── manifesto v2: compatibilidade e dependências ─────────────────────────────

def _manifesto(**kw):
    base = dict(nome="x", rotulo="X", versao="1", vocabulario=Vocabulario("xx", "https://x.invalid/v1#"))
    return Extensao(**{**base, **kw})


def _registrar(monkeypatch, *manifestos):
    configs = [SimpleNamespace(name=f"pacote{i}", extensao=m) for i, m in enumerate(manifestos)]
    monkeypatch.setattr(registro.apps, "get_app_configs", lambda: configs)
    registro.extensoes.cache_clear()
    try:
        return registro.extensoes()
    finally:
        registro.extensoes.cache_clear()


def test_extensao_que_exige_api_mais_nova_que_o_nucleo_e_recusada(monkeypatch):
    with pytest.raises(ValueError, match="exige a API de extensões"):
        _registrar(monkeypatch, _manifesto(requer_api=API_EXTENSOES + 1))


def test_dependencia_ausente_e_recusada_e_presente_e_aceita(monkeypatch):
    with pytest.raises(ValueError, match="depende de extensões não instaladas"):
        _registrar(monkeypatch, _manifesto(dependencias=("outra",)))
    ok = _registrar(monkeypatch, _manifesto(dependencias=("b",)),
                    _manifesto(nome="b", vocabulario=Vocabulario("bb", "https://b.invalid/v1#")))
    assert [e.nome for e in ok] == ["x", "b"]


def test_nome_de_extensao_repetido_e_recusado(monkeypatch):
    with pytest.raises(ValueError, match="nome de extensão repetido"):
        _registrar(monkeypatch, _manifesto(), _manifesto(vocabulario=Vocabulario("yy", "https://y.invalid/v1#")))

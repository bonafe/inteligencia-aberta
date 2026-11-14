"""ADR 023, fase 3: componentes do Agora, rotas de leitura e precache vindos de extensões.

Validado com `exemplos/ia_mapa` (componente `ia-map` + rota `/agora/api/v1/dominio/mapa/pontos/`)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import RequestFactory, override_settings

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact
from apps.extensoes import registro
from apps.extensoes.contrato import Extensao, PackUI, Vocabulario

PORTAL = Path(__file__).resolve().parent.parent
APP = "ia_mapa.apps.MapaConfig"


def _limpar():
    registro.extensoes.cache_clear()
    finders.get_finder.cache_clear()


@pytest.fixture
def com_mapa(monkeypatch):
    monkeypatch.syspath_prepend(str(PORTAL / "exemplos"))
    _limpar()
    try:
        with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, APP]):
            _limpar()
            yield
    finally:
        _limpar()
        for m in [m for m in sys.modules if m == "ia_mapa" or m.startswith("ia_mapa.")]:
            sys.modules.pop(m, None)


@pytest.fixture
def mundo(db):
    def org(slug):
        dono = User.objects.create_user(username=f"u-{slug}", password="x")
        organizacao = Organization.objects.create(name=slug, slug=slug, org_type="individual", owner=dono)
        Membership.objects.create(user=dono, organization=organizacao, role=Membership.Role.OWNER)
        return organizacao
    return org("a"), org("b")


def _ponto(org, nome, lat, lon):
    return Artifact.objects.create(artifact_type="endereco", tenant=org, classification_level="interno",
                                   content={"nome": nome, "latitude": lat, "longitude": lon})


# ── registro ─────────────────────────────────────────────────────────────────

def _instalar(monkeypatch, manifesto):
    monkeypatch.setattr(registro.apps, "get_app_configs", lambda: [SimpleNamespace(name="p", extensao=manifesto)])
    registro.extensoes.cache_clear()
    try:
        return registro.extensoes()
    finally:
        registro.extensoes.cache_clear()


def _manifesto(**kw):
    base = dict(nome="x", rotulo="X", versao="1", requer_api=4, vocabulario=Vocabulario("xx", "https://x.invalid/v1#"))
    return Extensao(**{**base, **kw})


@pytest.mark.parametrize("base,manifestos", [
    ("/abs/", ("a.json",)), ("../fora/", ("a.json",)), ("x/", ("../a.json",)), ("x/", ("/a.json",)),
    ("agora/pack/", ("a.json",)), ("agora-ia/pack/", ("a.json",)), ("", ("a.json",))])
def test_pack_com_caminho_perigoso_ou_reservado_e_recusado(monkeypatch, base, manifestos):
    with pytest.raises(ValueError):
        _instalar(monkeypatch, _manifesto(packs=(PackUI(base, manifestos),)))


def test_pack_valido_e_aceito(monkeypatch):
    (e,) = _instalar(monkeypatch, _manifesto(packs=(PackUI("meu/pack/", ("c/c.manifest.json",)),)))
    assert e.packs[0].base == "meu/pack/"


# ── a extensão de exemplo ────────────────────────────────────────────────────

def test_o_agora_recebe_os_manifestos_dos_componentes_da_extensao(com_mapa, client, mundo):
    client.force_login(User.objects.get(username="u-a"))
    config = client.get("/agora/").context["configuracao"]
    assert config["packs"] == ["/static/ia_mapa/pack/ia-map/ia-map.manifest.json"]


def test_sem_a_extensao_o_agora_nao_recebe_pack_nenhum(client, mundo):
    _limpar()
    client.force_login(User.objects.get(username="u-a"))
    assert client.get("/agora/").context["configuracao"]["packs"] == []


def test_os_arquivos_do_componente_sao_servidos_como_estaticos(com_mapa):
    for arquivo in ("ia-map.manifest.json", "ia-map.js", "ia-map.html", "ia-map.css"):
        assert finders.find(f"ia_mapa/pack/ia-map/{arquivo}"), arquivo
    manifesto = json.loads(Path(finders.find("ia_mapa/pack/ia-map/ia-map.manifest.json")).read_text())
    assert manifesto["tag"] == "ia-map" and "domain-data" in manifesto["capabilities"]


def test_precache_inclui_a_pasta_do_pack_e_muda_de_versao(com_mapa, client):
    com = client.get("/agora/precache.json")                     # sem login: só nomes de estáticos
    assert com.status_code == 200 and com["Cache-Control"] == "no-cache"
    lista = com.json()
    for esperado in ("/static/ia_mapa/pack/ia-map/ia-map.js", "/static/ia_mapa/pack/ia-map/ia-map.manifest.json",
                     "/static/agora-ia/boot.js"):
        assert esperado in lista["files"], esperado
    assert client.get("/agora/precache.json").json() == lista       # estável
    base = json.loads(Path(finders.find("agora-ia/precache.json")).read_text())
    assert lista["version"] != base["version"] and set(base["files"]) < set(lista["files"])


def test_sem_extensao_o_precache_e_exatamente_o_do_app(client):
    _limpar()
    base = json.loads(Path(finders.find("agora-ia/precache.json")).read_text())
    assert client.get("/agora/precache.json").json() == base


def test_a_rota_de_dominio_da_extensao_e_montada_sob_o_dominio_do_agora(com_mapa):
    (padrao,) = registro.urlpatterns_dominio()
    assert str(padrao.pattern) == "mapa/"


def test_pontos_so_das_organizacoes_da_pessoa_e_com_coordenadas_validas(com_mapa, mundo):
    from ia_mapa.views import pontos

    a, b = mundo
    _ponto(a, "Sede", -23.5, -46.6)
    _ponto(b, "De outra organização", -1.0, -1.0)
    _ponto(a, "Fora da Terra", 123.0, 0.0)
    Artifact.objects.create(artifact_type="endereco", tenant=a, classification_level="interno", content={"nome": "Sem coordenada"})
    pedido = RequestFactory().get("/agora/api/v1/dominio/mapa/pontos/")
    pedido.user = User.objects.get(username="u-a")
    rotulos = [p["label"] for p in json.loads(pontos(pedido).content)["results"]]
    assert rotulos == ["Sede"]


def test_o_nucleo_nao_cita_a_extensao_de_mapa():
    for arquivo in [*PORTAL.joinpath("apps").rglob("*.py"), *PORTAL.joinpath("config").rglob("*.py"),
                    *PORTAL.joinpath("templates").rglob("*.html"), *PORTAL.joinpath("static/agora-ia").rglob("*.js")]:
        if "tests" in arquivo.parts or arquivo.name == "base.py" and arquivo.parent.name == "settings":
            continue
        assert "ia_mapa" not in arquivo.read_text(encoding="utf-8"), arquivo

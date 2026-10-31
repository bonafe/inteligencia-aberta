"""ADR 022: o núcleo não conhece as extensões de domínio pelo nome, e funciona sem elas."""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from apps.artifacts.alegacoes import prefixo_conhecido
from apps.extensoes import registro
from apps.extensoes.contrato import Extensao, TipoDado

PORTAL = Path(__file__).resolve().parent.parent


def _extensoes_instaladas():
    return [(e, next(c for c in registro.apps.get_app_configs() if getattr(c, "extensao", None)
                     and registro.import_string(c.extensao) is e)) for e in registro.extensoes()]


def _arquivos_do_nucleo(pacote_extensao: str, label: str):
    """Python e templates de tudo que não é a própria extensão (migrations do núcleo incluídas)."""
    pasta_extensao = PORTAL / Path(*pacote_extensao.split("."))
    for arquivo in [*PORTAL.joinpath("apps").rglob("*.py"), *PORTAL.joinpath("config").rglob("*.py"),
                    *PORTAL.joinpath("templates").rglob("*.html")]:
        if pasta_extensao in arquivo.parents or PORTAL / "templates" / label in arquivo.parents:
            continue
        yield arquivo


def test_ha_pelo_menos_uma_extensao_para_o_teste_valer():
    assert registro.extensoes(), "sem extensão instalada o teste de arquitetura não testa nada"


@pytest.mark.parametrize("extensao,config", _extensoes_instaladas(), ids=lambda x: getattr(x, "nome", ""))
def test_nucleo_nao_cita_a_extensao(extensao, config):
    proibidos = [
        re.compile(rf"\b{re.escape(config.name)}\b"),                   # import apps.financeiro
        re.compile(rf"""['"]{re.escape(config.label)}:"""),             # {% url 'financeiro:...' %}
    ]
    if extensao.prefixo_url:
        proibidos.append(re.compile(rf"""['"`]/{re.escape(extensao.prefixo_url)}"""))  # "/gastos/..."
    citacoes = []
    for arquivo in _arquivos_do_nucleo(config.name, config.label):
        if arquivo.name == "base.py" and arquivo.parent.name == "settings":
            continue            # INSTALLED_APPS é onde a extensão é ligada
        for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(linha) for p in proibidos):
                citacoes.append(f"{arquivo.relative_to(PORTAL)}:{n}: {linha.strip()[:100]}")
    assert not citacoes, "o núcleo cita a extensão:\n" + "\n".join(citacoes)


def test_sem_a_extensao_o_registro_fica_vazio_e_o_vocabulario_some():
    sem = [a for a in settings.INSTALLED_APPS if a not in {c.name + ".apps." + type(c).__name__
                                                         for _, c in _extensoes_instaladas()}]
    assert len(sem) < len(settings.INSTALLED_APPS)
    registro.extensoes.cache_clear()
    try:
        with override_settings(INSTALLED_APPS=sem):
            registro.extensoes.cache_clear()
            assert registro.extensoes() == () and registro.menu() == () and registro.consumidores() == ()
            assert not prefixo_conhecido("fin") and prefixo_conhecido("schema")
    finally:
        registro.extensoes.cache_clear()
    assert prefixo_conhecido("fin")


def test_contrato_recusa_piso_desconhecido():
    with pytest.raises(ValueError):
        TipoDado("x:Y", "y", piso="secreto")
    assert isinstance(registro.extensoes()[0], Extensao)

"""Registro de ferramentas: contrato, descoberta de plugins, validação e política (ADR 023, fase 4)."""
import types

import pytest

from ferramentas import (ParametroInvalido, bloqueio_por_politica, carregar, construir, validar_parametros)


async def _ok(**kw):
    return kw


def _dados(**kw):
    return {"id": "minha_ferramenta", "descricao": "d", "executar": _ok,
            "parametros": {"x": {"tipo": "string", "obrigatorio": True}}, **kw}


# ── contrato ─────────────────────────────────────────────────────────────────

def test_ferramenta_valida_ganha_padroes_seguros():
    f = construir(_dados())
    assert f.classificacao_maxima == "publico" and f.timeout_s == 15.0 and f.esquema()["id"] == "minha_ferramenta"
    assert "executar" not in f.esquema()


@pytest.mark.parametrize("ruim", [
    {"id": "tem espaço"}, {"id": ""}, {"executar": "não chamável"}, {"classificacao_maxima": "secreto"},
    {"parametros": {"x": {"tipo": "tuple"}}}])
def test_ferramenta_fora_do_contrato_falha_na_subida(ruim):
    with pytest.raises(ValueError, match="ferramenta inválida"):
        construir(_dados(**ruim))


def test_faltando_campo_obrigatorio_do_contrato():
    with pytest.raises(ValueError, match="ferramenta inválida"):
        construir({"id": "a", "executar": _ok})


# ── descoberta ───────────────────────────────────────────────────────────────

def test_catalogo_junta_as_do_projeto_e_as_do_plugin():
    catalogo = carregar([_dados()], "ia_cep")
    assert set(catalogo) == {"minha_ferramenta", "consultar_cep", "contar_palavras"}


def test_nome_de_entry_point_resolve_para_o_modulo():
    pontos = [types.SimpleNamespace(name="cep", value="ia_cep")]
    assert "consultar_cep" in carregar([], "cep", pontos)


def test_ferramenta_repetida_e_recusada_em_vez_de_sobrescrita():
    with pytest.raises(ValueError, match="repetida"):
        carregar([_dados(id="consultar_cep")], "ia_cep")


def test_plugin_inexistente_falha_na_subida():
    with pytest.raises(ModuleNotFoundError):
        carregar([], "nao_existe")


def test_sem_plugins_so_o_do_projeto():
    assert list(carregar([_dados()], "")) == ["minha_ferramenta"]


# ── parâmetros ───────────────────────────────────────────────────────────────

def test_parametros_conferidos_por_tipo_obrigatoriedade_e_sobras():
    f = construir(_dados(parametros={"x": {"tipo": "string", "obrigatorio": True}, "n": {"tipo": "integer"},
                                     "b": {"tipo": "boolean"}}))
    assert validar_parametros(f, {"x": "a", "n": 3, "b": False}) == {"x": "a", "n": 3, "b": False}
    assert validar_parametros(f, {"x": "a"}) == {"x": "a"}
    for ruim in ({}, {"x": 1}, {"x": "a", "n": "3"}, {"x": "a", "n": True}, {"x": "a", "z": 1}):
        with pytest.raises(ParametroInvalido):
            validar_parametros(f, ruim)


# ── política da ferramenta ───────────────────────────────────────────────────

@pytest.mark.parametrize("maxima,classificacao,bloqueia", [
    ("publico", "publico", False), ("publico", "interno", True), ("publico", "restrito", True),
    ("interno", "interno", False), ("interno", "restrito", True),
    ("confidencial", "confidencial", False),
    ("publico", None, True), ("publico", "inventado", True), ("confidencial", None, False)])
def test_politica_por_classificacao_maxima(maxima, classificacao, bloqueia):
    f = construir(_dados(classificacao_maxima=maxima))
    assert (bloqueio_por_politica(f, classificacao) is not None) is bloqueia

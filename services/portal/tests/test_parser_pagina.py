"""Do schema revisado ao parser (montagem, execução, validação) e a tela de revisão."""
import copy
import json
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extractors import parser_pagina as P
from apps.artifacts.models import Artifact, DocumentText, SchemaPagina, URLPatternCache

TITULO = '<tr><td></td><td class="tituloLancamento">{}</td><td class="tituloLancamento"></td><td class="tituloLancamento"></td></tr>'
LINHA = "<tr><td>{}</td><td>{}</td><td>R$</td><td>{}</td></tr>"


def fatura(itens_a, itens_b, saldo="100,00", total=None, vencimento="28/10/2026"):
    """Fatura sintética: duas seções, uma linha de cotação (informativa) e o saldo anterior (especial)."""
    def valor(v):
        return float(v.replace(".", "").replace(",", "."))
    soma = valor(saldo) + sum(valor(v) for _, _, v in itens_a + itens_b)
    total = total or f"{soma:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    linhas = (
        [LINHA.format("", "SALDO FATURA ANTERIOR", saldo), TITULO.format("Restaurantes")]
        + [LINHA.format(*i) for i in itens_a]
        + ["<tr><td></td><td>Cotação do Dólar de 22/09: R$ 5,31</td><td></td><td></td></tr>",
           TITULO.format("Supermercados")]
        + [LINHA.format(*i) for i in itens_b]
    )
    return f"""<html><body>
<div class="vencimentoFatura"><span class="tituloVencimento">Vencimento</span><br>{vencimento}</div>
<table class="resumo"><tr><td>Saldo anterior</td><td>{saldo}</td></tr>
<tr><td>Total da fatura - R$</td><td>{total}</td></tr></table>
<div class="lancamentos"><table>
<tr><td>Data</td><td>Transações</td><td>Moeda</td><td>Valor</td></tr>
{''.join(linhas)}
</table></div></body></html>"""


A = [("01/09", "BAR UM", "10,00"), ("02/09", "BAR DOIS", "20,50"), ("03/09", "BAR TRES", "5,00"),
     ("04/09", "BAR QUATRO", "1,25")]
B = [("05/09", "MERCADO UM", "100,00"), ("06/09", "MERCADO DOIS", "7,10"), ("07/09", "MERCADO TRES", "3,00"),
     ("08/09", "MERCADO QUATRO", "2,40")]
AMOSTRA = fatura(A, B)
OUTRO_MES = fatura([("01/10", "BAR CINCO", "50,00"), ("02/10", "BAR SEIS", "1,00"), ("03/10", "BAR SETE", "2,00")],
                   [("05/10", "MERCADO OITO", "9,99"), ("06/10", "MERCADO NOVE", "0,01")],
                   saldo="200,00", vencimento="28/11/2026")


def campo(nome, tipo, exemplo, obrigatorio=True):
    return {"nome": nome, "tipo": tipo, "descricao": "", "obrigatorio": obrigatorio, "exemplo": exemplo}


SCHEMA = {
    "descricao": "Fatura de cartão",
    "campos": [campo("vencimento", "data", "28/10/2026"), campo("total_fatura", "valor", "249,25"),
               campo("saldo_anterior", "valor", "100,00"), campo("pagamento_minimo", "valor", "", False)],
    "listas": [{"nome": "lancamentos", "descricao": "", "secoes": True, "estrutura": "lancamentos", "campos": [
        campo("data", "data", "01/09"), campo("transacoes", "texto", "BAR UM"),
        campo("moeda", "texto", "R$"), campo("valor", "valor", "10,00")]}],
    "invariantes": [{"tipo": "soma", "lista": "lancamentos", "campo": "valor", "mais": ["saldo_anterior"],
                     "igual_a": "total_fatura", "descricao": "saldo + lançamentos = total"}],
}


# ── montagem e execução ─────────────────────────────────────────────────────

def test_amostra_extrai_campos_itens_secoes_e_fecha_a_conferencia():
    parser, avisos = P.montar_parser(SCHEMA, AMOSTRA)
    assert parser["listas"][0]["colunas"] == {"data": "data", "transacoes": "transacoes", "moeda": "moeda", "valor": "valor"}
    assert "pagamento_minimo" in parser["sem_locator"] and any("pagamento_minimo" in a for a in avisos)
    r = P.executar(parser, SCHEMA, AMOSTRA)
    assert r["campos"]["vencimento"] == "28/10/2026" and r["campos"]["total_fatura"] == "249,25"
    l = r["listas"]["lancamentos"]
    assert len(l["itens"]) == 8
    assert l["secoes"] == ["Restaurantes", "Supermercados"]          # a cotação não virou seção
    assert l["informativas"] == 1
    assert [i["secao"] for i in l["itens"]] == ["Restaurantes"] * 4 + ["Supermercados"] * 4
    assert [e["transacoes"] for e in l["especiais"]] == ["SALDO FATURA ANTERIOR"]
    assert r["invariantes"][0]["ok"] and r["validacao"] == {"ok": True, "problemas": []}


def test_o_parser_da_amostra_extrai_o_mes_seguinte_sem_llm():
    parser, _ = P.montar_parser(SCHEMA, AMOSTRA)
    parser = json.loads(json.dumps(parser))                         # como volta do banco
    r = P.executar(parser, SCHEMA, OUTRO_MES)
    assert r["campos"]["vencimento"] == "28/11/2026" and r["campos"]["saldo_anterior"] == "200,00"
    assert [i["transacoes"] for i in r["listas"]["lancamentos"]["itens"]][:2] == ["BAR CINCO", "BAR SEIS"]
    assert r["validacao"]["ok"]


def test_conferencia_que_nao_fecha_invalida():
    parser, _ = P.montar_parser(SCHEMA, AMOSTRA)
    errada = fatura(A, B, total="999,99")
    r = P.executar(parser, SCHEMA, errada)
    assert not r["invariantes"][0]["ok"]
    assert not r["validacao"]["ok"] and any("não fecha" in p for p in r["validacao"]["problemas"])


def test_layout_mudado_invalida_em_vez_de_devolver_dado_errado():
    parser, _ = P.montar_parser(SCHEMA, AMOSTRA)
    mudada = OUTRO_MES.replace("Vencimento", "Data limite").replace('class="lancamentos"', 'class="extrato"')
    r = P.executar(parser, SCHEMA, mudada)
    assert r["campos"]["vencimento"] == ""
    problemas = r["validacao"]["problemas"]
    assert "campo obrigatório 'vencimento' vazio" in problemas and "lista 'lancamentos' sem itens" in problemas


def test_exemplo_que_nao_esta_na_pagina_vira_aviso():
    schema = copy.deepcopy(SCHEMA)
    schema["campos"][0]["exemplo"] = "31/12/1999"
    parser, avisos = P.montar_parser(schema, AMOSTRA)
    assert "vencimento" in parser["sem_locator"] and any("não localizado" in a for a in avisos)
    assert not P.executar(parser, schema, AMOSTRA)["validacao"]["ok"]


def test_lista_sem_estrutura_que_case_e_avisada():
    schema = copy.deepcopy(SCHEMA)
    schema["listas"][0]["campos"] = [campo("x", "texto", "NAO EXISTE")]
    schema["invariantes"] = []
    parser, avisos = P.montar_parser(schema, AMOSTRA)
    assert parser["listas"] == [] and any("nenhuma estrutura" in a for a in avisos)
    assert "lista 'lancamentos' sem estrutura ligada" in P.executar(parser, schema, AMOSTRA)["validacao"]["problemas"]


# ── tela de revisão ─────────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _rascunho(org, schema=SCHEMA, versao=1, status=SchemaPagina.Status.RASCUNHO):
    padrao = URLPatternCache.objects.get_or_create(
        tenant=org, domain="banco.example", path_pattern="/fatura", structure_fingerprint="f",
        defaults={"page_type": "tabular_generico", "confidence": 0.9},
    )[0]
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"mhtml_path": "x"},
    )
    DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", url_pattern_cache=padrao)
    return SchemaPagina.objects.create(
        tenant=org, padrao=padrao, dominio=padrao.domain, caminho=padrao.path_pattern, amostra=artefato, versao=versao, status=status, schema=schema,
        classification_level="restrito", provider="ollama", model_name="qwen2.5:7b",
    )


@pytest.fixture
def amostra():
    with mock.patch("apps.artifacts.tasks._ler_mhtml", return_value=b""), \
         mock.patch("apps.artifacts.tasks._html_do_mhtml", return_value=(AMOSTRA, "utf-8")):
        yield


def _post(client, url, corpo=None):
    return client.post(url, json.dumps(corpo) if corpo is not None else "", content_type="application/json")


def test_tela_abre_e_previa_usa_o_schema_da_tela(client, org, amostra):
    client.force_login(org.owner)
    r = _rascunho(org)
    assert client.get(f"/artifacts/schemas/{r.id}/").status_code == 200
    editado = copy.deepcopy(SCHEMA)
    editado["listas"][0]["campos"][1]["nome"] = "Descrição"                # renomear na tela
    resp = _post(client, f"/artifacts/schemas/{r.id}/previa/", {"schema": editado}).json()
    assert resp["schema"]["listas"][0]["campos"][1]["nome"] == "descricao"
    assert resp["resultado"]["listas"]["lancamentos"]["itens"][0]["descricao"] == "BAR UM"
    assert resp["localizados"]["vencimento"]["strategy"] == "prefix"
    r.refresh_from_db()
    assert r.schema == SCHEMA                                             # prévia não salva


def test_salvar_e_aprovar_substitui_o_aprovado_anterior(client, org, amostra):
    client.force_login(org.owner)
    antigo = _rascunho(org, versao=1, status=SchemaPagina.Status.APROVADO)
    r = _rascunho(org, versao=2)
    assert _post(client, f"/artifacts/schemas/{r.id}/salvar/", {"schema": SCHEMA}).status_code == 200
    resp = _post(client, f"/artifacts/schemas/{r.id}/aprovar/", {"substitui": str(antigo.id)})
    assert resp.status_code == 200 and resp.json()["substituidos"] == ["1"]
    r.refresh_from_db(); antigo.refresh_from_db()
    assert r.status == SchemaPagina.Status.APROVADO and r.parser["listas"] and r.aprovado_por == org.owner
    assert antigo.status == SchemaPagina.Status.SUBSTITUIDO
    assert _post(client, f"/artifacts/schemas/{r.id}/salvar/", {"schema": SCHEMA}).status_code == 400


def test_aprovar_sem_substituir_deixa_os_dois_valendo(client, org, amostra):
    # Fatura e extrato no mesmo endereço: aprovar um não tira o outro.
    client.force_login(org.owner)
    outro = _rascunho(org, versao=1, status=SchemaPagina.Status.APROVADO)
    r = _rascunho(org, versao=2)
    assert _post(client, f"/artifacts/schemas/{r.id}/aprovar/").json()["substituidos"] == []
    outro.refresh_from_db()
    assert outro.status == SchemaPagina.Status.APROVADO


def test_so_substitui_aprovado_da_mesma_familia(client, org, amostra):
    client.force_login(org.owner)
    r = _rascunho(org, versao=2)
    rascunho = _rascunho(org, versao=3)
    assert _post(client, f"/artifacts/schemas/{r.id}/aprovar/", {"substitui": str(rascunho.id)}).status_code == 400


def test_nao_aprova_o_que_nao_valida(client, org, amostra):
    client.force_login(org.owner)
    errado = copy.deepcopy(SCHEMA)
    errado["invariantes"][0]["mais"] = []                                 # sem o saldo, não fecha
    r = _rascunho(org, schema=errado)
    resp = _post(client, f"/artifacts/schemas/{r.id}/aprovar/")
    assert resp.status_code == 400 and any("não fecha" in p for p in resp.json()["problemas"])
    r.refresh_from_db()
    assert r.status == SchemaPagina.Status.RASCUNHO and r.parser is None


def test_aprovar_e_de_dono_ou_admin(client, org, amostra):
    membro = User.objects.create_user(username="m", password="x")
    Membership.objects.create(user=membro, organization=org, role=Membership.Role.MEMBER)
    client.force_login(membro)
    r = _rascunho(org)
    assert _post(client, f"/artifacts/schemas/{r.id}/salvar/", {"schema": SCHEMA}).status_code == 200
    assert _post(client, f"/artifacts/schemas/{r.id}/aprovar/").status_code == 403


def test_nova_versao_copia_o_aprovado_para_um_rascunho(client, org):
    client.force_login(org.owner)
    aprovado = _rascunho(org, status=SchemaPagina.Status.APROVADO)
    resp = _post(client, f"/artifacts/schemas/{aprovado.id}/nova-versao/").json()
    nova = SchemaPagina.objects.get(id=resp["id"])
    assert nova.versao == 2 and nova.status == SchemaPagina.Status.RASCUNHO
    assert nova.schema == aprovado.schema and nova.derivado_de == aprovado


def test_tela_isola_organizacao(client, org, amostra):
    r = _rascunho(org)
    estranho = User.objects.create_user(username="e", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert client.get(f"/artifacts/schemas/{r.id}/").status_code == 404
    for acao in ("previa", "salvar", "aprovar", "nova-versao"):
        assert _post(client, f"/artifacts/schemas/{r.id}/{acao}/", {"schema": SCHEMA}).status_code == 404

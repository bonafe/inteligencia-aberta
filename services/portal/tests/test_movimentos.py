"""Movimentos de contas (ADR 021, fase 1): papéis e convenção do schema, conta, competência e parcela."""
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extractors.schema_pagina import normalizar_schema
from apps.artifacts.models import Artifact, DocumentText, ExtracaoPagina, SchemaPagina, URLPatternCache
from apps.financeiro import lancamentos as L
from apps.financeiro.models import Conta, Movimento

pytestmark = pytest.mark.django_db
HASH = "ni:///sha-256;" + "A" * 43


def c(nome, papel, tipo="texto", exemplo="x"):
    return {"nome": nome, "tipo": tipo, "papel": papel, "descricao": "", "obrigatorio": True, "exemplo": exemplo}


FATURA = {
    "descricao": "Fatura de cartão", "tipo_documento": "fatura_cartao", "convencao": "positivo_e_saida",
    "campos": [c("vencimento", "vencimento", "data"), c("total", "total_documento", "valor"),
               c("numero_cartao", "identifica_conta"), c("titular", "titular")],
    "listas": [{"nome": "lancamentos", "descricao": "", "secoes": True, "estrutura": None, "campos": [
        c("data", "data", "data"), c("transacoes", "descricao"), c("moeda", ""), c("valor", "valor", "valor")]}],
    "invariantes": [],
}
DADOS_FATURA = {
    "campos": {"vencimento": "28/10/2026", "total": "200,00", "numero_cartao": "Nº 6516.XXXX.XXXX.1402",
               "titular": "FULANO"},
    "listas": {"lancamentos": {"itens": [
        {"data": "13/05", "transacoes": "Vindi Catum PARC 06/12 Campinas BR", "moeda": "R$", "valor": "10,43",
         "secao": "Compras parceladas"},
        {"data": "02/10", "transacoes": "PADARIA", "moeda": "R$", "valor": "189,57", "secao": "Restaurantes"},
        {"data": "28/09", "transacoes": "PGTO DEBITO CONTA", "moeda": "R$", "valor": "-150,00", "secao": "Pagamentos"},
    ], "secoes": [], "informativas": 0, "especiais": []}},
}
EXTRATO = {
    "descricao": "Extrato de conta corrente", "tipo_documento": "extrato_conta", "convencao": "sufixo_dc",
    "campos": [c("conta", "identifica_conta"), c("saldo_anterior", "saldo_anterior", "valor")],
    "listas": [{"nome": "movimentos", "descricao": "", "secoes": False, "estrutura": None, "campos": [
        c("data_movim", "data", "data"), c("historico", "descricao"), c("historico_2", "complemento"),
        c("documento", "documento"), c("valor", "valor", "valor"), c("saldo", "saldo_linha")]}],
    "invariantes": [],
}
DADOS_EXTRATO = {
    "campos": {"conta": "1234-5 / 67890-1", "saldo_anterior": "2.825,71 D"},
    "listas": {"movimentos": {"itens": [
        {"data_movim": "01/10/2026", "historico": "Pix - Recebido", "historico_2": "MARIA", "documento": "1",
         "valor": "300,00 C", "saldo": ""},
        {"data_movim": "01/10/2026", "historico": "Pix - Enviado", "historico_2": "ANA", "documento": "2",
         "valor": "400,00 D", "saldo": ""},
        {"data_movim": "28/10/2026", "historico": "Pagto cartão crédito", "historico_2": "ELO NANQUIM",
         "documento": "3", "valor": "13.405,63 D", "saldo": ""},
        {"data_movim": "30/10/2026", "historico": "S A L D O", "historico_2": "", "documento": "", "valor": "",
         "saldo": "6.430,02 D"},
    ], "secoes": [], "informativas": 0, "especiais": []}},
}


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _registrar(org, schema, dados, versao=1):
    padrao = URLPatternCache.objects.get_or_create(
        tenant=org, domain="www2.bb.com.br", path_pattern="/aapf/principal.jsp", structure_fingerprint="f",
        defaults={"page_type": "x", "confidence": 0.9})[0]
    s = SchemaPagina.objects.create(
        tenant=org, padrao=padrao, dominio="www2.bb.com.br", caminho="/aapf/principal.jsp", versao=versao,
        status=SchemaPagina.Status.APROVADO, schema=schema, parser={}, classification_level="restrito",
        provider="ollama", model_name="m")
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://www2.bb.com.br/aapf/principal.jsp"},
        blob_hash=HASH[:-2] + f"{versao:02d}")
    doc = DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", url_pattern_cache=padrao)
    e = ExtracaoPagina.objects.create(tenant=org, document_text=doc, schema=s, status="valido", origem="manual",
                                      dados=dados, classification_level="restrito")
    L.registrar_extracao(e)
    return artefato


# ── normalização dos papéis ─────────────────────────────────────────────────

def test_normalizar_aceita_papeis_e_avisa_o_que_nao_conhece():
    bruto = {**FATURA, "convencao": "ao_contrario", "campos": FATURA["campos"] + [c("outro_total", "total_documento")]}
    bruto["listas"] = [{**FATURA["listas"][0], "campos": FATURA["listas"][0]["campos"] + [c("x", "inventado")]}]
    schema, avisos = normalizar_schema(bruto)
    assert schema["tipo_documento"] == "fatura_cartao" and schema["convencao"] == ""
    assert [x["papel"] for x in schema["campos"]] == ["vencimento", "total_documento", "identifica_conta", "titular", ""]
    assert schema["listas"][0]["campos"][-1]["papel"] == ""
    assert any("ao_contrario" in a for a in avisos) and any("já é de outro campo" in a for a in avisos)
    assert any("inventado" in a for a in avisos)


def test_normalizar_avisa_convencao_sem_as_colunas_que_ela_pede():
    schema, avisos = normalizar_schema({**EXTRATO, "convencao": "colunas_debito_credito"})
    assert any("débito" in a or "debito" in a for a in avisos)


# ── leitura pelos papéis ────────────────────────────────────────────────────

def test_linhas_do_schema_junta_complemento_e_pula_linha_sem_valor():
    linhas, ctx = L.linhas_do_schema(DADOS_EXTRATO, EXTRATO)
    assert [l.descricao for l in linhas] == ["Pix - Recebido MARIA", "Pix - Enviado ANA", "Pagto cartão crédito ELO NANQUIM"]
    assert [l.documento for l in linhas] == ["1", "2", "3"]
    assert ctx == {"periodo": "", "conta": "1234-5 / 67890-1", "titular": ""}


def test_colunas_debito_credito_viram_valor_e_sentido():
    schema = {"listas": [{"nome": "m", "campos": [c("data", "data"), c("hist", "descricao"),
                                                   c("deb", "debito"), c("cred", "credito")]}], "campos": []}
    dados = {"listas": {"m": {"itens": [{"data": "01/10/2026", "hist": "LUZ", "deb": "90,00", "cred": ""},
                                        {"data": "02/10/2026", "hist": "SALARIO", "deb": "", "cred": "5.000,00"}]}}}
    linhas, _ = L.linhas_do_schema(dados, schema)
    assert [(l.valor, l.sentido) for l in linhas] == [("90,00", "D"), ("5.000,00", "C")]


# ── projeção: conta, sinal, natureza, competência, parcela ──────────────────

def test_fatura_com_papeis_cria_a_conta_e_preenche_competencia_e_parcela(org):
    _registrar(org, FATURA, DADOS_FATURA)
    conta = Conta.objects.get()
    assert conta.tipo == "cartao" and conta.identificadores == ["Nº 6516.XXXX.XXXX.1402"] and conta.titular == "FULANO"
    assert conta.nome.endswith("1402")
    m = {x.descricao.split()[0]: x for x in Movimento.objects.all()}
    assert m["Vindi"].competencia == date(2026, 10, 1) and m["Vindi"].data == date(2026, 5, 13)
    assert (m["Vindi"].parcela, m["Vindi"].parcelas) == (6, 12)
    assert m["PADARIA"].valor == Decimal("-189.57") and m["PADARIA"].natureza == "compra"
    assert m["PGTO"].valor == Decimal("150.00") and m["PGTO"].natureza == "pagamento_fatura"


def test_extrato_com_sufixo_dc(org):
    _registrar(org, EXTRATO, DADOS_EXTRATO)
    conta = Conta.objects.get()
    assert conta.tipo == "corrente" and conta.identificadores == ["1234-5 / 67890-1"]
    m = {x.documento: x for x in Movimento.objects.all()}
    assert m["1"].valor == Decimal("300.00") and m["1"].natureza == "entrada" and m["1"].estabelecimento is None
    assert m["2"].valor == Decimal("-400.00") and m["2"].natureza == "saida_terceiros"
    assert m["3"].natureza == "pagamento_fatura"                # não é gasto: a fatura já foi contada
    assert m["2"].competencia == date(2026, 10, 1)


def test_o_mesmo_identificador_cai_na_mesma_conta_e_juntar_leva_os_movimentos(org):
    _registrar(org, FATURA, DADOS_FATURA, versao=1)
    sem_numero = dict(DADOS_FATURA, campos={**DADOS_FATURA["campos"], "numero_cartao": ""})
    _registrar(org, FATURA, sem_numero, versao=2)
    assert Conta.objects.count() == 2
    numerada = Conta.objects.get(identificadores__contains=["Nº 6516.XXXX.XXXX.1402"])
    sem = Conta.objects.exclude(pk=numerada.pk).get()
    L.juntar_contas(numerada, sem)
    assert Conta.objects.count() == 1 and Movimento.objects.filter(conta=numerada).count() == 6
    assert L.reconstruir(org) == 6 and Conta.objects.count() == 1    # a reconstrução respeita a junção


def test_tela_filtra_por_conta_e_soma_por_competencia(client, org):
    client.force_login(org.owner)
    _registrar(org, FATURA, DADOS_FATURA)
    conta = Conta.objects.get()
    resp = client.get(f"/gastos/?conta={conta.pk}")
    assert resp.status_code == 200 and resp.context["total"] == Decimal("200.00")
    assert client.get("/gastos/?de=2026-10&ate=2026-10").context["total"] == Decimal("200.00")
    assert client.get("/gastos/?de=2026-05&ate=2026-05").context["total"] == 0     # a compra foi em maio
    client.post("/gastos/", {"organizacao": org.pk, "acao": "apelido_conta", "conta": conta.pk, "apelido": "Ourocard"})
    conta.refresh_from_db()
    assert conta.nome == "Ourocard"

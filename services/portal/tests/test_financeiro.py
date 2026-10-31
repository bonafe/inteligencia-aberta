"""Gastos: estruturação → alegações → `Lancamento`, categorias por estabelecimento e a tela `/gastos/`."""
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact, Claim, DocumentText, EstruturacaoLLM
from apps.financeiro import lancamentos as L
from apps.financeiro.models import CategoriaGasto, Estabelecimento, Lancamento

HASH = "ni:///sha-256;" + "A" * 43

FATURA = {
    "emissor": "Banco do Brasil",
    "período": "Out/26",
    "detalhamento_fatura": {
        "cartão": "OUROCARD",
        "lancamentos": [
            {"data": "28/09", "moeda": "R$", "valor": "R$ -9.964,56", "transação": "PGTO DEBITO CONTA 6937 000000253 200211"},
            {"data": "29/09", "moeda": "R$", "valor": "R$ 132,01", "transação": "HortifrutiSanta IBIRAREMA BR"},
            {"data": "02/10", "moeda": "R$", "valor": "R$ 19,48", "transação": "HortifrutiSanta IBIRAREMA BR"},
            {"data": "22/09", "transação": "*** 20,00 DOLAR AMERICANO, Cotação do Dólar de 22/09: R$ 5,3162"},
            {"data": "23/09", "moeda": "R$", "valor": "R$ 1,16", "transação": "IOF - COMPRA NO EXTERIOR"},
            {"data": "24/09", "moeda": "R$", "valor": "R$ -10,00", "transação": "ESTORNO LOJA X"},
            {"data": "23/09", "moeda": "R$", "valor": "R$ 203,49", "transação": "NAKAYOSHI INDAIATUBA BR"},
            {"data": "23/09", "moeda": "R$", "valor": "R$ 24,90", "transação": "NAKAYOSHI PRODUTOS ORI INDAIATUBA BR"},
        ],
    },
}


# ── parsing puro ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("R$ 93,50", Decimal("93.50")),
    ("R$ -9.964,56", Decimal("-9964.56")),
    ("1.234", Decimal("1234.00")),
    ("12.50", Decimal("12.50")),
    ("(9,00)", Decimal("-9.00")),
    ("9,00-", Decimal("-9.00")),
    ("", None),
    ("R$", None),
])
def test_parse_valor(texto, esperado):
    assert L.parse_valor(texto) == esperado


@pytest.mark.parametrize("periodo,esperado", [
    ("Out/26", date(2026, 10, 31)),
    ("outubro de 2026", date(2026, 10, 31)),
    ("02/2027", date(2027, 2, 28)),
    ("15/10/2026", date(2026, 10, 15)),
    ("vence em 15/10/26", date(2026, 10, 15)),
    ("16/09 a 15/10", None),
    ("", None),
])
def test_data_referencia(periodo, esperado):
    assert L.data_referencia(periodo) == esperado


def test_parse_data_infere_o_ano_e_trata_a_virada():
    out26 = date(2026, 10, 31)
    assert L.parse_data("16/09", out26) == date(2026, 9, 16)
    assert L.parse_data("15/12", out26) == date(2025, 12, 15)       # parcela de compra antiga
    jan27 = date(2027, 1, 31)
    assert L.parse_data("15/12", jan27) == date(2026, 12, 15)
    assert L.parse_data("05/01", jan27) == date(2027, 1, 5)
    assert L.parse_data("16/09/2025", out26) == date(2025, 9, 16)
    assert L.parse_data("2026-09-16", out26) == date(2026, 9, 16)
    assert L.parse_data("31/02", out26) is None
    assert L.parse_data("ontem", out26) is None


@pytest.mark.parametrize("descricao,esperado", [
    ("MELFOOD COMERCIO DE A CAMPINAS BR", "MELFOOD COMERCIO DE A CAMPINAS"),
    ("HortifrutiSanta IBIRAREMA BR", "HORTIFRUTISANTA IBIRAREMA"),
    ("OPENAI *CHATGPT SUBSCR 4158799686 CA", "OPENAI CHATGPT SUBSCR"),
    ("LINODE . AKAMAI 855-454-6633 MA", "LINODE . AKAMAI"),
    ("LOJA X PARC 02/10", "LOJA X"),
    ("Café São João", "CAFE SAO JOAO"),
])
def test_normalizar_descritor(descricao, esperado):
    assert L.normalizar_descritor(descricao) == esperado


def test_tipo_do_lancamento():
    assert L.tipo_do_lancamento("PGTO DEBITO CONTA", Decimal("-10")) == Lancamento.Tipo.PAGAMENTO
    assert L.tipo_do_lancamento("LOJA", Decimal("-10")) == Lancamento.Tipo.ESTORNO
    assert L.tipo_do_lancamento("IOF - COMPRA NO EXTERIOR", Decimal("1")) == Lancamento.Tipo.TARIFA
    assert L.tipo_do_lancamento("PADARIA", Decimal("1")) == Lancamento.Tipo.COMPRA


def test_extrair_linhas_acha_a_lista_e_o_periodo_e_pula_a_cotacao():
    linhas, periodo = L.extrair_linhas(FATURA)
    assert periodo == "Out/26"
    assert len(linhas) == 7                                     # a linha da cotação não tem valor
    assert [l.indice for l in linhas] == list(range(7))
    assert linhas[1].descricao == "HortifrutiSanta IBIRAREMA BR"
    assert linhas[1].valor == "R$ 132,01"
    assert linhas[1].secao == ""                                # "lancamentos" é nome genérico


def test_extrair_linhas_usa_o_nome_da_lista_como_secao_e_aceita_outras_chaves():
    dados = {
        "compras_nacionais": [{"data_compra": "01/10", "valor_rs": "10,00", "historico": "PADARIA"}],
        "compras_internacionais": [{"data": "02/10", "valor": "5,00", "valor_us": "1,00", "descricao": "APP"}],
    }
    linhas, _ = L.extrair_linhas(dados)
    assert [(l.secao, l.descricao, l.valor) for l in linhas] == [
        ("compras_nacionais", "PADARIA", "10,00"), ("compras_internacionais", "APP", "5,00"),
    ]


def test_extrair_linhas_sem_lista():
    assert L.extrair_linhas({"titulo": "notícia"}) == ([], "")


# ── registro e projeção ─────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _estruturacao(org, dados=FATURA, *, blob_hash=HASH, modelo="qwen2.5:7b"):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://www2.bb.com.br/fatura"}, blob_hash=blob_hash,
    )
    doc = DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", title="Fatura BB")
    return EstruturacaoLLM.objects.create(
        document_text=doc, tenant=org, provider="ollama", model_name=modelo,
        status=EstruturacaoLLM.Status.CONCLUIDO, structured_data=dados,
    )


def _outra(estruturacao, modelo):
    return EstruturacaoLLM.objects.create(
        document_text=estruturacao.document_text, tenant=estruturacao.tenant, provider="ollama",
        model_name=modelo, status=EstruturacaoLLM.Status.CONCLUIDO, structured_data=estruturacao.structured_data,
    )


def test_registrar_cria_alegacoes_com_evidencia_e_projeta(org):
    e = _estruturacao(org)
    r = L.registrar(e)
    assert r.lancamentos == 7

    claims = Claim.objects.filter(produtor=L.PRODUTOR)
    assert claims.filter(predicado="ia:periodo").count() == 1
    assert claims.filter(predicado="ia:valor").count() == 7
    c = claims.filter(predicado="ia:descricao").first()
    assert c.autor_ref == "dominio:www2.bb.com.br"
    assert c.modelo == "ollama:qwen2.5:7b"
    assert c.classification_level == "restrito"
    ev = c.evidencias.get()
    assert ev.blob_hash == HASH and ev.localizador["estruturacao"] == str(e.id)

    ls = {l.indice: l for l in Lancamento.objects.all()}
    assert len(ls) == 7
    assert ls[0].tipo == Lancamento.Tipo.PAGAMENTO and ls[0].estabelecimento is None
    assert ls[1].data == date(2026, 9, 29) and ls[1].valor == Decimal("132.01")
    assert ls[1].estabelecimento == ls[2].estabelecimento       # mesmo descritor, mesmo lugar
    assert ls[3].tipo == Lancamento.Tipo.TARIFA
    assert ls[4].tipo == Lancamento.Tipo.ESTORNO


def test_registrar_e_idempotente(org):
    e = _estruturacao(org)
    L.registrar(e)
    total = Claim.objects.count()
    r = L.registrar(e)
    assert r.alegacoes_criadas == 0
    assert Claim.objects.count() == total
    assert Lancamento.objects.count() == 7


def test_outra_estruturacao_retrata_a_anterior_sem_duplicar_gastos(org):
    e1 = _estruturacao(org)
    L.registrar(e1)
    e2 = _outra(e1, "llama3.1:8b")
    r = L.registrar(e2)
    assert r.retratadas == Claim.objects.filter(sujeito_ref__contains=str(e1.id)).count() > 0
    assert not Claim.objects.filter(sujeito_ref__contains=str(e1.id), estado=Claim.Estado.ATIVA).exists()
    assert Lancamento.objects.count() == 7
    assert set(Lancamento.objects.values_list("estruturacao_id", flat=True)) == {e2.id}


def test_registrar_recusa_sem_hash_ou_nao_concluida(org):
    with pytest.raises(ValueError):
        L.registrar(_estruturacao(org, blob_hash=""))
    e = _estruturacao(org)
    e.status = EstruturacaoLLM.Status.EXECUTANDO
    with pytest.raises(ValueError):
        L.registrar(e)


def test_registrar_sem_lancamentos_e_vazio(org):
    r = L.registrar(_estruturacao(org, {"titulo": "notícia"}))
    assert r.lancamentos == 0
    assert not Claim.objects.exists()


def test_reconstruir_mantem_fusao_e_categoria(org):
    L.registrar(_estruturacao(org))
    naka = Estabelecimento.objects.get(nome__startswith="Nakayoshi Indaiatuba")
    variante = Estabelecimento.objects.get(nome__startswith="Nakayoshi Produtos")
    mercado = CategoriaGasto.objects.create(tenant=org, nome="Mercado")
    naka.categoria = mercado
    naka.save()
    L.fundir(naka, variante)

    Lancamento.objects.all().delete()
    assert L.reconstruir(org) == 7
    gastos_naka = Lancamento.objects.filter(estabelecimento=naka)
    assert gastos_naka.count() == 2
    assert {l.estabelecimento.categoria for l in gastos_naka} == {mercado}


def test_fundir_recusa_outra_organizacao(org):
    outro = User.objects.create_user(username="outro", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=outro)
    a = Estabelecimento.objects.create(tenant=org, nome="A")
    b = Estabelecimento.objects.create(tenant=org2, nome="B")
    with pytest.raises(ValueError):
        L.fundir(a, b)


# ── tela ────────────────────────────────────────────────────────────────────

def test_view_registrar_e_somar_por_categoria(client, org):
    client.force_login(org.owner)
    e = _estruturacao(org)
    resp = client.post(f"/gastos/registrar/{e.id}/")
    assert resp.status_code == 200 and resp.json()["lancamentos"] == 7

    resp = client.get("/gastos/")
    assert resp.status_code == 200
    # 132,01 + 19,48 + 1,16 − 10,00 + 203,49 + 24,90 (pagamento fora)
    assert resp.context["total"] == Decimal("371.04")
    assert CategoriaGasto.objects.filter(tenant=org).count() == len(L.CATEGORIAS_PADRAO)

    hortifruti = Estabelecimento.objects.get(nome__startswith="Hortifrutisanta")
    cat = CategoriaGasto.objects.get(tenant=org, nome="Mercado")
    client.post("/gastos/", {"organizacao": org.pk, "acao": "categoria",
                             "estabelecimento": hortifruti.pk, "categoria": cat.pk})
    resp = client.get(f"/gastos/?categoria={cat.pk}")
    linhas = {c["estabelecimento__categoria__nome"]: c["total"] for c in resp.context["por_categoria"]}
    assert linhas["Mercado"] == Decimal("151.49")
    assert [l.indice for l in resp.context["lista"]] == [1, 2]

    resp = client.get("/gastos/?de=2026-10&ate=2026-10")
    assert resp.context["total"] == Decimal("19.48")


def test_view_isola_organizacao(client, org):
    e = _estruturacao(org)
    estranho = User.objects.create_user(username="estranho", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert client.post(f"/gastos/registrar/{e.id}/").status_code == 404
    L.registrar(e)
    est = Estabelecimento.objects.filter(tenant=org).first()
    assert client.get(f"/gastos/?estabelecimento={est.pk}").status_code == 404
    assert client.get("/gastos/").context["total"] == 0

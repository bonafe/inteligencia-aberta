"""Gastos: sugestão de categoria (`categorias.py`), renormalização de descritores e exportar/importar
o estado da pessoa (`estado.py`), com as telas `/gastos/categorizar/` e `/gastos/estado/`."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Membership, Organization, User
from apps.financeiro import categorias as C
from apps.financeiro import estado as E
from apps.financeiro import lancamentos as L
from apps.financeiro.models import (
    CategoriaGasto, CompraParcelada, Conta, DescritorEstabelecimento, Estabelecimento, Movimento,
)
from tests.test_financeiro import FATURA, _estruturacao, org  # noqa: F401 (fixture)

CATS = ["Mercado", "Restaurante e bar", "Café e lanche", "Transporte", "Pet", "Hospedagem", "Viagem e hospedagem",
        "Compras Online", "Assinaturas e software", "Farmácia e saúde", "Construção"]


# ── sugestão (puro) ─────────────────────────────────────────────────────────

def test_vizinho_nas_duas_primeiras_palavras_e_alta():
    s = C.Sugeridor([("Pet", ["AGROCOC COMERCIO DE RA INDAIATUBA"])], CATS)
    assert s.sugerir(["AGROCOC COMERCIO INDAIATUBA"]) == C.Sugestao("Pet", C.ALTA, s.sugerir(
        ["AGROCOC COMERCIO INDAIATUBA"]).motivo)
    # Só a primeira palavra (um nome próprio, "GERALDO") não basta para aplicar sozinho.
    s = C.Sugeridor([("Construção", ["GERALDO FERRAGENS CAMPINAS"])], CATS)
    assert s.sugerir(["GERALDO SPORT CAMPINAS"]).confianca == C.MEDIA


def test_vizinhos_que_discordam_e_prefixo_de_maquininha():
    s = C.Sugeridor([("Pet", ["MP NUTRI ANIMAL"]), ("Mercado", ["NUTRI VERDE LTDA"])], CATS)
    assert s.sugerir(["NUTRI ANIMAL CENTRO"]).confianca == C.MEDIA     # dois vizinhos, duas categorias
    s = C.Sugeridor([("Restaurante e bar", ["MP SABOR DA TERRA OSASCO"])], CATS)
    assert s.sugerir(["SABOR DA TERRA CAMPINAS"]).categoria == "Restaurante e bar"  # "MP" não conta


def test_palavra_generica_nao_faz_vizinho():
    s = C.Sugeridor([("Construção", ["CASA DOS PARAFUSOS"])], CATS)
    assert s.sugerir(["CASA DO LAVRA INDAIATUBA"]) is None


@pytest.mark.parametrize("descritor,categoria,confianca", [
    ("SERV POSTO OASIS LTDA CAMPINAS", "Transporte", C.ALTA),
    ("DROGARIA SAO PAULO OURINHOS", "Farmácia e saúde", C.ALTA),
    ("MP MERCADOLIVRE TAUBATE", "Compras Online", C.ALTA),          # antes de "MERCADO"
    ("AMAZONPRIMEBR SAO PAULO", "Assinaturas e software", C.ALTA),  # antes de "AMAZON"
    ("AUTO ELETRICA IBIRAREMA", "Transporte", C.ALTA),              # antes de "ELETRICA"
    ("HDI SEGUROS SA SAO PAULO", "Transporte", C.MEDIA),
    ("TEC LAB INDAIATUBA", "Farmácia e saúde", C.MEDIA),
    ("PINHEIRORACOES IBIRAREMA", "Pet", C.MEDIA),                    # colada
    ("RAIA2199 INDAIATUBA", "Farmácia e saúde", C.MEDIA),
])
def test_tema(descritor, categoria, confianca):
    s = C.Sugeridor([], CATS)
    assert (s.sugerir([descritor]).categoria, s.sugerir([descritor]).confianca) == (categoria, confianca)


@pytest.mark.parametrize("descritor", [
    "PETROCAR OURINHOS", "BA DECORACAO E ARTESANACAMANDUCAIA", "NOVA GERACAO BEER AND OURINHOS",
    "NONATO VIEI OURINHOS",
])
def test_sem_sugestao(descritor):
    assert C.Sugeridor([], CATS).sugerir([descritor]) is None


def test_tema_escolhe_a_categoria_que_a_pessoa_usa():
    sem_uso = C.Sugeridor([], CATS)
    assert sem_uso.sugerir(["PADARIA BARAO CAMPINAS"]).categoria == "Café e lanche"
    quem_poe_cafe_em_restaurante = C.Sugeridor([("Restaurante e bar", ["1727 COFFEE ROASTERS"])], CATS)
    assert quem_poe_cafe_em_restaurante.sugerir(["PADARIA BARAO CAMPINAS"]).categoria == "Restaurante e bar"
    # Candidata que não existe na organização não é sugerida.
    assert C.Sugeridor([], ["Mercado"]).sugerir(["HOTEL EXPRESS GOIANIA"]) is None


# ── parcela e renormalização ────────────────────────────────────────────────

def test_parcela_sem_parc_e_descritor_sem_ela():
    assert L.parcela_da_descricao("ALLIANZ SEGU 02 de 10 SAO PAULO BR") == (2, 10)
    assert L.parcela_da_descricao("PADARIA 7 DE SETEMBRO") is None
    assert L.normalizar_descritor("ALLIANZ SEGU 02 de 10 SAO PAULO BR") == "ALLIANZ SEGU SAO PAULO"
    assert L.normalizar_descritor("ALLIANZ SEGU 1 de 10 SAO PAULO BR") == "ALLIANZ SEGU SAO PAULO"


def test_reconstruir_junta_quem_a_normalizacao_nova_iguala(org):
    L.registrar(_estruturacao(org))
    transporte = CategoriaGasto.objects.create(tenant=org, nome="Transporte")
    # Como estava antes da normalização nova: um estabelecimento por parcela, cada um categorizado.
    m = Movimento.objects.filter(tenant=org, estabelecimento__isnull=False).first()
    velhos = []
    for n in (1, 2):
        est = Estabelecimento.objects.create(tenant=org, nome=f"Allianz Segu {n} De 10 Sao Paulo",
                                             categoria=transporte if n == 1 else None)
        DescritorEstabelecimento.objects.create(tenant=org, descritor=f"ALLIANZ SEGU {n} DE 10 SAO PAULO",
                                                estabelecimento=est)
        velhos.append(est)
    Movimento.objects.filter(pk=m.pk).update(descricao="ALLIANZ SEGU 1 de 10 SAO PAULO BR", estabelecimento=velhos[0])
    m2 = Movimento.objects.filter(tenant=org, estabelecimento__isnull=False).exclude(pk=m.pk).first()
    Movimento.objects.filter(pk=m2.pk).update(descricao="ALLIANZ SEGU 02 de 10 SAO PAULO BR",
                                              estabelecimento=velhos[1])

    assert L.renormalizar_descritores(Movimento.objects.filter(tenant=org)) == 1
    est = DescritorEstabelecimento.objects.get(tenant=org, descritor="ALLIANZ SEGU SAO PAULO").estabelecimento
    assert est.categoria == transporte and est.nome == "Allianz Segu Sao Paulo"
    assert est == velhos[0] and not Estabelecimento.objects.filter(pk=velhos[1].pk).exists()   # fica o mais antigo


def test_fundir_leva_a_compra_parcelada(org):
    a = Estabelecimento.objects.create(tenant=org, nome="A")
    b = Estabelecimento.objects.create(tenant=org, nome="B")
    from datetime import date
    compra = CompraParcelada.objects.create(
        tenant=org, estabelecimento=b, descritor="B", data=date(2026, 5, 1), parcelas=3, vistas=1,
        ultima_parcela=1, valor_parcela=10, valor=30, classification_level="restrito")
    L.fundir(a, b)
    compra.refresh_from_db()
    assert compra.estabelecimento == a


# ── aplicar as de alta ──────────────────────────────────────────────────────

def test_aplicar_alta_so_grava_as_de_alta(org):
    for nome in CATS:
        CategoriaGasto.objects.create(tenant=org, nome=nome)
    posto = Estabelecimento.objects.create(tenant=org, nome="Posto")
    DescritorEstabelecimento.objects.create(tenant=org, descritor="AUTO POSTO COMETA", estabelecimento=posto)
    lab = Estabelecimento.objects.create(tenant=org, nome="Lab")
    DescritorEstabelecimento.objects.create(tenant=org, descritor="TEC LAB INDAIATUBA", estabelecimento=lab)
    assert C.aplicar_alta(org) == 1
    posto.refresh_from_db()
    lab.refresh_from_db()
    assert posto.categoria.nome == "Transporte" and lab.categoria is None


# ── exportar e importar ─────────────────────────────────────────────────────

def _preencher(org):
    L.registrar(_estruturacao(org))
    naka = Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Indaiatuba")
    L.fundir(naka, Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Produtos"))
    naka.refresh_from_db()
    naka.nome, naka.categoria = "Nakayoshi", CategoriaGasto.objects.create(tenant=org, nome="Mercado japonês")
    naka.save()
    conta = Conta.objects.get(tenant=org)
    conta.apelido, conta.propria = "Ourocard", False
    conta.save()
    return naka


def _zerar(org):
    Movimento.objects.filter(tenant=org).delete()
    Estabelecimento.objects.filter(tenant=org).delete()
    CategoriaGasto.objects.filter(tenant=org).delete()
    Conta.objects.filter(tenant=org).delete()


@pytest.mark.parametrize("importar_antes", [True, False])
def test_exportar_zerar_e_importar_volta_ao_mesmo_ponto(org, importar_antes):
    _preencher(org)
    dados = json.loads(json.dumps(E.exportar(org)))
    assert dados["formato"] == E.FORMATO and "Mercado japonês" in dados["categorias"]

    _zerar(org)
    if importar_antes:
        r = E.importar(org, dados)
        assert r.estabelecimentos_criados == len(dados["estabelecimentos"]) and r.contas_criadas == 1
        L.reconstruir(org)
    else:
        L.reconstruir(org)          # a projeção recria um estabelecimento por descritor, sem nome nem categoria
        r = E.importar(org, dados)
        assert r.estabelecimentos_juntados == 1      # as duas variantes de Nakayoshi voltam a ser uma

    naka = DescritorEstabelecimento.objects.get(tenant=org, descritor="NAKAYOSHI INDAIATUBA").estabelecimento
    assert naka.nome == "Nakayoshi" and naka.categoria.nome == "Mercado japonês"
    assert naka.descritores.count() == 2 and Movimento.objects.filter(estabelecimento=naka).count() == 2
    conta = Conta.objects.get(tenant=org)
    assert conta.apelido == "Ourocard" and conta.propria is False
    assert set(Movimento.objects.filter(tenant=org).values_list("conta_id", flat=True)) == {conta.id}

    # De novo, o mesmo arquivo: nada muda.
    r = E.importar(org, dados)
    assert (r.categorias_criadas, r.estabelecimentos_criados, r.estabelecimentos_atualizados,
            r.estabelecimentos_juntados, r.contas_criadas, r.contas_atualizadas) == (0, 0, 0, 0, 0, 0)


def test_importar_nao_apaga_o_que_o_arquivo_deixa_vazio(org):
    naka = _preencher(org)
    dados = E.exportar(org)
    for e in dados["estabelecimentos"]:
        e["categoria"] = None
    dados["contas"][0]["apelido"] = ""
    E.importar(org, dados)
    naka.refresh_from_db()
    assert naka.categoria.nome == "Mercado japonês"
    assert Conta.objects.get(tenant=org).apelido == "Ourocard"


def test_importar_recusa_arquivo_alheio():
    with pytest.raises(ValueError):
        E.validar({"formato": "outro"})
    with pytest.raises(ValueError):
        E.validar({"formato": E.FORMATO, "versao": 99})


# ── telas ───────────────────────────────────────────────────────────────────

def test_tela_categorizar_sugere_e_aplica(client, org):
    client.force_login(org.owner)
    L.registrar(_estruturacao(org))
    resp = client.get("/gastos/categorizar/")
    assert resp.status_code == 200
    hortifruti = next(l for l in resp.context["linhas"] if l["estabelecimento"].nome.startswith("Hortifrutisanta"))
    assert hortifruti["sugestao"].categoria == "Mercado" and hortifruti["sugestao"].confianca == C.ALTA

    resp = client.post("/gastos/categorizar/", {"organizacao": org.pk, "acao": "aplicar_alta"})
    assert resp.status_code == 302 and "aplicadas=" in resp.url
    assert Estabelecimento.objects.get(pk=hortifruti["estabelecimento"].pk).categoria.nome == "Mercado"

    loja = Estabelecimento.objects.get(tenant=org, nome="Estorno Loja X")
    cat = CategoriaGasto.objects.get(tenant=org, nome="Compras")
    client.post("/gastos/categorizar/", {"organizacao": org.pk, "acao": "categoria",
                                         "estabelecimento": loja.pk, "categoria": cat.pk})
    loja.refresh_from_db()
    assert loja.categoria == cat


def test_tela_estado_baixa_e_importa(client, org):
    client.force_login(org.owner)
    _preencher(org)
    resp = client.get("/gastos/estado/?baixar=1")
    assert resp.status_code == 200 and "attachment" in resp["Content-Disposition"]
    corpo = resp.content
    assert json.loads(corpo)["formato"] == E.FORMATO

    _zerar(org)
    resp = client.post("/gastos/estado/", {"organizacao": org.pk,
                                           "arquivo": SimpleUploadedFile("e.json", corpo, "application/json")})
    assert resp.status_code == 200 and resp.context["resumo"].contas_criadas == 1
    assert CategoriaGasto.objects.filter(tenant=org, nome="Mercado japonês").exists()

    resp = client.post("/gastos/estado/", {"organizacao": org.pk,
                                           "arquivo": SimpleUploadedFile("e.json", b"{", "application/json")})
    assert resp.status_code == 400


def test_tela_estado_isola_e_importar_exige_admin(client, org):
    _preencher(org)
    membro = User.objects.create_user(username="membro", password="x")
    Membership.objects.create(user=membro, organization=org, role=Membership.Role.MEMBER)
    client.force_login(membro)
    assert client.get("/gastos/estado/?baixar=1").status_code == 200            # exportar: qualquer membro
    arquivo = SimpleUploadedFile("e.json", json.dumps(E.exportar(org)).encode(), "application/json")
    assert client.post("/gastos/estado/", {"organizacao": org.pk, "arquivo": arquivo}).status_code == 403

    estranho = User.objects.create_user(username="estranho", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert client.get(f"/gastos/estado/?organizacao={org.pk}&baixar=1").status_code == 404
    assert client.get(f"/gastos/categorizar/?organizacao={org.pk}").status_code == 404

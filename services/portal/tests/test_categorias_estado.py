"""Gastos: sugestão de categoria (`categorias.py`), decisões da pessoa como alegações e o estado como
projeção delas (`decisoes.py`), exportar/importar (`estado.py`), com as telas `/gastos/categorizar/`
e `/gastos/estado/`."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Claim
from apps.financeiro import categorias as C
from apps.financeiro import decisoes as D
from apps.financeiro import estado as E
from apps.financeiro import lancamentos as L
from apps.financeiro.models import (
    CategoriaGasto, CompraParcelada, Conta, DescritorEstabelecimento, Estabelecimento, Movimento,
)
from tests.test_financeiro import FATURA, HASH as FATURA_HASH, _estruturacao, org  # noqa: F401 (fixture)

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


# ── parcela e normalização ──────────────────────────────────────────────────

def test_parcela_sem_parc_e_descritor_sem_ela():
    assert L.parcela_da_descricao("ALLIANZ SEGU 02 de 10 SAO PAULO BR") == (2, 10)
    assert L.parcela_da_descricao("PADARIA 7 DE SETEMBRO") is None
    assert L.normalizar_descritor("ALLIANZ SEGU 02 de 10 SAO PAULO BR") == "ALLIANZ SEGU SAO PAULO"
    assert L.normalizar_descritor("ALLIANZ SEGU 1 de 10 SAO PAULO BR") == "ALLIANZ SEGU SAO PAULO"


def test_decisao_sobre_descritor_antigo_vale_com_a_normalizacao_nova(org):
    L.registrar(_estruturacao(org))
    # Decidido quando a normalização ainda deixava a parcela no descritor.
    D.Decisor(org, org.owner, tela="teste").categorizar_descritores(["ALLIANZ SEGU 1 DE 10 SAO PAULO"], "Transporte")
    m = Movimento.objects.filter(tenant=org, estabelecimento__isnull=False).first()
    Movimento.objects.filter(pk=m.pk).update(descricao="ALLIANZ SEGU 02 de 10 SAO PAULO BR")
    D.projetar(org.pk)
    est = DescritorEstabelecimento.objects.get(tenant=org, descritor="ALLIANZ SEGU SAO PAULO").estabelecimento
    assert est.categoria.nome == "Transporte" and est.nome == "Allianz Segu Sao Paulo"
    assert Movimento.objects.get(pk=m.pk).estabelecimento == est


# ── decisões como alegações ─────────────────────────────────────────────────

def _decisoes(org, predicado=None):
    qs = Claim.objects.filter(tenant=org, produtor=D.PRODUTOR).order_by("criada_em", "id")
    return list(qs.filter(predicado=predicado) if predicado else qs)


def test_fundir_e_alegacao_da_pessoa_e_leva_a_compra(org):
    L.registrar(_estruturacao(org))
    naka = Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Indaiatuba")
    produtos = Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Produtos")
    descritor = produtos.descritores.get().descritor
    from datetime import date
    compra = CompraParcelada.objects.create(
        tenant=org, estabelecimento=produtos, descritor=descritor, data=date(2026, 5, 1), parcelas=3, vistas=1,
        ultima_parcela=1, valor_parcela=10, valor=30, classification_level="restrito")
    antes = Estabelecimento.objects.filter(tenant=org).count()

    decisor = D.Decisor(org, org.owner, tela="gastos", acao="fundir")
    decisor.fundir(naka, produtos)
    D.projetar(org.pk)

    (claim,) = _decisoes(org, D.MESMO_LUGAR)
    assert claim.artefato is None and claim.autor_ref == f"urn:uuid:{org.owner.pk}"
    assert claim.sujeito_ref == D.ref_descritor(descritor) and claim.classification_level == "interno"
    (ev,) = claim.evidencias.all()
    assert ev.localizador_tipo == "decisao" and ev.blob_hash == "" and ev.localizador["acao"] == "fundir"
    assert Estabelecimento.objects.filter(tenant=org).count() == antes - 1
    junto = DescritorEstabelecimento.objects.get(tenant=org, descritor=descritor).estabelecimento
    assert junto.descritores.count() == 2
    compra.refresh_from_db()
    assert compra.estabelecimento == junto


def test_decisao_nova_revisa_a_anterior_e_voltar_atras_vale(org):
    L.registrar(_estruturacao(org))
    loja = Estabelecimento.objects.get(tenant=org, nome="Estorno Loja X")
    for categoria in ("Mercado", "Pet", "Mercado"):
        D.Decisor(org, org.owner, tela="teste").categorizar(loja, categoria)
    D.projetar(org.pk)
    a, b, c = _decisoes(org, D.CATEGORIA)
    assert (b.revisa, c.revisa) == (a, b) and a.objeto_ref == c.objeto_ref == D.ref_categoria("Mercado")
    loja.refresh_from_db()
    assert loja.categoria.nome == "Mercado"

    decisor = D.Decisor(org, org.owner, tela="teste")
    decisor.categorizar(loja, "Mercado")                     # o que já vale não vira decisão nova
    assert decisor.registradas == 0
    decisor.categorizar(loja, None)
    D.projetar(org.pk)
    loja.refresh_from_db()
    assert loja.categoria is None and _decisoes(org, D.CATEGORIA)[-1].objeto_ref == D.NENHUM


def test_juntar_contas_e_apelido_sao_decisoes(org):
    L.registrar(_estruturacao(org))
    conta = Conta.objects.get(tenant=org)
    outra = Conta.objects.create(tenant=org, tipo=conta.tipo, instituicao=conta.instituicao, identificadores=["9999"])
    m = Movimento.objects.filter(tenant=org).first()
    Movimento.objects.filter(pk=m.pk).update(conta=outra)
    decisor = D.Decisor(org, org.owner, tela="teste")
    decisor.apelidar(outra, "Cartão velho")
    decisor.juntar_contas(conta, outra)
    D.projetar(org.pk)
    (junta,) = Conta.objects.filter(tenant=org)
    assert junta == conta and set(junta.identificadores) == set(conta.identificadores) | {"9999"}
    assert Movimento.objects.get(pk=m.pk).conta == conta
    assert junta.apelido == "Cartão velho"           # o destino não tinha apelido: vale o da outra
    assert _decisoes(org, D.MESMA_CONTA)[0].classification_level == "restrito"
    with pytest.raises(ValueError):
        decisor.juntar_contas(conta, Conta.objects.create(tenant=org, tipo="corrente", instituicao="x"))


def test_zerar_o_estado_e_reconstruir_volta_tudo_das_alegacoes(org):
    _preencher(org)
    _zerar(org)
    L.reconstruir(org)
    _confere(org)


def test_alegacao_sem_artefato_so_como_decisao(org):
    from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao

    decisao = EvidenciaEntrada(blob_hash="", localizador_tipo="decisao")
    comum = dict(artefato=None, sujeito_ref="fin:descritor/X", predicado="fin:nome", objeto_literal="X",
                 autor_ref=f"urn:uuid:{org.owner.pk}", produtor="pessoa", produtor_versao="1")
    with pytest.raises(ValueError):
        registrar_alegacao(**comum, evidencias=[decisao])                       # sem tenant
    with pytest.raises(ValueError):
        registrar_alegacao(**comum, tenant=org, classificacao="interno",
                           evidencias=[EvidenciaEntrada(blob_hash="", localizador_tipo="parser-pagina")])
    with pytest.raises(ValueError):
        registrar_alegacao(**comum, tenant=org, classificacao="interno",
                           evidencias=[EvidenciaEntrada(blob_hash=FATURA_HASH, localizador_tipo="decisao")])
    with pytest.raises(ValueError):                                             # referência com espaço
        registrar_alegacao(**{**comum, "sujeito_ref": "fin:descritor/X Y"}, tenant=org, classificacao="interno",
                           evidencias=[decisao])
    claim, criada = registrar_alegacao(**comum, tenant=org, classificacao="interno", evidencias=[decisao])
    assert criada and claim.artefato is None
    assert registrar_alegacao(**comum, tenant=org, classificacao="interno", evidencias=[decisao]) == (claim, False)


# ── aplicar as de alta ──────────────────────────────────────────────────────

def test_aplicar_alta_so_registra_as_de_alta(org):
    for nome in CATS:
        CategoriaGasto.objects.create(tenant=org, nome=nome)
    posto = Estabelecimento.objects.create(tenant=org, nome="Posto")
    DescritorEstabelecimento.objects.create(tenant=org, descritor="AUTO POSTO COMETA", estabelecimento=posto)
    lab = Estabelecimento.objects.create(tenant=org, nome="Lab")
    DescritorEstabelecimento.objects.create(tenant=org, descritor="TEC LAB INDAIATUBA", estabelecimento=lab)
    assert C.aplicar_alta(org, org.owner) == 1
    vig = D.vigentes(org.pk)
    assert vig[(D.ref_descritor("AUTO POSTO COMETA"), D.CATEGORIA)].objeto_ref == D.ref_categoria("Transporte")
    assert (D.ref_descritor("TEC LAB INDAIATUBA"), D.CATEGORIA) not in vig
    (claim,) = _decisoes(org, D.CATEGORIA)
    assert claim.evidencias.get().localizador["acao"] == "aplicar_alta"


# ── exportar e importar ─────────────────────────────────────────────────────

def _preencher(org):
    L.registrar(_estruturacao(org))
    decisor = D.Decisor(org, org.owner, tela="teste")
    naka = Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Indaiatuba")
    decisor.fundir(naka, Estabelecimento.objects.get(tenant=org, nome__startswith="Nakayoshi Produtos"))
    decisor.renomear(naka, "Nakayoshi")
    decisor.categorizar(naka, "Mercado japonês")
    conta = Conta.objects.get(tenant=org)
    decisor.apelidar(conta, "Ourocard")
    decisor.marcar_propria(conta, False)
    D.projetar(org.pk)
    return DescritorEstabelecimento.objects.get(tenant=org, descritor="NAKAYOSHI INDAIATUBA").estabelecimento


def _zerar(org, decisoes=False):
    Movimento.objects.filter(tenant=org).delete()
    Estabelecimento.objects.filter(tenant=org).delete()
    CategoriaGasto.objects.filter(tenant=org).delete()
    Conta.objects.filter(tenant=org).delete()
    if decisoes:                    # zerar o banco inteiro: as decisões também se vão
        Claim.objects.filter(tenant=org, produtor=D.PRODUTOR).delete()


def _confere(org):
    naka = DescritorEstabelecimento.objects.get(tenant=org, descritor="NAKAYOSHI INDAIATUBA").estabelecimento
    assert naka.nome == "Nakayoshi" and naka.categoria.nome == "Mercado japonês"
    assert naka.descritores.count() == 2 and Movimento.objects.filter(estabelecimento=naka).count() == 2
    conta = Conta.objects.get(tenant=org)
    assert conta.apelido == "Ourocard" and conta.propria is False
    assert set(Movimento.objects.filter(tenant=org).values_list("conta_id", flat=True)) == {conta.id}


@pytest.mark.parametrize("importar_antes", [True, False])
def test_exportar_zerar_e_importar_volta_ao_mesmo_ponto(org, importar_antes):
    _preencher(org)
    dados = json.loads(json.dumps(E.exportar(org)))
    assert dados["formato"] == E.FORMATO and "Mercado japonês" in dados["categorias"]

    _zerar(org, decisoes=True)
    if importar_antes:
        r = E.importar(org, dados, org.owner)
        assert r.estabelecimentos == len(dados["estabelecimentos"]) and r.contas == 1 and r.decisoes
        L.reconstruir(org)
    else:
        L.reconstruir(org)          # a projeção recria um estabelecimento por descritor, sem nome nem categoria
        E.importar(org, dados, org.owner)
    _confere(org)

    # De novo, o mesmo arquivo: nenhuma decisão nova.
    assert E.importar(org, dados, org.owner).decisoes == 0


def test_importar_nao_apaga_o_que_o_arquivo_deixa_vazio(org):
    naka = _preencher(org)
    dados = E.exportar(org)
    for e in dados["estabelecimentos"]:
        e["categoria"] = None
    dados["contas"][0]["apelido"] = ""
    E.importar(org, dados, org.owner)
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
    assert _decisoes(org, D.CATEGORIA)[-1].autor_ref == f"urn:uuid:{org.owner.pk}"


def test_tela_gastos_decide_em_vez_de_editar(client, org):
    client.force_login(org.owner)
    L.registrar(_estruturacao(org))
    loja = Estabelecimento.objects.get(tenant=org, nome="Estorno Loja X")
    client.post("/gastos/", {"organizacao": org.pk, "acao": "renomear", "estabelecimento": loja.pk, "nome": "Loja X"})
    client.post("/gastos/", {"organizacao": org.pk, "acao": "nova_categoria", "nome": "Presentes"})
    conta = Conta.objects.get(tenant=org)
    client.post("/gastos/", {"organizacao": org.pk, "acao": "apelido_conta", "conta": conta.pk, "apelido": "Ouro"})
    loja.refresh_from_db()
    conta.refresh_from_db()
    assert loja.nome == "Loja X" and conta.apelido == "Ouro"
    assert {c.predicado for c in _decisoes(org)} == {D.NOME, D.ROTULO, D.APELIDO}
    # Reconstruir não perde nada: o estado vem das decisões.
    _zerar(org)
    L.reconstruir(org)
    assert Estabelecimento.objects.filter(tenant=org, nome="Loja X").exists()
    assert CategoriaGasto.objects.filter(tenant=org, nome="Presentes").exists()
    assert Conta.objects.get(tenant=org).apelido == "Ouro"


def test_tela_estado_baixa_e_importa(client, org):
    client.force_login(org.owner)
    _preencher(org)
    resp = client.get("/gastos/estado/?baixar=1")
    assert resp.status_code == 200 and "attachment" in resp["Content-Disposition"]
    corpo = resp.content
    assert json.loads(corpo)["formato"] == E.FORMATO

    _zerar(org, decisoes=True)
    resp = client.post("/gastos/estado/", {"organizacao": org.pk,
                                           "arquivo": SimpleUploadedFile("e.json", corpo, "application/json")})
    assert resp.status_code == 200 and resp.context["resumo"].contas == 1
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

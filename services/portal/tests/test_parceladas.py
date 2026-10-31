"""Compras parceladas (ADR 021, fase 2): agrupar as parcelas, visão por compra, compromisso futuro."""
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.models import Artifact
from apps.financeiro import lancamentos as L
from apps.financeiro.models import CompraParcelada, Conta, LeituraLancamentos, Movimento

pytestmark = pytest.mark.django_db
ESTRUTURACAO = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


@pytest.fixture
def conta(org):
    return Conta.objects.create(tenant=org, tipo="cartao", instituicao="www2.bb.com.br", identificadores=["1402"])


_faturas: dict = {}


def _fatura(org, competencia):
    """Um documento por mês de competência (uma fatura)."""
    chave = (org.pk, competencia)
    if chave not in _faturas or not Artifact.objects.filter(pk=_faturas[chave].pk).exists():
        _faturas[chave] = Artifact.objects.create(
            artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
            classification_level="restrito", content={})
    return _faturas[chave]


def mov(org, conta, descricao, valor, data, competencia, parcela=None, parcelas=None, natureza="compra"):
    artefato = _fatura(org, competencia)
    indice = Movimento.objects.filter(artefato=artefato).count()
    return Movimento.objects.create(
        tenant=org, artefato=artefato, conta=conta, fonte="estruturacao", fonte_id=ESTRUTURACAO, indice=indice,
        sujeito_ref=f"s/{indice}", data=data, competencia=competencia, valor=-Decimal(valor), valor_texto=valor,
        descricao=descricao, natureza=natureza, parcela=parcela, parcelas=parcelas, classification_level="restrito")


def _vindi(org, conta):
    """Vindi Catum, 13/05/2025, 12×: a primeira com os centavos do arredondamento."""
    for n in range(1, 13):
        mes = date(2025 + (5 + n - 1) // 12, (5 + n - 1) % 12 + 1, 1)          # jun/2025 em diante
        mov(org, conta, f"Vindi Catum PARC {n:02d}/12 Campinas BR", "10.52" if n == 1 else "10.43",
            date(2025, 5, 13), mes, n, 12)


def test_as_doze_parcelas_viram_uma_compra_mesmo_com_a_primeira_diferente(org, conta):
    _vindi(org, conta)
    assert L.agrupar_parceladas(org.pk) == 1
    c = CompraParcelada.objects.get()
    assert (c.parcelas, c.vistas, c.ultima_parcela) == (12, 12, 12)
    assert c.valor == Decimal("125.25") and c.valor_parcela == Decimal("10.43") and not c.estimado
    assert c.descritor == "VINDI CATUM CAMPINAS" and c.data == date(2025, 5, 13)
    assert Movimento.objects.filter(compra=c).count() == 12


def test_parcelas_anteriores_a_primeira_fatura_dao_valor_estimado(org, conta):
    for n in (3, 4, 5):
        mov(org, conta, f"LOJA PARC {n:02d}/10", "20.00", date(2026, 6, 2), date(2026, 5 + n, 1), n, 10)
    L.agrupar_parceladas(org.pk)
    c = CompraParcelada.objects.get()
    assert c.vistas == 3 and c.estimado and c.valor == Decimal("200.00")      # 3 vistas + 7 × 20


def test_duas_compras_iguais_no_mesmo_dia_e_lugar_ficam_separadas(org, conta):
    for valor in ("30.00", "45.00"):
        for n in (1, 2, 3):
            mov(org, conta, f"MAGAZINE PARC {n:02d}/03", valor, date(2026, 7, 1), date(2026, 7 + n, 1), n, 3)
    assert L.agrupar_parceladas(org.pk) == 2
    assert sorted(c.valor for c in CompraParcelada.objects.all()) == [Decimal("90.00"), Decimal("135.00")]


def test_estorno_e_movimento_sem_parcela_nao_viram_compra(org, conta):
    mov(org, conta, "PADARIA", "10.00", date(2026, 9, 1), date(2026, 10, 1))
    mov(org, conta, "LOJA PARC 01/02", "-5.00", date(2026, 9, 1), date(2026, 10, 1), 1, 2, natureza="estorno")
    assert L.agrupar_parceladas(org.pk) == 0


def test_compromisso_futuro_conta_so_o_que_vem_depois_da_ultima_fatura(org, conta):
    # Última fatura capturada: out/2026.
    for n in (1, 2, 3, 4, 5):            # 5/10 em out/2026 → faltam nov..mar
        mov(org, conta, f"TV PARC {n:02d}/10", "20.00", date(2026, 5, 20), date(2026, 5 + n, 1), n, 10)
    for n in (1, 2, 3):                  # 3/10 em ago/2026 e sumiu: set e out já foram capturados
        mov(org, conta, f"SOFA PARC {n:02d}/10", "50.00", date(2026, 5, 2), date(2026, 5 + n, 1), n, 10)
    L.agrupar_parceladas(org.pk)
    futuro = {m["mes"]: (m["compras"], m["valor"]) for m in L.compromisso_futuro(org.pk)}
    assert min(futuro) == date(2026, 11, 1)
    assert futuro[date(2026, 11, 1)] == (2, Decimal("70.00"))
    assert futuro[date(2027, 3, 1)] == (2, Decimal("70.00"))           # os dois terminam em março
    assert date(2027, 4, 1) not in futuro
    assert sum(v for _, v in futuro.values()) == Decimal("350.00")      # 5 meses × (20 + 50)


def test_as_duas_visoes_da_tela(client, org, conta):
    client.force_login(org.owner)
    _vindi(org, conta)
    mov(org, conta, "PADARIA", "10.00", date(2025, 5, 13), date(2025, 6, 1))
    L.agrupar_parceladas(org.pk)
    competencia = client.get("/gastos/?de=2025-05&ate=2025-05").context
    assert competencia["total"] == 0                                    # nada foi cobrado em maio
    junho = client.get("/gastos/?de=2025-06&ate=2025-06").context
    assert junho["total"] == Decimal("20.52")                           # padaria + parcela 01/12
    compra = client.get("/gastos/?de=2025-05&ate=2025-05&visao=compra").context
    assert compra["total"] == Decimal("135.25")                         # a compra inteira + padaria
    tudo_compra = client.get("/gastos/?visao=compra").context["total"]
    tudo_competencia = client.get("/gastos/").context["total"]
    assert tudo_compra == tudo_competencia                              # todas as parcelas foram vistas
    assert len(compra["compras"]) == 1


def test_reconstruir_e_juntar_contas_reagrupam(org, conta):
    outra = Conta.objects.create(tenant=org, tipo="cartao", instituicao="www2.bb.com.br", identificadores=[""])
    for n, c in ((1, outra), (2, conta)):
        mov(org, c, f"TV PARC {n:02d}/02", "50.00", date(2026, 8, 1), date(2026, 8 + n, 1), n, 2)
    L.agrupar_parceladas(org.pk)
    assert CompraParcelada.objects.count() == 2                         # uma em cada conta
    L.juntar_contas(conta, outra)
    c = CompraParcelada.objects.get()
    assert c.vistas == 2 and c.valor == Decimal("100.00")


def test_registrar_agrupa(org, conta):
    from tests.test_movimentos import DADOS_FATURA, FATURA, _registrar

    _registrar(org, FATURA, DADOS_FATURA)
    c = CompraParcelada.objects.get()
    assert c.parcelas == 12 and c.vistas == 1 and c.estimado and c.valor == Decimal("125.16")
    assert LeituraLancamentos.objects.count() == 1

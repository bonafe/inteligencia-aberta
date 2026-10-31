"""Família de páginas (domínio + caminho): fatura e extrato no mesmo endereço, e a impressão
estrutural que muda a cada captura não pode separar a fatura de um mês da do mês seguinte."""
import json
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extracao_pagina import extrair_com_parser
from apps.artifacts.extractors import parser_pagina as P
from apps.artifacts.models import Artifact, DocumentText, ExtracaoPagina, SchemaPagina, URLPatternCache
from apps.events.models import PipelineEvent
from tests.test_parser_pagina import AMOSTRA, OUTRO_MES, SCHEMA, campo

pytestmark = pytest.mark.django_db

LINHAS = "".join(f"<tr><td>{d:02d}/10</td><td>PIX RECEBIDO {d}</td><td>{d}00,00</td></tr>" for d in range(1, 7))
EXTRATO = f"""<html><body>
<div class="cabecalho"><span class="rotulo">Saldo final</span><br>1.234,56</div>
<div class="extrato"><table><tr><td>Data</td><td>Histórico</td><td>Valor</td></tr>{LINHAS}</table></div>
</body></html>"""
SCHEMA_EXTRATO = {
    "descricao": "Extrato de conta corrente",
    "campos": [campo("saldo_final", "valor", "1.234,56")],
    "listas": [{"nome": "movimentos", "descricao": "", "secoes": False, "estrutura": None, "campos": [
        campo("data", "data", "01/10"), campo("historico", "texto", "PIX RECEBIDO 1"), campo("valor", "valor", "100,00")]}],
    "invariantes": [],
}


@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _padrao(org, impressao):
    """Mesmo endereço, impressão estrutural diferente: é o que o BB produz a cada captura."""
    return URLPatternCache.objects.create(tenant=org, domain="www2.banco.example", path_pattern="/principal.jsp",
                                          structure_fingerprint=impressao, page_type="tabular_generico", confidence=0.9)


def _aprovar(org, schema, html, versao, quando):
    from django.utils import timezone

    parser, _ = P.montar_parser(schema, html)
    return SchemaPagina.objects.create(
        tenant=org, padrao=_padrao(org, f"amostra{versao}"), dominio="www2.banco.example", caminho="/principal.jsp",
        versao=versao, status=SchemaPagina.Status.APROVADO, schema=schema, parser=parser,
        classification_level="restrito", provider="ollama", model_name="m",
        aprovado_em=timezone.now() + timezone.timedelta(minutes=quando),
    )


def _captura(org, impressao):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="restrito", content={"url": "https://www2.banco.example/principal.jsp", "mhtml_path": "x"},
    )
    return DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>",
                                       url_pattern_cache=_padrao(org, impressao))


@pytest.fixture
def fatura_e_extrato(org):
    return _aprovar(org, SCHEMA, AMOSTRA, 1, 0), _aprovar(org, SCHEMA_EXTRATO, EXTRATO, 2, 1)


def test_fatura_do_mes_seguinte_com_outra_impressao_ainda_e_lida(org, fatura_e_extrato):
    fatura, _ = fatura_e_extrato
    e = extrair_com_parser(_captura(org, "impressao-de-novembro"), OUTRO_MES)
    assert e.status == ExtracaoPagina.Status.VALIDO and e.schema == fatura
    assert e.dados["campos"]["vencimento"] == "28/11/2026"


def test_a_validacao_separa_fatura_de_extrato_no_mesmo_endereco(org, fatura_e_extrato):
    fatura, extrato = fatura_e_extrato
    e = extrair_com_parser(_captura(org, "x1"), EXTRATO)
    assert e.status == ExtracaoPagina.Status.VALIDO and e.schema == extrato
    assert len(e.dados["listas"]["movimentos"]["itens"]) == 6
    assert {c["versao"]: c["status"] for c in e.candidatos} == {1: "invalido", 2: "valido"}
    f = extrair_com_parser(_captura(org, "x2"), OUTRO_MES)
    assert f.schema == fatura


def test_nenhum_validando_registra_o_mais_perto_e_diz_quantos_tentou(org, fatura_e_extrato):
    fatura, _ = fatura_e_extrato
    e = extrair_com_parser(_captura(org, "x"), OUTRO_MES.replace("Vencimento", "Data limite"))
    assert e.status == ExtracaoPagina.Status.INVALIDO and e.schema == fatura   # a fatura errou menos
    ev = PipelineEvent.objects.get(stage="extracao.parser_pagina")
    assert ev.status == "falhou" and "nenhum dos 2" in ev.message


def test_dois_validando_vale_o_aprovado_mais_recente(org):
    antigo = _aprovar(org, SCHEMA, AMOSTRA, 1, 0)
    recente = _aprovar(org, SCHEMA, AMOSTRA, 2, 5)
    e = extrair_com_parser(_captura(org, "x"), OUTRO_MES)
    assert e.schema == recente
    assert "também validaram [1]" in PipelineEvent.objects.get(stage="extracao.parser_pagina").message
    assert antigo.status == SchemaPagina.Status.APROVADO


def test_outro_endereco_nao_roda(org, fatura_e_extrato):
    artefato = Artifact.objects.create(artifact_type=Artifact.Type.DOCUMENT, tenant=org,
                                       info_type=Artifact.InfoType.FACT, classification_level="restrito", content={})
    outro = URLPatternCache.objects.create(tenant=org, domain="www2.banco.example", path_pattern="/outra.jsp",
                                           structure_fingerprint="z", page_type="x", confidence=0.9)
    doc = DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", url_pattern_cache=outro)
    assert extrair_com_parser(doc, OUTRO_MES) is None


def test_a_versao_e_numerada_pela_familia_entre_capturas_de_impressoes_diferentes(client, org):
    client.force_login(org.owner)
    with mock.patch("apps.artifacts.tasks.gerar_schema_pagina.delay") as delay:
        delay.return_value.id = "t"
        versoes = [
            client.post(f"/artifacts/{_captura(org, imp).document_id}/schema-pagina/",
                        json.dumps({"provider": "ollama", "model_name": "m"}), content_type="application/json").json()["versao"]
            for imp in ("a", "b", "c")
        ]
    assert versoes == [1, 2, 3]
    assert set(SchemaPagina.objects.values_list("dominio", "caminho")) == {("www2.banco.example", "/principal.jsp")}


def test_pagina_de_schemas_agrupa_por_endereco_e_isola_organizacao(client, org, fatura_e_extrato):
    estranho = User.objects.create_user(username="e", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(org.owner)
    extrair_com_parser(_captura(org, "x"), OUTRO_MES)
    pagina = client.get("/artifacts/schemas/").content.decode()
    assert "www2.banco.example/principal.jsp" in pagina and "2 aprovado(s)" in pagina
    assert "Extrato de conta corrente" in pagina and "Fatura de cartão" in pagina
    client.force_login(estranho)
    assert "Nenhum schema ainda" in client.get("/artifacts/schemas/").content.decode()

"""ADR 022, etapa 4: o piso de classificação de cada tipo de dado da extensão vale na alegação, na
projeção e na decisão de replicação; as regras de replicação só citam tipos conhecidos."""
import pytest
from django.core.exceptions import ValidationError

from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao
from apps.artifacts.models import Artifact, Claim
from apps.extensoes import registro
from apps.extensoes.contrato import Extensao, TipoDado, Vocabulario, mais_alto
from apps.federacao import politica, regras
from apps.federacao.models import RegraReplicacao
from apps.financeiro import decisoes as D
from apps.financeiro import lancamentos as L
from apps.financeiro.models import Movimento
from tests.test_financeiro import HASH, _estruturacao, org  # noqa: F401 (fixture)


def _alegar(artefato, predicado, **extra):
    return registrar_alegacao(
        artefato=artefato, sujeito_ref=f"mencao:{HASH}#teste/1", predicado=predicado, objeto_literal="10,00",
        autor_ref="dominio:exemplo.com.br", produtor="teste", produtor_versao="1",
        evidencias=[EvidenciaEntrada(blob_hash=HASH, localizador_tipo="teste")], **extra,
    )


def test_mais_alto():
    assert mais_alto("publico", "restrito", None) == "restrito"
    assert mais_alto("confidencial", "interno") == "confidencial"
    assert mais_alto(None) is None


def test_alegacao_sobe_ao_piso_do_tipo_e_nunca_desce(org):
    artefato = _estruturacao(org).document_text.document
    Artifact.objects.filter(pk=artefato.pk).update(classification_level="publico")
    artefato.refresh_from_db()
    claim, _ = _alegar(artefato, "fin:valor")
    assert claim.classification_level == "restrito"                    # piso de fin:Movimento
    Artifact.objects.filter(pk=artefato.pk).update(classification_level="confidencial")
    artefato.refresh_from_db()
    claim, _ = _alegar(artefato, "fin:data")
    assert claim.classification_level == "confidencial"                # acima do piso, fica
    assert _alegar(artefato, "schema:name")[0].classification_level == "confidencial"   # núcleo: sem piso


def test_predicado_da_extensao_nao_declarado_e_recusado(org):
    artefato = _estruturacao(org).document_text.document
    with pytest.raises(ValueError, match="não é declarado"):
        _alegar(artefato, "fin:inventado")


def test_decisao_nasce_no_piso_mesmo_pedindo_menos(org):
    claim, _ = registrar_alegacao(
        artefato=None, tenant=org, classificacao="publico", sujeito_ref=D.ref_conta("cartao", "x", "1"),
        predicado=D.APELIDO, objeto_literal="Ouro", autor_ref=f"urn:uuid:{org.owner.pk}",
        produtor=D.PRODUTOR, produtor_versao=D.PRODUTOR_VERSAO,
        evidencias=[EvidenciaEntrada(blob_hash="", localizador_tipo="decisao")],
    )
    assert claim.classification_level == "restrito"                    # piso de fin:Conta


def test_movimento_de_documento_menos_restrito_fica_no_piso(org):
    e = _estruturacao(org)
    Artifact.objects.filter(pk=e.document_text.document_id).update(classification_level="interno")
    e.document_text.document.refresh_from_db()
    L.registrar(e)
    assert set(Movimento.objects.values_list("classification_level", flat=True)) == {"restrito"}
    assert set(Claim.objects.filter(predicado__startswith="fin:").values_list("classification_level", flat=True)) \
        == {"restrito"}


def test_registro_recusa_tipo_fora_do_vocabulario_ou_predicado_repetido():
    voc = Vocabulario("x", "https://exemplo/x#")
    with pytest.raises(ValueError, match="fora do vocabulário"):
        registro._validar_tipos(Extensao("x", "X", "1", voc, tipos=(TipoDado("x:A", "a", predicados=("y:b",)),)))
    with pytest.raises(ValueError, match="mais de um tipo"):
        registro._validar_tipos(Extensao("x", "X", "1", voc, tipos=(
            TipoDado("x:A", "a", predicados=("x:b",)), TipoDado("x:B", "b", predicados=("x:b",)))))
    with pytest.raises(ValueError, match="tipo repetido"):
        registro._validar_tipos(Extensao("x", "X", "1", voc, tipos=(TipoDado("x:A", "a"), TipoDado("x:A", "b"))))


def test_cada_predicado_do_financeiro_tem_tipo():
    usados = {"fin:data", "fin:valor", "fin:descricao", "fin:secao", "fin:documento", "fin:sentido", "fin:periodo",
              "fin:conta", "fin:titular", D.CATEGORIA, D.NOME, D.EMPRESA, D.MESMO_LUGAR, D.ROTULO, D.APELIDO,
              D.PROPRIA, D.MESMA_CONTA}
    assert {p for p in usados if registro.tipo_do_predicado(p) is None} == set()


def test_regra_so_cita_tipo_conhecido(org):
    comum = dict(organizacao=org, efeito="negar", sentido="enviar")
    assert RegraReplicacao.objects.create(**comum, tipo_objeto="fin:Movimento").tipo_objeto == "fin:Movimento"
    assert RegraReplicacao.objects.create(**comum, tipo_objeto="documento").tipo_objeto == "documento"
    with pytest.raises(ValidationError):
        RegraReplicacao.objects.create(**comum, tipo_objeto="fin:Inexistente")
    assert ("fin:Movimento", "Movimento de conta") in politica.tipos_de_objeto()


def test_decisao_de_replicacao_usa_o_piso_do_tipo(org):
    ctx = regras.Contexto(sentido="enviar", par_tipo="proprio", nivel="publico", tipo_objeto="fin:Movimento")
    assert politica.decidir(org, ctx).nivel_efetivo == "restrito"
    sem_tipo = regras.Contexto(sentido="enviar", par_tipo="proprio", nivel="publico", tipo_objeto="documento")
    assert politica.decidir(org, sem_tipo).nivel_efetivo == "publico"


def test_simulacao_recusa_tipo_desconhecido(client, org):
    client.force_login(org.owner)
    dados = {"organizacao": org.pk, "sentido": "enviar", "par_tipo": "proprio", "nivel": "publico"}
    assert client.post("/cluster/regras/", {**dados, "tipo_objeto": "xyz"}).status_code == 400
    resp = client.post("/cluster/regras/", {**dados, "tipo_objeto": "fin:Movimento"})
    assert resp.status_code == 200 and resp.context["decisao"].nivel_efetivo == "restrito"

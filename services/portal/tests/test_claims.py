"""`Claim` e `Evidence` — F0 da ADR 010: modelo, serviço único e produtor `extruct`."""
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest import mock

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Organization, User
from apps.artifacts import alegacoes
from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao, retratar, truncar_trecho
from apps.artifacts.extractors import claims_extruct
from apps.artifacts.models import Artifact, Claim, Evidence
from apps.artifacts.referencias import cnpj_valido, normalizar_referencia
from config.conteudo_hash import hash_ni

pytestmark = pytest.mark.django_db

HASH = "ni:///sha-256;" + "A" * 43
CNPJ = "11222333000181"  # DV válido


# ── referências ─────────────────────────────────────────────────────────────

def test_cnpj_valido():
    assert cnpj_valido(CNPJ)
    for ruim in ("11222333000182", "00000000000000", "1122233300018", "abc", "", None, "11111111111111"):
        assert not cnpj_valido(ruim)


@pytest.mark.parametrize("entrada,esperado", [
    ("cnpj:11.222.333/0001-81", f"cnpj:{CNPJ}"),
    ("CNPJ:11222333000181", f"cnpj:{CNPJ}"),
    ("url:https://exemplo.org/a?b=1", "url:https://exemplo.org/a?b=1"),
    ("dominio:WWW.Exemplo.org", "dominio:www.exemplo.org"),
    ("URN:UUID:3F2504E0-4F89-41D3-9A0C-0305E82C3301", "urn:uuid:3f2504e0-4f89-41d3-9a0c-0305e82c3301"),
    (f"mencao:{HASH}#json-ld[0]/@graph[2]", f"mencao:{HASH}#json-ld[0]/@graph[2]"),
])
def test_normalizar_referencia(entrada, esperado):
    assert normalizar_referencia(entrada) == esperado


@pytest.mark.parametrize("ruim", [
    "", "  ", None, "cnpj:11222333000182", "url:ftp://x.org", "url:exemplo.org", "dominio:192.168.0.1",
    "mencao:lixo#x", f"mencao:{HASH}", f"mencao:{HASH}#", "pessoa:joao", "urn:uuid:nao-e-uuid",
    "url:https://x.org/" + "a" * 600,
])
def test_referencia_invalida(ruim):
    with pytest.raises(ValueError):
        normalizar_referencia(ruim)


# ── serviço ─────────────────────────────────────────────────────────────────

@pytest.fixture
def org():
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


@pytest.fixture
def artefato(org):
    return Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="confidencial", content={"url": "https://www.jornal.example/a", "mhtml_path": "x"},
        blob_hash=HASH,
    )


def _ev(**kw):
    base = dict(blob_hash=HASH, localizador_tipo="extruct.json-ld",
                localizador={"item": "json-ld[0]", "propriedade": "name"}, trecho='{"name": "Empresa X"}')
    base.update(kw)
    return EvidenciaEntrada(**base)


def _registrar(artefato, **kw):
    base = dict(artefato=artefato, sujeito_ref=f"cnpj:{CNPJ}", predicado="schema:name",
                objeto_literal="Empresa X", autor_ref="dominio:jornal.example",
                produtor="teste", produtor_versao="1", evidencias=[_ev()])
    base.update(kw)
    return registrar_alegacao(**base)


def test_registrar_cria_alegacao_com_evidencia_e_herda_classificacao(artefato):
    claim, criada = _registrar(artefato)
    assert criada
    assert claim.classification_level == "confidencial" and claim.tenant_id == artefato.tenant_id
    assert claim.estado == "ativa" and claim.urn == f"urn:uuid:{claim.id}"
    [ev] = claim.evidencias.all()
    assert ev.blob_hash == HASH and ev.localizador["propriedade"] == "name"


def test_registrar_normaliza_as_referencias(artefato):
    claim, _ = _registrar(artefato, sujeito_ref="cnpj:11.222.333/0001-81", autor_ref="dominio:Jornal.Example")
    assert claim.sujeito_ref == f"cnpj:{CNPJ}" and claim.autor_ref == "dominio:jornal.example"


def test_registrar_e_idempotente_e_nao_duplica_evidencias(artefato):
    a, c1 = _registrar(artefato)
    b, c2 = _registrar(artefato)
    assert c1 and not c2 and a.pk == b.pk
    assert Claim.objects.count() == 1 and Evidence.objects.count() == 1


def test_versao_nova_do_produtor_gera_alegacao_nova_e_a_antiga_permanece(artefato):
    _registrar(artefato, produtor_versao="1")
    _registrar(artefato, produtor_versao="2")
    assert Claim.objects.count() == 2


def test_objeto_ref_e_objeto_literal_sao_exclusivos(artefato):
    with pytest.raises(ValueError):
        _registrar(artefato, objeto_ref="url:https://x.org", objeto_literal="x")
    with pytest.raises(ValueError):
        _registrar(artefato, objeto_literal=None)
    claim, _ = _registrar(artefato, predicado="schema:url", objeto_literal=None, objeto_ref="url:https://x.org")
    assert claim.objeto_literal is None and claim.objeto_ref == "url:https://x.org"


@pytest.mark.parametrize("kw", [
    dict(predicado="name"), dict(predicado="outro:name"), dict(predicado="schema:"),
    dict(produtor=""), dict(produtor_versao=""),
    dict(confianca=1.5), dict(confianca=-0.1),
    dict(evidencias=[]),
    dict(objeto_literal="   "), dict(objeto_literal="x" * (alegacoes.LITERAL_MAXIMO + 1)),
    dict(sujeito_ref="pessoa:joao"), dict(autor_ref="lixo"),
    dict(evidencias=[_ev(blob_hash="md5:abc")]),
    dict(evidencias=[_ev(localizador_tipo="")]),
    dict(evidencias=[_ev(trecho="x" * 501)]),
])
def test_entradas_invalidas_nao_gravam_nada(artefato, kw):
    with pytest.raises(ValueError):
        _registrar(artefato, **kw)
    assert Claim.objects.count() == 0 and Evidence.objects.count() == 0


def test_falha_ao_gravar_evidencia_desfaz_a_alegacao(artefato):
    with mock.patch.object(Evidence.objects, "bulk_create", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _registrar(artefato)
    assert Claim.objects.count() == 0


def test_corrida_devolve_a_alegacao_que_ja_existe(artefato):
    vencedora, _ = _registrar(artefato)
    original = Claim.objects.filter

    chamadas = {"n": 0}

    def filtro(*a, **k):
        # a 1ª leitura (idempotência) "não vê" a vencedora; as seguintes veem
        chamadas["n"] += 1
        return Claim.objects.none() if chamadas["n"] == 1 else original(*a, **k)

    with mock.patch.object(Claim.objects, "filter", side_effect=filtro):
        claim, criada = _registrar(artefato)
    assert claim.pk == vencedora.pk and not criada


def test_confianca_guardada(artefato):
    claim, _ = _registrar(artefato, confianca=0.72)
    assert claim.extractor_confidence == 0.72


def test_truncar_trecho():
    assert truncar_trecho("  a   b\n c ") == "a b c"
    longo = truncar_trecho("x" * 1000)
    assert len(longo) == 500 and longo.endswith("…")


# ── imutabilidade ───────────────────────────────────────────────────────────

def test_alegacao_nao_se_edita_nem_se_apaga(artefato):
    claim, _ = _registrar(artefato)
    claim.objeto_literal = "outro"
    with pytest.raises(ValueError):
        claim.save()
    with pytest.raises(ValueError):
        claim.save(update_fields=["objeto_literal"])
    with pytest.raises(ValueError):
        claim.delete()
    claim.refresh_from_db()
    assert claim.objeto_literal == "Empresa X"


def test_evidencia_nao_se_edita_nem_se_apaga(artefato):
    claim, _ = _registrar(artefato)
    ev = claim.evidencias.get()
    ev.trecho = "adulterado"
    with pytest.raises(ValueError):
        ev.save()
    with pytest.raises(ValueError):
        ev.delete()


def test_retratar_muda_so_o_estado_e_e_idempotente(artefato):
    claim, _ = _registrar(artefato)
    retratar(claim)
    claim.refresh_from_db()
    assert claim.estado == "retratada" and claim.retratada_em is not None
    quando = claim.retratada_em
    retratar(claim)
    claim.refresh_from_db()
    assert claim.retratada_em == quando
    assert Claim.objects.count() == 1


def test_correcao_e_outra_alegacao_que_revisa_a_anterior(artefato):
    antiga, _ = _registrar(artefato, objeto_literal="Empresa X")
    nova, _ = _registrar(artefato, objeto_literal="Empresa X S.A.", revisa=antiga)
    retratar(antiga)
    assert nova.revisa_id == antiga.id and antiga.revisoes.get() == nova
    assert Claim.objects.count() == 2


def test_o_banco_recusa_objeto_duplo_ou_ausente_e_confianca_fora_da_faixa(artefato):
    base = dict(tenant=artefato.tenant, artefato=artefato, sujeito_ref="s", predicado="schema:x",
                autor_ref="a", produtor="p", produtor_versao="1", classification_level="restrito")
    for extra in (dict(objeto_ref="r", objeto_literal="l", chave="1"), dict(chave="2"),
                  dict(objeto_literal="l", extractor_confidence=2, chave="3")):
        with pytest.raises(IntegrityError), transaction.atomic():
            Claim.objects.create(**base, **extra)


def test_artefato_com_alegacoes_nao_pode_ser_apagado(artefato):
    from django.db.models import ProtectedError

    _registrar(artefato)
    with pytest.raises(ProtectedError):
        artefato.delete()


# ── produtor extruct (puro) ─────────────────────────────────────────────────

URL = "https://www.jornal.example/noticia/1"


def _gerar(*itens):
    return claims_extruct.gerar({"raw": {"json-ld": list(itens)}}, url_captura=URL, blob_hash=HASH)


def _por_predicado(candidatas):
    return {c.predicado: c for c in candidatas}


def test_organizacao_usa_o_cnpj_como_sujeito_e_o_dominio_como_autor():
    candidatas, ignoradas = _gerar({
        "@type": "Organization", "name": "Empresa X", "taxID": "11.222.333/0001-81",
        "url": "https://x.com.br", "sameAs": ["https://x.com/x", "não é url"],
        "address": {"@type": "PostalAddress", "streetAddress": "Rua A, 1", "addressLocality": "SP"},
        "naoMapeada": "ignorada",
    })
    por = _por_predicado(candidatas)
    assert {c.sujeito_ref for c in candidatas} == {f"cnpj:{CNPJ}"}
    assert {c.autor_ref for c in candidatas} == {"dominio:jornal.example"}   # sem www.
    assert por["schema:taxID"].objeto_literal == "11.222.333/0001-81"
    assert por["schema:address"].objeto_literal == "Rua A, 1, SP"
    assert por["schema:url"].objeto_ref == "url:https://x.com.br"
    assert por["schema:sameAs"].objeto_ref == "url:https://x.com/x"
    assert "schema:naoMapeada" not in por and ignoradas == 1          # só o sameAs que não é URL


def test_localizador_e_trecho_apontam_para_a_propriedade():
    [c] = [c for c in _gerar({"@type": "Organization", "name": "Empresa X"})[0] if c.predicado == "schema:name"]
    assert c.localizador == {"item": "json-ld[0]", "propriedade": "name"}
    assert json.loads(c.trecho) == {"name": "Empresa X"}


def test_trecho_nunca_passa_do_limite():
    [c] = _gerar({"@type": "Organization", "name": "x" * 1900})[0]
    assert len(c.trecho) <= 500


def test_sem_cnpj_valido_o_sujeito_cai_para_url_e_depois_para_mencao():
    com_url, _ = _gerar({"@type": "Organization", "name": "X", "taxID": "11222333000182", "url": "https://x.com.br"})
    assert com_url[0].sujeito_ref == "url:https://x.com.br"
    sem_nada, _ = _gerar({"@type": "Organization", "name": "X"})
    assert sem_nada[0].sujeito_ref == f"mencao:{HASH}#json-ld[0]"


def test_graph_tipo_em_lista_e_caminho_do_item():
    candidatas, _ = _gerar({"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "ignorado"},
        {"@type": ["Thing", "NewsArticle"], "headline": "Prefeitura investiga", "url": "https://jornal.example/n/1"},
    ]})
    [c] = candidatas
    assert c.predicado == "schema:headline" and c.sujeito_ref == "url:https://jornal.example/n/1"
    assert c.localizador["item"] == "json-ld[0]/@graph[1]"


def test_artigo_relacoes_author_e_publisher():
    candidatas, _ = _gerar({
        "@type": "NewsArticle", "url": "https://jornal.example/n/1", "datePublished": "2026-10-02",
        "author": [{"@type": "Person", "name": "Ana Souza"}, "Beto Lima"],
        "publisher": {"@type": "Organization", "name": "Jornal Exemplo", "url": "https://jornal.example"},
    })
    autores = [c for c in candidatas if c.predicado == "schema:author"]
    assert {c.objeto_literal for c in autores} == {"Ana Souza", "Beto Lima"}
    [pub] = [c for c in candidatas if c.predicado == "schema:publisher"]
    assert pub.objeto_ref == "url:https://jornal.example" and pub.objeto_literal is None


def test_pessoa_so_gera_atributos_da_lista_curta():
    candidatas, _ = _gerar({"@type": "Person", "name": "João", "jobTitle": "Diretor", "email": "j@x.com",
                            "birthDate": "1980-01-01", "worksFor": {"name": "Empresa X"}})
    assert {c.predicado for c in candidatas} == {"schema:name", "schema:jobTitle", "schema:worksFor"}


def test_valores_estruturados_demais_ou_vazios_sao_ignorados_e_contados():
    candidatas, ignoradas = _gerar({"@type": "Organization", "name": {"@value": "x"}, "legalName": "  ",
                                    "telephone": 4130000000, "foundingDate": "x" * 3000})
    assert [c.predicado for c in candidatas] == ["schema:telephone"] and ignoradas == 3


def test_sem_host_ou_sem_jsonld_nao_gera_nada():
    item = {"@type": "Organization", "name": "X"}
    assert claims_extruct.gerar({"raw": {"json-ld": [item]}}, url_captura="", blob_hash=HASH) == ([], 0)
    assert claims_extruct.gerar({"raw": {"microdata": [item]}}, url_captura=URL, blob_hash=HASH) == ([], 0)
    assert claims_extruct.gerar(None, url_captura=URL, blob_hash=HASH) == ([], 0)


# ── produtor extruct (com banco e extruct de verdade) ───────────────────────

HTML = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Empresa X","taxID":"11.222.333/0001-81",
 "url":"https://x.com.br"}
</script></head><body><p>texto</p></body></html>"""


def _dados_reais():
    from apps.artifacts.extractors import extruct_extractor

    return extruct_extractor.extract(HTML, "https://www.jornal.example/a")


def test_registrar_com_extruct_de_verdade_e_idempotente(artefato):
    dados = _dados_reais()
    c1 = claims_extruct.registrar(artefato, dados, HASH)
    c2 = claims_extruct.registrar(artefato, dados, HASH)
    assert c1.criadas == 3 and c1.existentes == 0 and c1.rejeitadas == 0
    assert c2.criadas == 0 and c2.existentes == 3
    assert Claim.objects.count() == 3 and Evidence.objects.count() == 3
    claim = Claim.objects.get(predicado="schema:taxID")
    assert claim.produtor == "extruct-jsonld" and claim.produtor_versao.startswith("1/extruct-")
    assert claim.autor_ref == "dominio:jornal.example" and claim.classification_level == "confidencial"


def test_registrar_nunca_levanta(artefato):
    with mock.patch("apps.artifacts.extractors.claims_extruct.registrar_alegacao", side_effect=RuntimeError("x")):
        contagem = claims_extruct.registrar(artefato, _dados_reais(), HASH)
    assert contagem.criadas == 0 and contagem.rejeitadas == 3
    with mock.patch("apps.artifacts.extractors.claims_extruct.gerar", side_effect=RuntimeError("x")):
        assert claims_extruct.registrar(artefato, _dados_reais(), HASH).total == 0


# ── integração com a task de extração ───────────────────────────────────────

def _mhtml(html: str) -> bytes:
    msg = MIMEMultipart("related")
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_bytes()


@pytest.fixture
def extracao(org):
    """Roda `extract_text_from_mhtml` de ponta a ponta, com S3, detecção e fila falsos."""
    from apps.artifacts import tasks

    mhtml = _mhtml(HTML)

    def rodar(artefato):
        resposta = mock.MagicMock()
        resposta.read.return_value = mhtml
        with mock.patch.object(tasks, "Minio") as minio, \
                mock.patch("apps.artifacts.extractors.detect_page_type", return_value=("artigo", 0.9, "teste", None, None)), \
                mock.patch.object(tasks.fragment_text, "delay"):
            minio.return_value.get_object.return_value = resposta
            return tasks.extract_text_from_mhtml.run(str(artefato.id)), mhtml
    return rodar


def test_a_extracao_registra_as_alegacoes_e_preenche_o_blob_hash_que_faltava(org, extracao):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        content={"url": "https://www.jornal.example/a", "mhtml_path": "x.mhtml"},
    )
    assert artefato.blob_hash is None
    resultado, mhtml = extracao(artefato)
    assert resultado["status"] == "success"
    artefato.refresh_from_db()
    assert artefato.blob_hash == hash_ni(mhtml)
    assert Claim.objects.filter(artefato=artefato).count() == 3
    assert set(Evidence.objects.values_list("blob_hash", flat=True)) == {hash_ni(mhtml)}


def test_reprocessar_nao_duplica_alegacoes(org, extracao):
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        content={"url": "https://www.jornal.example/a", "mhtml_path": "x.mhtml"},
    )
    from apps.artifacts import tasks

    extracao(artefato)
    with mock.patch.object(tasks, "Minio") as minio, \
            mock.patch("apps.artifacts.extractors.detect_page_type", return_value=("artigo", 0.9, "teste", None, None)), \
            mock.patch.object(tasks.fragment_text, "delay"):
        minio.return_value.get_object.return_value.read.return_value = _mhtml(HTML)
        tasks.extract_text_from_mhtml.run(str(artefato.id), True)
    assert Claim.objects.filter(artefato=artefato).count() == 3


def test_falha_nas_alegacoes_nao_derruba_a_extracao(org, extracao):
    from apps.artifacts.models import DocumentText

    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        content={"url": "https://www.jornal.example/a", "mhtml_path": "x.mhtml"},
    )
    with mock.patch("apps.artifacts.extractors.claims_extruct.registrar", side_effect=RuntimeError("x")):
        resultado, _ = extracao(artefato)
    assert resultado["status"] == "success" and DocumentText.objects.filter(document=artefato).exists()

"""F1b: pacote offline assinado. Uma instância só, simulando as duas pontas.

Truque do teste: a chave da instância é uma só (a do banco). A org A exporta para um par
cujo `did` é o da própria instância; depois os artefatos são apagados e a org B importa, com
um par (A) de mesmo `did`. Adulterações e emissores de fora usam `ChaveOutraInstancia`.
"""
import io
import json
import zipfile
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact, AuditLog, Claim, DocumentText
from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao
from apps.cluster.models import Maquina
from apps.federacao import blobs, envelope, espacos, pacote, politica
from apps.federacao.chaves import chave_ativa, garantir_chave_ativa
from apps.federacao.models import EventoFederado, Space
from config.conteudo_hash import hash_ni

pytestmark = pytest.mark.django_db

MHTML = b"MIME-Version: 1.0\r\nContent-Type: multipart/related\r\n\r\n<html>ola</html>"


@pytest.fixture(autouse=True)
def armazenamento(monkeypatch):
    """S3 em memória: tudo que `blobs` lê/grava passa por aqui."""
    guardado = {}
    monkeypatch.setattr(blobs, "ler_blob", lambda b, c: guardado[(b, c)])
    monkeypatch.setattr(blobs, "gravar_blob", lambda b, c, d, tipo="": guardado.__setitem__((b, c), d))
    return guardado


@pytest.fixture
def chave():
    return garantir_chave_ativa()[0]


def _org(slug, dono=None):
    dono = dono or User.objects.create_user(username=f"d-{slug}", password="x")
    return Organization.objects.create(name=slug, slug=slug, org_type="individual", owner=dono)


def _par(org, did, tipo="proprio", estado="confirmado", apelido="outra"):
    return Maquina.objects.create(
        organizacao=org, dono=org.owner, apelido=apelido, did=did, tipo=tipo, estado=estado, eh_local=False,
    )


def _artefato(org, armazenamento, nivel="restrito", url="https://x.example/a", com_blob=True):
    a = Artifact.objects.create(
        artifact_type="documento", content={"url": url, "title": "T", "nota": 0.5}, tenant=org,
        classification_level=nivel, info_type="fato", sources=["s"],
    )
    if com_blob:
        caminho = f"{a.id}.mhtml"
        armazenamento[(blobs.BUCKET_MHTML, caminho)] = MHTML
        a.content = {**a.content, "mhtml_bucket": blobs.BUCKET_MHTML, "mhtml_path": caminho}
        a.blob_hash = hash_ni(MHTML)
        a.save()
    DocumentText.objects.create(document=a, text="corpo do texto", title="T", source_url=url, page_type="noticia",
                                structured_data=None, dados_estruturados_extruct={"x": [1, 2.5]},
                                extractor_version="v1", char_count=14, word_count=3)
    registrar_alegacao(
        artefato=a, sujeito_ref=f"url:{url}", predicado="schema:headline", autor_ref="dominio:x.example",
        produtor="extruct", produtor_versao="1", objeto_literal="Manchete", confianca=0.72,
        evidencias=[EvidenciaEntrada(blob_hash=hash_ni(MHTML), localizador_tipo="json-ld", localizador={"i": 0}, trecho="Manchete")],
    )
    return a


@pytest.fixture
def cenario(chave, armazenamento):
    """org A com espaço e artefato; org B com o par A. Devolve tudo."""
    a = _org("a")
    b = _org("b")
    par_para_b = _par(a, chave.did, apelido="b")          # em A, o par B (mesmo did de teste)
    par_a_em_b = _par(b, chave.did, apelido="a")          # em B, o par A
    esp = espacos.criar_espaco(organizacao=a, nome="familia")
    art = _artefato(a, armazenamento)
    espacos.incluir_artefato(esp, art)
    return dict(a=a, b=b, par_b=par_para_b, par_a=par_a_em_b, esp=esp, art=art)


def _como_instancia_nova(art):
    """Apaga o artefato e o espaço locais: a importação precisa ser de objetos desconhecidos."""
    # Claim protege o Artifact (PROTECT): limpa por SQL de teste.
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("DELETE FROM artifacts_evidence WHERE claim_id IN (SELECT id FROM artifacts_claim WHERE artefato_id=%s)", [art.id])
        cur.execute("DELETE FROM artifacts_claim WHERE artefato_id=%s", [art.id])
    Space.objects.all().delete()
    art.delete()


def test_roundtrip_proprio(cenario, armazenamento):
    dados, r = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    assert (r.enviados, r.negados) == (1, 0)
    art = cenario["art"]
    pk, blob_hash = art.id, art.blob_hash
    _como_instancia_nova(art)
    armazenamento.clear()

    res = pacote.importar(organizacao=cenario["b"], dados=dados)
    assert (res.importados, res.recusados, res.alegacoes, res.erros) == (1, 0, 1, [])
    novo = Artifact.objects.get(pk=pk)                       # mantém o UUID de origem
    assert novo.tenant_id == cenario["b"].id                 # par próprio: cai na org escolhida
    assert novo.classification_level == "restrito" and novo.blob_hash == blob_hash
    assert not novo.allow_external_llm
    assert armazenamento[(blobs.BUCKET_MHTML, f"{pk}.mhtml")] == MHTML
    assert "federado" in novo.content and novo.content["nota"] == "0.5"   # float virou texto
    assert novo.extracted_text.text == "corpo do texto"
    assert Claim.objects.get(artefato=novo).classification_level == "restrito"
    assert Space.objects.get(pk=cenario["esp"].id).organizacao_id == cenario["b"].id
    assert EventoFederado.objects.filter(direcao="recebido").count() == 1
    assert EventoFederado.objects.filter(direcao="emitido").count() == 1


def test_importacao_e_idempotente(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    _como_instancia_nova(cenario["art"])
    assert pacote.importar(organizacao=cenario["b"], dados=dados).importados == 1
    r = pacote.importar(organizacao=cenario["b"], dados=dados)
    assert (r.importados, r.ja_existiam) == (0, 1)
    assert Artifact.objects.count() == 1


def test_objeto_que_ja_existe_so_ganha_o_espaco(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    # o artefato continua em A; B (mesmo banco) o recebe: fica no tenant de origem
    res = pacote.importar(organizacao=cenario["b"], dados=dados)
    assert (res.importados, res.associados) == (0, 1)
    assert Artifact.objects.get(pk=cenario["art"].id).tenant_id == cenario["a"].id


def test_terceiro_cai_em_organizacao_dedicada(cenario, chave):
    cenario["par_a"].tipo = "terceiro"
    cenario["par_a"].save()
    cenario["par_b"].tipo = "terceiro"
    cenario["par_b"].save()
    # terceiro só recebe publico, ou restrito por concessão em lote (ADR 018)
    politica.conceder_espaco(cenario["a"], cenario["esp"], par_ref="b", valida_ate=timezone.now() + timedelta(days=30))
    dados, r = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    assert r.enviados == 1
    _como_instancia_nova(cenario["art"])
    res = pacote.importar(organizacao=cenario["b"], dados=dados)
    assert res.importados == 1
    novo = Artifact.objects.get()
    assert novo.tenant_id != cenario["b"].id and novo.tenant.name.startswith("Federado: ")


def test_terceiro_sem_concessao_nao_leva_restrito(cenario):
    cenario["par_b"].tipo = "terceiro"
    cenario["par_b"].save()
    _, r = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    assert (r.enviados, r.negados) == (0, 1)
    assert AuditLog.objects.filter(operation="replicacao.enviar", outcome="bloqueado").exists()


def test_receptor_recusa_com_a_propria_regra(cenario):
    from apps.federacao.models import RegraReplicacao

    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    _como_instancia_nova(cenario["art"])
    politica.garantir_regras_padrao(cenario["b"])
    RegraReplicacao.objects.create(organizacao=cenario["b"], efeito="negar", sentido="receber", nivel="restrito")
    res = pacote.importar(organizacao=cenario["b"], dados=dados)
    assert (res.importados, res.recusados) == (0, 1)
    assert not Artifact.objects.exists()
    assert not EventoFederado.objects.filter(direcao="recebido").exists()


def _reempacotar(dados, troca):
    """Devolve um zip igual ao original com `troca(nome, bytes) -> bytes|None` aplicada."""
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(dados)) as zin, zipfile.ZipFile(saida, "w") as zout:
        for info in zin.infolist():
            novo = troca(info.filename, zin.read(info.filename))
            if novo is not None:
                zout.writestr(info.filename, novo)
    return saida.getvalue()


def test_blob_adulterado_e_recusado_e_nada_e_aplicado(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    ruim = _reempacotar(dados, lambda n, b: b + b"x" if n.startswith("blobs/") else b)
    _como_instancia_nova(cenario["art"])
    with pytest.raises(pacote.ErroPacote, match="blob"):
        pacote.importar(organizacao=cenario["b"], dados=ruim)
    assert not Artifact.objects.exists()


def test_evento_adulterado_e_recusado(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])

    def troca(n, b):
        if n.startswith("eventos/"):
            env = json.loads(b)
            env["payload"]["nivel"] = "publico"          # rebaixar a classificação
            return json.dumps(env).encode()
        return b
    _como_instancia_nova(cenario["art"])
    with pytest.raises(pacote.ErroPacote, match="id não confere|recusado"):
        pacote.importar(organizacao=cenario["b"], dados=_reempacotar(dados, troca))


def test_evento_a_menos_ou_a_mais_no_pacote_e_recusado(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    sem_evento = _reempacotar(dados, lambda n, b: None if n.startswith("eventos/") else b)
    with pytest.raises(pacote.ErroPacote):
        pacote.importar(organizacao=cenario["b"], dados=sem_evento)


def test_emissor_desconhecido_ou_nao_confirmado(cenario, chave):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    cenario["par_a"].estado = "pendente"
    cenario["par_a"].save()
    with pytest.raises(pacote.ErroPacote, match="par confirmado"):
        pacote.importar(organizacao=cenario["b"], dados=dados)
    cenario["par_a"].estado = "revogado"
    cenario["par_a"].save()
    with pytest.raises(pacote.ErroPacote, match="par confirmado"):
        pacote.importar(organizacao=cenario["b"], dados=dados)


def test_pacote_para_outra_instancia_e_recusado(cenario):
    from tests._instancias import ChaveOutraInstancia

    outro = ChaveOutraInstancia()
    cenario["par_b"].did = outro.did            # o pacote sai endereçado a outra instância
    cenario["par_b"].save()
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    with pytest.raises(pacote.ErroPacote, match="não é para esta instância"):
        pacote.importar(organizacao=cenario["b"], dados=dados)


def test_arquivo_estranho_no_zip_e_recusado(cenario):
    dados, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(dados)) as zin, zipfile.ZipFile(saida, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
        zout.writestr("../../etc/passwd", b"x")
    with pytest.raises(pacote.ErroPacote, match="inesperado"):
        pacote.importar(organizacao=cenario["b"], dados=saida.getvalue())
    with pytest.raises(pacote.ErroPacote, match="zip"):
        pacote.importar(organizacao=cenario["b"], dados=b"nao e zip")


def test_exportar_exige_par_confirmado_e_chave(cenario):
    cenario["par_b"].estado = "pendente"
    cenario["par_b"].save()
    with pytest.raises(pacote.ErroPacote, match="confirmado"):
        pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])


def test_cadeia_prev_continua_entre_exportacoes(cenario, armazenamento):
    d1, _ = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    art2 = _artefato(cenario["a"], armazenamento, url="https://x.example/b")
    espacos.incluir_artefato(cenario["esp"], art2)
    d2, r = pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    emitidos = list(EventoFederado.objects.filter(direcao="emitido").order_by("criado_em", "id"))
    assert len(emitidos) == 3                       # o 1º reenviado + o novo (cada export é um fato)
    assert emitidos[-1].prev == emitidos[-2].evento_id


def test_envelope_rejeita_extensao_obrigatoria_e_assinatura_de_outro(chave):
    from tests._instancias import ChaveOutraInstancia

    esp = "urn:uuid:11111111-1111-4111-8111-111111111111"
    env = envelope.criar(chave, espaco_urn=esp, tipo="t", payload={}, prev=None)
    envelope.verificar(json.dumps(env))
    com_must = dict(env, must_understand=["x"])
    with pytest.raises(envelope.ErroEnvelope):
        envelope.verificar(json.dumps(com_must))
    outro = ChaveOutraInstancia()
    falso = dict(env, author=outro.did)         # troca o autor: id e assinatura deixam de conferir
    with pytest.raises(envelope.ErroEnvelope):
        envelope.verificar(json.dumps(falso))


def test_evento_federado_e_append_only(cenario):
    pacote.exportar(organizacao=cenario["a"], espaco=cenario["esp"], par=cenario["par_b"])
    ev = EventoFederado.objects.get()
    with pytest.raises(ValueError):
        ev.save()
    with pytest.raises(ValueError):
        ev.delete()


def test_comandos_exportar_e_importar(cenario, tmp_path):
    from django.core.management import call_command

    arq = tmp_path / "p.zip"
    saida = io.StringIO()
    call_command("exportar_pacote", organizacao="a", espaco="familia", par="b", saida=str(arq), stdout=saida)
    assert "1 enviado(s)" in saida.getvalue() and arq.exists()
    _como_instancia_nova(cenario["art"])
    saida = io.StringIO()
    call_command("importar_pacote", organizacao="b", arquivo=str(arq), stdout=saida)
    assert "1 importado(s)" in saida.getvalue()

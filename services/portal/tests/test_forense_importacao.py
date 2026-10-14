"""Fluxo B: itens escolhidos no IPED → acervo. O runner/IPED são simulados com um JSONL; o Garage também."""
import json

import pytest

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact, OrigemArquivo
from apps.federacao import blobs
from apps.forense import fila, importacao
from apps.forense.models import ProcessamentoIPED


@pytest.fixture
def garage(monkeypatch):
    guardado = {}
    monkeypatch.setattr(blobs, "garantir_bucket", lambda b: None)
    monkeypatch.setattr(blobs, "existe_blob", lambda b, c: c in guardado)
    monkeypatch.setattr(blobs, "gravar_arquivo",
                        lambda bucket, caminho, origem, tipo="x": guardado.__setitem__(caminho, open(origem, "rb").read()))
    return guardado


@pytest.fixture
def pedido(db, tmp_path, settings):
    raiz = tmp_path / "entrada"
    (raiz / "fotos").mkdir(parents=True)
    (raiz / "fotos" / "a.jpg").write_bytes(b"jpg-bytes")
    settings.IPED_RAIZES_PERMITIDAS = [str(raiz)]
    settings.IPED_TRABALHO_DIR = str(tmp_path / "trabalho")
    dono = User.objects.create_user(username="d", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    return ProcessamentoIPED.objects.create(
        organizacao=org, raiz=str(raiz), relativo=".", perfil="fastmode", estado="concluido")


def jsonl(tmp_path, itens):
    arq = tmp_path / "itens.jsonl"
    arq.write_text("\n".join(json.dumps(i) for i in itens))
    return arq


def item(**kw):
    base = {"id": "1", "name": "a.jpg", "path": "entrada/fotos/a.jpg", "idInDataSource": "fotos/a.jpg",
            "category": "Other Images", "contentType": "image/jpeg", "isDir": "false", "subitem": "false",
            "deleted": "false", "carved": "false", "hashes": {}}
    base.update(kw)
    return base


def test_importa_o_arquivo_original_e_guarda_a_proveniencia(pedido, garage, tmp_path):
    r = importacao.importar_itens(pedido, jsonl(tmp_path, [item()]))
    assert r.do_acervo.novos == 1
    art = Artifact.objects.get(artifact_type="arquivo")
    assert art.content["categoria"] == "foto"
    assert art.content["externo"][f"iped:{pedido.id}"]["categoria"] == "Other Images"
    assert OrigemArquivo.objects.get().relativo == "fotos/a.jpg"


def test_pasta_subitem_apagado_e_fuga_nao_viram_arquivo(pedido, garage, tmp_path):
    itens = [item(id="2", isDir="true"), item(id="3", subitem="true"), item(id="4", deleted="true"),
             item(id="5", idInDataSource="../../etc/passwd"), item(id="6", idInDataSource="nao/existe.jpg")]
    r = importacao.importar_itens(pedido, jsonl(tmp_path, itens))
    assert (r.pastas, r.sem_arquivo_no_disco, r.do_acervo.novos) == (1, 4, 0)
    assert not Artifact.objects.exists()


def test_hash_do_iped_divergente_e_erro_e_nao_importa(pedido, garage, tmp_path):
    r = importacao.importar_itens(pedido, jsonl(tmp_path, [item(hashes={"sha-256": "0" * 64})]))
    assert r.do_acervo.total_erros == 1 and not Artifact.objects.exists()


def test_hash_do_iped_correto_passa(pedido, garage, tmp_path):
    import hashlib
    h = hashlib.sha256(b"jpg-bytes").hexdigest()
    r = importacao.importar_itens(pedido, jsonl(tmp_path, [item(hashes={"sha-256": h})]))
    assert r.do_acervo.novos == 1 and r.do_acervo.total_erros == 0


def test_simular_nao_grava(pedido, garage, tmp_path):
    r = importacao.importar_itens(pedido, jsonl(tmp_path, [item()]), simular=True)
    assert r.do_acervo.novos == 1 and not garage and not Artifact.objects.exists()


def test_exige_processamento_concluido(pedido):
    pedido.estado = "executando"
    with pytest.raises(importacao.ErroImportacao):
        importacao.exportar_itens(pedido, consulta="*")


def test_pedido_de_exportacao_exige_bookmark_ou_consulta_e_so_um(pedido):
    with pytest.raises(importacao.ErroImportacao):
        importacao.exportar_itens(pedido)                       # nem um nem outro: nada de "caso inteiro"
    with pytest.raises(importacao.ErroImportacao):
        importacao.exportar_itens(pedido, bookmark="a", consulta="b")
    with pytest.raises(importacao.ErroImportacao):
        importacao.exportar_itens(pedido, consulta="a\nperfil=forensic")   # injeção de linha no protocolo


def test_pedido_escrito_para_o_runner(pedido):
    fila.enviar_exportacao("11111111-1111-1111-1111-111111111111", caso=pedido.id, bookmark="fotos")
    txt = (fila.trabalho() / "pedidos" / "11111111-1111-1111-1111-111111111111.pedido").read_text()
    assert txt == f"acao=exportar\ncaso={pedido.id}\nbookmark=fotos\n"

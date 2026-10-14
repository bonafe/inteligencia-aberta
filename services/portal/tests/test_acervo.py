"""Acervo pessoal (Fluxo A): hash, deduplicação, retomada, projetos git e simulação. Sem rede: o Garage é simulado."""
import subprocess

import pytest

from apps.accounts.models import Organization, User
from apps.artifacts import acervo
from apps.artifacts.models import Artifact, OrigemArquivo
from apps.federacao import blobs


@pytest.fixture
def garage(monkeypatch):
    guardado = {}
    monkeypatch.setattr(blobs, "garantir_bucket", lambda b: None)
    monkeypatch.setattr(blobs, "existe_blob", lambda b, c: c in guardado)

    def gravar(bucket, caminho, origem, tipo="x"):
        guardado[caminho] = open(origem, "rb").read()

    monkeypatch.setattr(blobs, "gravar_arquivo", gravar)
    return guardado


@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="d", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


def importar(org, base, **kw):
    raiz = base if base.is_dir() else base.parent
    return acervo.importar_pasta(organizacao=org, usuario=None, base=base, raiz=str(raiz), **kw)


def test_categoria_pelo_sufixo():
    assert acervo.categoria_de("IMG_1.JPG") == "foto"
    assert acervo.categoria_de("a.pdf") == "pdf"
    assert acervo.categoria_de("x.py") == "codigo"
    assert acervo.categoria_de("sem_extensao") == "outro"


def test_importa_e_guarda_pelo_hash(org, garage, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"ola")
    r = importar(org, tmp_path)
    assert (r.novos, r.duplicados, r.total_erros) == (1, 0, 0)
    art = Artifact.objects.get(artifact_type="arquivo")
    assert art.blob_hash.startswith("ni:///sha-256;")
    assert art.classification_level == "restrito" and art.allow_external_llm is False
    assert garage[art.content["caminho"]] == b"ola"
    assert art.content["categoria"] == "texto"


def test_mesmo_conteudo_em_dois_lugares_e_um_artefato_duas_origens(org, garage, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"igual")
    (tmp_path / "copia.txt").write_bytes(b"igual")
    r = importar(org, tmp_path)
    assert (r.novos, r.duplicados) == (1, 1)
    assert Artifact.objects.filter(artifact_type="arquivo").count() == 1
    assert OrigemArquivo.objects.count() == 2
    assert len(garage) == 1


def test_retomar_pula_sem_reler(org, garage, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_bytes(b"x")
    importar(org, tmp_path)
    monkeypatch.setattr(acervo, "hash_do_arquivo", lambda *_: pytest.fail("não devia reler"))
    r = importar(org, tmp_path)
    assert (r.pulados, r.novos) == (1, 0)


def test_arquivo_alterado_vira_novo_conteudo(org, garage, tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"v1")
    importar(org, tmp_path)
    f.write_bytes(b"versao dois")
    r = importar(org, tmp_path)
    assert r.novos == 1
    assert Artifact.objects.filter(artifact_type="arquivo").count() == 2


def test_simular_nao_grava(org, garage, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x")
    r = importar(org, tmp_path, simular=True)
    assert r.novos == 1
    assert not garage and not Artifact.objects.exists() and not OrigemArquivo.objects.exists()


def test_link_simbolico_nao_e_seguido(org, garage, tmp_path):
    fora = tmp_path / "fora.txt"
    fora.write_bytes(b"segredo")
    pasta = tmp_path / "acervo"
    pasta.mkdir()
    (pasta / "atalho.txt").symlink_to(fora)
    r = importar(org, pasta)
    assert r.arquivos == 0 and not garage


def test_erro_em_um_arquivo_nao_para_os_outros(org, garage, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    real = acervo.hash_do_arquivo

    def quebra(caminho):
        if str(caminho).endswith("a.txt"):
            raise PermissionError("negado")
        return real(caminho)

    monkeypatch.setattr(acervo, "hash_do_arquivo", quebra)
    r = importar(org, tmp_path)
    assert r.novos == 1 and r.total_erros == 1 and "a.txt" in r.erros[0]


def test_nivel_invalido(org, garage, tmp_path):
    with pytest.raises(ValueError):
        importar(org, tmp_path, nivel="secreto")


def test_projeto_git_vira_um_artefato(org, garage, tmp_path):
    proj = tmp_path / "meu_projeto"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    (proj / "main.py").write_text("print(1)")
    (proj / "lib.py").write_text("x=1")
    r = importar(org, tmp_path)
    assert r.projetos == 1
    arts = Artifact.objects.filter(artifact_type="arquivo")
    assert arts.count() == 1 and arts[0].content["projeto_git"] is True
    assert arts[0].content["categoria"] == "codigo"
    # retomar não reempacota
    assert importar(org, tmp_path).pulados == 1


def test_sem_projetos_trata_como_arquivos_soltos(org, garage, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    (proj / "main.py").write_text("print(1)")
    r = importar(org, tmp_path, projetos=False)
    assert r.projetos == 0 and r.novos >= 1

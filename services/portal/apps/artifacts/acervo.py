"""Acervo pessoal: guardar e catalogar arquivos (fotos, vídeos, textos, livros, PDFs, código).

Fluxo A: o IA guarda primeiro, sem depender de nada externo. Para cada arquivo:

    hash SHA-256 (em streaming)  →  blob no Garage, nomeado pelo hash  →  `Artifact(tipo=arquivo)`

- **Deduplicação:** o blob é gravado uma vez por conteúdo; um mesmo conteúdo em vários lugares dá
  um só `Artifact` e uma `OrigemArquivo` por lugar.
- **Retomável:** uma origem com o mesmo tamanho e mtime já importada é pulada **sem reler o
  arquivo** (importar terabytes de novo só para descobrir que nada mudou seria inviável).
- **Nada é reprocessado:** o artefato nasce catalogado e mais nada; texto, OCR e miniaturas
  são etapas separadas, por cima do hash.
- **Projetos de código:** uma pasta com `.git` vira **um** artefato (um `tar` do projeto inteiro,
  histórico incluído), não milhares de arquivos soltos.
- Links simbólicos e arquivos especiais não são seguidos nem importados.

O nível de classificação padrão é `restrito` e o artefato nunca permite LLM externo.
"""

import base64
import hashlib
import mimetypes
import os
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from apps.events.emit import emit
from config.conteudo_hash import PREFIXO

from apps.federacao import blobs

from .models import Artifact, OrigemArquivo

TAMANHO_LEITURA = 8 * 1024 * 1024
#: Limite de erros guardados no resumo (o resto só é contado).
MAX_ERROS = 50

_EXT = {
    "foto": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".raw",
             ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".mts"},
    "audio": {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".opus"},
    "pdf": {".pdf"},
    "livro": {".epub", ".mobi", ".azw", ".azw3", ".djvu", ".fb2"},
    "texto": {".txt", ".md", ".rtf", ".odt", ".doc", ".docx", ".tex", ".org", ".rst", ".csv", ".tsv",
              ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".html", ".htm", ".mhtml", ".eml", ".json"},
    "codigo": {".py", ".js", ".ts", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
               ".sh", ".sql", ".pl", ".lua", ".kt", ".swift", ".m", ".r", ".css", ".vb", ".pas", ".asm"},
    "compactado": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst"},
}


def categoria_de(nome: str) -> str:
    """Categoria pelo sufixo do nome (foto, video, audio, pdf, livro, texto, codigo, compactado, outro)."""
    ext = Path(nome).suffix.lower()
    for categoria, exts in _EXT.items():
        if ext in exts:
            return categoria
    return "outro"


def caminho_do_blob(hash_ni: str) -> str:
    """`sha256/ab/cd/<hex>` — o prefixo espalha os objetos em "pastas" do bucket."""
    b64 = hash_ni[len(PREFIXO):]
    hexa = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).hex()
    return f"sha256/{hexa[:2]}/{hexa[2:4]}/{hexa}"


def hash_do_arquivo(caminho) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as fh:
        while bloco := fh.read(TAMANHO_LEITURA):
            resumo.update(bloco)
    return PREFIXO + base64.urlsafe_b64encode(resumo.digest()).rstrip(b"=").decode("ascii")


@dataclass
class Resumo:
    arquivos: int = 0          # vistos
    novos: int = 0             # artefato criado
    duplicados: int = 0        # conteúdo já no acervo (só ganhou uma origem)
    pulados: int = 0           # origem já importada, sem mudança
    projetos: int = 0
    bytes_novos: int = 0
    erros: list = field(default_factory=list)
    total_erros: int = 0

    def erro(self, relativo: str, exc: Exception):
        self.total_erros += 1
        if len(self.erros) < MAX_ERROS:
            self.erros.append(f"{relativo}: {exc}")


def _percorrer(base: Path, projetos: bool):
    """Gera (caminho, eh_projeto). Uma pasta com `.git` é devolvida inteira como projeto."""
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        atual = Path(dirpath)
        if projetos and ".git" in dirnames + filenames:
            dirnames[:] = []
            yield atual, True
            continue
        dirnames.sort()
        for nome in sorted(filenames):
            yield atual / nome, False


def _arquivos_do_projeto(pasta: Path):
    for dirpath, dirnames, filenames in os.walk(pasta, followlinks=False):
        dirnames.sort()
        for nome in sorted(filenames):
            arq = Path(dirpath) / nome
            if not arq.is_symlink() and arq.is_file():
                yield arq


def _medir_projeto(pasta: Path) -> tuple[int, int]:
    """(bytes totais, mtime_ns mais novo): a "assinatura" que permite pular o projeto sem empacotar."""
    total, mais_novo = 0, 0
    for arq in _arquivos_do_projeto(pasta):
        st = arq.stat()
        total += st.st_size
        mais_novo = max(mais_novo, st.st_mtime_ns)
    return total, mais_novo


def _empacotar_projeto(pasta: Path, destino: str) -> None:
    """Cria o tar do projeto (ordem e metadados fixos → mesmo conteúdo, mesmo hash)."""

    def neutro(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(destino, "w") as tar:
        for arq in _arquivos_do_projeto(pasta):
            tar.add(arq, arcname=str(arq.relative_to(pasta.parent)), recursive=False, filter=neutro)


def anexar_meta(artefato, extra):
    """Guarda em `content["externo"][<chave>]` o que um produtor externo (ex.: o IPED) disse do arquivo.

    `extra` = `{"chave": "iped:<caso>", "dados": {...}}`. É informação de proveniência, não fato do sistema.
    """
    if not extra:
        return
    externo = dict(artefato.content.get("externo", {}))
    externo[extra["chave"]] = extra["dados"]
    artefato.content = {**artefato.content, "externo": externo}
    artefato.save(update_fields=["content", "updated_at"])


def _registrar(*, organizacao, usuario, nivel, raiz, relativo, tamanho, mtime_ns, hash_ni, categoria,
               nome, mime, resumo: Resumo, simular: bool, projeto: bool, enviar, extra=None):
    """Cria (ou reaproveita) o artefato do conteúdo e registra a origem. `enviar()` grava o blob se faltar."""
    existente = Artifact.objects.filter(
        tenant=organizacao, artifact_type=Artifact.Type.FILE, blob_hash=hash_ni).first()
    if existente:
        resumo.duplicados += 1
        if not simular:
            OrigemArquivo.objects.update_or_create(
                tenant=organizacao, raiz=raiz, relativo=relativo,
                defaults={"artefato": existente, "tamanho": tamanho, "mtime_ns": mtime_ns})
            anexar_meta(existente, extra)
        return existente
    if simular:
        resumo.novos += 1
        resumo.bytes_novos += tamanho
        return None
    caminho = caminho_do_blob(hash_ni)
    if not blobs.existe_blob(blobs.BUCKET_ACERVO, caminho):
        enviar(caminho)
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.FILE,
        content={"categoria": categoria, "nome": nome, "mime": mime, "tamanho": tamanho,
                 "projeto_git": projeto, "bucket": blobs.BUCKET_ACERVO, "caminho": caminho},
        classification_level=nivel, tenant=organizacao, allow_external_llm=False,
        classified_by=usuario, info_type=Artifact.InfoType.FACT, sources=[],
        blob_hash=hash_ni)
    anexar_meta(artefato, extra)
    OrigemArquivo.objects.update_or_create(
        tenant=organizacao, raiz=raiz, relativo=relativo,
        defaults={"artefato": artefato, "tamanho": tamanho, "mtime_ns": mtime_ns})
    resumo.novos += 1
    resumo.bytes_novos += tamanho
    return artefato


def importar_pasta(*, organizacao, usuario, base: Path, raiz: str, nivel: str = "restrito",
                   simular: bool = False, projetos: bool = True, progresso=None) -> Resumo:
    """Importa `base` (uma pasta ou um arquivo já validado por `forense.caminhos.resolver`).

    `raiz` é a raiz permitida; `relativo` de cada origem é medido a partir dela.
    `progresso(resumo, relativo)` é chamado a cada arquivo. `simular` só conta, sem gravar nada.
    """
    if nivel not in Artifact.ClassificationLevel.values:
        raise ValueError(f"nível desconhecido: {nivel}")
    raiz_p = Path(raiz).resolve()
    resumo = Resumo()
    if not simular:
        blobs.garantir_bucket(blobs.BUCKET_ACERVO)
    if base.is_file():
        itens = [(base, False)]
    else:
        itens = _percorrer(base, projetos)

    for caminho, eh_projeto in itens:
        relativo = str(caminho.relative_to(raiz_p)) if caminho != raiz_p else "."
        try:
            if not eh_projeto and (caminho.is_symlink() or not caminho.is_file()):
                continue
            resumo.arquivos += 1
            if eh_projeto:
                _importar_projeto(caminho, relativo, raiz, organizacao, usuario, nivel, resumo, simular)
            else:
                _importar_arquivo(caminho, relativo, raiz, organizacao, usuario, nivel, resumo, simular)
        except Exception as exc:  # um arquivo ruim (permissão, sumiu no meio) não para a importação
            resumo.erro(relativo, exc)
        if progresso:
            progresso(resumo, relativo)

    emit("acervo.importacao", "ok" if not resumo.total_erros else "falhou", source="portal",
         tenant_id=organizacao.id, user_id=getattr(usuario, "id", None),
         message=f"acervo: {resumo.novos} novo(s), {resumo.duplicados} duplicado(s), "
                 f"{resumo.pulados} pulado(s), {resumo.total_erros} erro(s)",
         payload={"raiz": raiz, "arquivos": resumo.arquivos, "novos": resumo.novos,
                  "duplicados": resumo.duplicados, "pulados": resumo.pulados, "projetos": resumo.projetos,
                  "bytes_novos": resumo.bytes_novos, "erros": resumo.total_erros, "simulado": simular})
    return resumo


def _importar_arquivo(caminho, relativo, raiz, organizacao, usuario, nivel, resumo, simular,
                      extra=None, sha256_esperado=None):
    st = caminho.stat()
    origem = OrigemArquivo.objects.filter(tenant=organizacao, raiz=raiz, relativo=relativo).first()
    if origem and origem.tamanho == st.st_size and origem.mtime_ns == st.st_mtime_ns and not sha256_esperado:
        resumo.pulados += 1
        if extra and not simular:
            anexar_meta(origem.artefato, extra)
        return
    hash_ni = hash_do_arquivo(caminho)
    if sha256_esperado and caminho_do_blob(hash_ni).rsplit("/", 1)[1] != sha256_esperado.lower():
        raise ValueError("o SHA-256 do arquivo difere do que o IPED registrou (o arquivo mudou depois do processamento?)")
    mime = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
    _registrar(organizacao=organizacao, usuario=usuario, nivel=nivel, raiz=raiz, relativo=relativo,
               tamanho=st.st_size, mtime_ns=st.st_mtime_ns, hash_ni=hash_ni,
               categoria=categoria_de(caminho.name), nome=caminho.name, mime=mime, resumo=resumo,
               simular=simular, projeto=False, extra=extra,
               enviar=lambda blob: blobs.gravar_arquivo(blobs.BUCKET_ACERVO, blob, caminho, mime))


def _importar_projeto(pasta, relativo, raiz, organizacao, usuario, nivel, resumo, simular):
    total, mtime_ns = _medir_projeto(pasta)
    origem = OrigemArquivo.objects.filter(tenant=organizacao, raiz=raiz, relativo=relativo).first()
    if origem and origem.tamanho == total and origem.mtime_ns == mtime_ns:
        resumo.pulados += 1
        return
    resumo.projetos += 1
    with tempfile.TemporaryDirectory(prefix="acervo-") as tmp:
        tar_path = os.path.join(tmp, "projeto.tar")
        _empacotar_projeto(pasta, tar_path)
        hash_ni = hash_do_arquivo(tar_path)
        _registrar(organizacao=organizacao, usuario=usuario, nivel=nivel, raiz=raiz, relativo=relativo,
                   tamanho=total, mtime_ns=mtime_ns, hash_ni=hash_ni, categoria="codigo",
                   nome=pasta.name + ".tar", mime="application/x-tar", resumo=resumo, simular=simular,
                   projeto=True,
                   enviar=lambda blob: blobs.gravar_arquivo(blobs.BUCKET_ACERVO, blob, tar_path, "application/x-tar"))

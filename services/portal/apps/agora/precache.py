"""A lista de arquivos que o service worker do Agora guarda para funcionar sem rede.

O `precache.json` gerado do app (`scripts/gerar_precache_ia.sh`) cobre só `static/agora` e `static/agora-ia`.
Os componentes que uma extensão traz moram em outras pastas estáticas e mudam com a instalação, então a lista
servida a quem se registra é a do app **mais** a pasta de cada pack instalado, com a versão recalculada: instalar
ou mudar uma extensão troca o cache sozinho, sem regenerar nada à mão."""
import hashlib
import json
from pathlib import Path

from django.contrib.staticfiles import finders

from apps.extensoes import registro

EXTENSOES = {".js", ".mjs", ".html", ".css", ".json", ".svg", ".png", ".ico", ".woff2"}


def _do_app() -> dict:
    caminho = finders.find("agora-ia/precache.json")
    if not caminho:
        raise FileNotFoundError("agora-ia/precache.json não encontrado")
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _arquivos_do_pack(base: str) -> dict[str, Path]:
    """`/static/<base>/<relativo>` → arquivo no disco, de todas as pastas estáticas que têm `base`."""
    achados: dict[str, Path] = {}
    for pasta in finders.find(base.rstrip("/"), all=True) or []:
        pasta = Path(pasta)
        for arquivo in sorted(pasta.rglob("*")):
            if arquivo.is_file() and arquivo.suffix in EXTENSOES:
                achados.setdefault(f"/static/{base.rstrip('/')}/{arquivo.relative_to(pasta).as_posix()}", arquivo)
    return achados


def montar() -> dict:
    lista = _do_app()
    extras: dict[str, Path] = {}
    for pack in registro.packs():
        extras.update(_arquivos_do_pack(pack.base))
    if not extras:
        return lista
    resumo = hashlib.sha256(lista["version"].encode())
    for url, arquivo in sorted(extras.items()):
        resumo.update(url.encode())
        resumo.update(hashlib.sha256(arquivo.read_bytes()).digest())
    return {"version": resumo.hexdigest()[:16], "files": sorted({*lista["files"], *extras})}

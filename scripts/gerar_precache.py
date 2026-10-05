#!/usr/bin/env python3
"""Gera precache.json: a lista dos arquivos do app que o service worker guarda para funcionar sem rede.

A lista nunca é escrita à mão (esquecer um componente = ele some offline). A versão é o hash do conteúdo, então
mudou um arquivo, mudou a versão, e o service worker troca o cache sozinho.

    tools/gerar_precache.py            reescreve precache.json
    tools/gerar_precache.py --check    falha (exit 1) se o precache.json versionado estiver desatualizado

Uso em outro hospedeiro: --raiz DIR --saida ARQ --prefixo PREFIXO (ver o IA: scripts/sincronizar_agora.sh).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

PASTAS = ["core", "collaboration", "components", "ui", "vendor", "adapters"]
EXTENSOES = {".js", ".mjs", ".html", ".css", ".json", ".svg", ".png", ".ico", ".woff2"}
EXCLUIR = {"README.md", "ORIGEM.txt", "precache.json"}
PASTAS_EXCLUIDAS = {"node_modules", "tests"}


def listar(raiz: Path, pastas, extras=()):
    arquivos = []
    for pasta in pastas:
        base = raiz / pasta
        if base.is_dir():
            arquivos += [p for p in base.rglob("*") if p.is_file() and p.suffix in EXTENSOES and p.name not in EXCLUIR and not PASTAS_EXCLUIDAS & set(p.relative_to(raiz).parts)]
    arquivos += [raiz / e for e in extras if (raiz / e).is_file()]
    return sorted(set(arquivos))


def gerar(raiz: Path, pastas=PASTAS, extras=("index.html",), prefixo="", pagina_raiz=True) -> dict:
    arquivos = listar(raiz, pastas, extras)
    resumo = hashlib.sha256()
    for arquivo in arquivos:
        resumo.update(str(arquivo.relative_to(raiz)).encode())
        resumo.update(hashlib.sha256(arquivo.read_bytes()).digest())
    arquivos_url = [f"{prefixo}{a.relative_to(raiz).as_posix()}" for a in arquivos]
    #A página inicial é pedida como "/" (não "/index.html"): sem esta entrada, recarregar offline não acharia nada
    return {"version": resumo.hexdigest()[:16], "files": ([f"{prefixo}./"] if pagina_raiz else []) + arquivos_url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--raiz", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--saida", default=None)
    ap.add_argument("--prefixo", default="")
    ap.add_argument("--sem-pagina-raiz", action="store_true", help="hospedeiro cuja página inicial não é um arquivo estático (ex.: Django)")
    ap.add_argument("--pastas", nargs="*", default=PASTAS)
    ap.add_argument("--extras", nargs="*", default=["index.html"])
    args = ap.parse_args()
    raiz = Path(args.raiz)
    saida = Path(args.saida) if args.saida else raiz / "precache.json"
    texto = json.dumps(gerar(raiz, args.pastas, args.extras, args.prefixo, not args.sem_pagina_raiz), indent=1, ensure_ascii=False) + "\n"
    if args.check:
        if not saida.exists() or saida.read_text() != texto:
            print(f"{saida} está desatualizado: rode tools/gerar_precache.py", file=sys.stderr)
            sys.exit(1)
        print("precache.json em dia")
        return
    saida.write_text(texto)
    print(f"{saida}: {len(json.loads(texto)['files'])} arquivos")


if __name__ == "__main__":
    main()

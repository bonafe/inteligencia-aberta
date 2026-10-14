#!/usr/bin/env python3
"""Lista os itens de um caso do IPED (por bookmark ou consulta) em JSON Lines — ADR 019.

Roda DENTRO do contêiner `iped` (sem rede): sobe o Web API do IPED só em 127.0.0.1, lê o que
interessa e o derruba. Nada fica escutando; quem chama (o runner) só enxerga o arquivo de saída.

    exportar_itens.py <pasta-do-caso> <saida.jsonl> (--bookmark NOME | --consulta 'lucene')

`<pasta-do-caso>` é a pasta que contém `iped/` (o `-o` do processamento). Uma linha por item, só com
metadados (nunca conteúdo): o IA lê o arquivo original da raiz permitida e calcula o próprio hash.
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

FONTE = "c1"
#: Propriedades do IPED que viram campos da linha (as demais não são necessárias).
CAMPOS = ("id", "name", "path", "idInDataSource", "dataSourcePath", "size", "category", "contentType", "ext",
          "isDir", "subitem", "deleted", "carved", "extMismatch", "evidenceUUID", "parentIds", "hasChildren")


def _primeiro(valor):
    return valor[0] if isinstance(valor, list) and valor else valor


class Cliente:
    def __init__(self, porta):
        self.conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=60)

    def get(self, caminho):
        self.conexao.request("GET", caminho)
        resposta = self.conexao.getresponse()
        corpo = resposta.read()
        if resposta.status != 200:
            raise RuntimeError(f"GET {caminho}: HTTP {resposta.status}")
        return json.loads(corpo)


def ids_da_resposta(resposta):
    ids = []
    for bloco in resposta.get("data", []):
        if isinstance(bloco, dict):
            ids.extend(bloco.get("ids", []))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("caso")
    ap.add_argument("saida")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--bookmark")
    grupo.add_argument("--consulta")
    ap.add_argument("--porta", type=int, default=18080)
    a = ap.parse_args()

    lib = os.path.join(a.caso, "iped", "lib")
    if not os.path.isdir(lib):
        print(f"não é um caso do IPED: {a.caso}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        fontes = os.path.join(tmp, "fontes.json")
        with open(fontes, "w") as fh:
            json.dump([{"id": FONTE, "path": a.caso}], fh)
        servidor = subprocess.Popen(
            ["java", f"-Duser.home={os.environ.get('HOME', '/tmp')}", "-cp", "iped-webapi.jar:*",
             "iped.engine.webapi.Main", f"--sources={fontes}", "--host=127.0.0.1", f"--port={a.porta}"],
            cwd=lib, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        try:
            cliente = None
            for _ in range(120):
                if servidor.poll() is not None:
                    print("o Web API do IPED encerrou ao subir", file=sys.stderr)
                    return 3
                try:
                    cliente = Cliente(a.porta)
                    cliente.get("/sources")
                    break
                except (OSError, RuntimeError):
                    cliente = None
                    time.sleep(1)
            if cliente is None:
                print("o Web API do IPED não subiu a tempo", file=sys.stderr)
                return 3

            if a.bookmark:
                resposta = cliente.get("/bookmarks/" + urllib.parse.quote(a.bookmark, safe=""))
            else:
                resposta = cliente.get("/search?" + urllib.parse.urlencode({"q": a.consulta, "sourceID": FONTE}))
            ids = ids_da_resposta(resposta)

            with open(a.saida + ".tmp", "w", encoding="utf-8") as saida:
                for i in ids:
                    props = cliente.get(f"/sources/{FONTE}/docs/{i}").get("properties", {})
                    linha = {k: _primeiro(props.get(k)) for k in CAMPOS if k in props}
                    linha["hashes"] = {k: _primeiro(v) for k, v in props.items()
                                       if k.lower() in ("md5", "sha-1", "sha-256", "sha-512", "sha1", "sha256")}
                    saida.write(json.dumps(linha, ensure_ascii=False) + "\n")
            os.replace(a.saida + ".tmp", a.saida)
            print(f"{len(ids)} item(ns) exportado(s)")
            return 0
        finally:
            servidor.terminate()
            try:
                servidor.wait(15)
            except subprocess.TimeoutExpired:
                servidor.kill()


if __name__ == "__main__":
    sys.exit(main())

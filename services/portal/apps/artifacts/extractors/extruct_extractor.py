"""Extração de metadados estruturados embutidos via `extruct`.

Complementa o `dom_parser` (que infere estrutura por repetição de padrões no
DOM, sem depender do que o publicador declarou): `extruct` lê os formatos que
o próprio site anuncia — JSON-LD, Microdata, OpenGraph, RDFa e Microformats
(schema.org Organization/Person/Article/Product, meta tags de redes sociais,
h-card/h-entry). Quando presentes, esses dados costumam ser mais confiáveis
que qualquer heurística, pois vêm do publicador da página.

Determinístico e sem LLM — mesmo perfil de custo do `dom2parser`: seguro para
rodar em toda captura.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SYNTAXES = ["json-ld", "microdata", "opengraph", "rdfa", "microformat"]


def extract(html: str, url: str, diagnostico: dict | None = None) -> dict | None:
    """Retorna os dados embutidos encontrados, ou None se nenhum formato produziu nada.

    `url` é usado por `extruct` para resolver URLs relativas em atributos como
    `image`/`url` dos formatos extraídos — não faz nenhuma requisição de rede.

    Quando um dict é passado em `diagnostico`, ele recebe o motivo de um
    resultado vazio. Sem isso, "a página não declara metadados" e "a biblioteca
    não está instalada" chegavam ao banco como o mesmo `NULL`.
    """
    diag = diagnostico if diagnostico is not None else {}

    try:
        import extruct
    except ImportError:
        diag["motivo"] = "biblioteca extruct não instalada neste processo"
        logger.warning("extruct não instalado — extração de metadados embutidos ignorada")
        return None

    try:
        data = extruct.extract(html, base_url=url or "", syntaxes=SYNTAXES, errors="log")
    except Exception:
        diag["motivo"] = "extruct.extract levantou exceção"
        logger.exception("extruct.extract falhou")
        raise

    found = {k: v for k, v in data.items() if v}
    diag["formatos"] = sorted(found.keys())
    diag["itens_por_formato"] = {k: len(v) for k, v in found.items()}
    if not found:
        diag["motivo"] = "a página não declara nenhum dos formatos suportados"
        return None

    total = sum(len(v) for v in found.values())
    diag["total_itens"] = total
    logger.info("extruct — %d item(ns) em %s", total, list(found.keys()))
    return found


__all__ = ["extract", "SYNTAXES"]

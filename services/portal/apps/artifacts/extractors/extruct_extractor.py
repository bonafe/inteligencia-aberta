"""Extração de metadados estruturados embutidos via `extruct`.

Complementa o `dom_parser` (que infere estrutura por repetição de padrões no
DOM, sem depender do que o publicador declarou): `extruct` lê os formatos que
o próprio site anuncia — JSON-LD, Microdata, OpenGraph e Microformats
(schema.org Organization/Person/Article/Product, meta tags de redes sociais,
h-card/h-entry). Quando presentes, esses dados costumam ser mais confiáveis
que qualquer heurística, pois vêm do publicador da página.

RDFa fora deliberadamente de SYNTAXES: o parser RDFa desta lib trata
atributos ARIA de acessibilidade (`role="dialog"`, `role="button"` etc.) como
triplas RDFa válidas — em páginas comuns isso produz centenas de itens que
não são metadado de conteúdo nenhum (medido: 262 itens de puro ruído numa
única página de busca do Google, sem nenhum dado aproveitável junto).

O retorno tem duas camadas: `resumo` (campos comuns — title/description/
image/url/site_name/type/locale — já achatados e legíveis, vindos do primeiro
formato disponível na ordem json-ld > microdata > opengraph) e `raw` (a saída
crua de cada formato, para quem quiser inspecionar o que o `resumo` deixou de
fora). Sem essa camada, o formato bruto do OpenGraph — uma lista de pares
`["og:title", "valor"]` dentro de `properties` — é tecnicamente correto mas
ilegível para leitura humana no visualizador.

Determinístico e sem LLM — mesmo perfil de custo do `dom2parser`: seguro para
rodar em toda captura.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SYNTAXES = ["json-ld", "microdata", "opengraph", "microformat"]

_CAMPOS_RESUMO = ("title", "description", "image", "url", "site_name", "type", "locale")


def _primeiro_item_de(lista):
    return lista[0] if lista else None


def _resumo_de_opengraph(itens):
    item = _primeiro_item_de(itens)
    if not item:
        return {}
    propriedades = {}
    for chave, valor in item.get("properties", []):
        # "og:title" -> "title"; primeira ocorrência vence (ex.: várias
        # og:image, a primeira costuma ser a imagem principal da página).
        propriedades.setdefault(chave.split(":", 1)[-1], valor)
    return {c: propriedades[c] for c in _CAMPOS_RESUMO if propriedades.get(c)}


def _resumo_de_json_ld(itens):
    item = _primeiro_item_de(itens)
    if not item:
        return {}
    imagem = item.get("image")
    if isinstance(imagem, list):
        imagem = _primeiro_item_de(imagem)
    if isinstance(imagem, dict):
        imagem = imagem.get("url")
    publisher = item.get("publisher") if isinstance(item.get("publisher"), dict) else {}
    resumo = {
        "title": item.get("headline") or item.get("name"),
        "description": item.get("description"),
        "image": imagem,
        "url": item.get("url"),
        "site_name": publisher.get("name"),
        "type": item.get("@type"),
        "locale": item.get("inLanguage"),
    }
    return {k: v for k, v in resumo.items() if v}


def _resumo_de_microdata(itens):
    item = _primeiro_item_de(itens)
    if not item:
        return {}
    propriedades = item.get("properties") or {}
    imagem = propriedades.get("image")
    if isinstance(imagem, list):
        imagem = _primeiro_item_de(imagem)
    tipo = (item.get("type") or "").rsplit("/", 1)[-1]
    resumo = {
        "title": propriedades.get("name") or propriedades.get("headline"),
        "description": propriedades.get("description"),
        "image": imagem,
        "url": propriedades.get("url"),
        "type": tipo,
    }
    return {k: v for k, v in resumo.items() if v}


# Ordem de prioridade: json-ld é o formato mais estruturado (schema.org com
# tipos explícitos), depois microdata, com opengraph como último recurso —
# quase sempre presente, mas só cobre título/descrição/imagem/site.
_RESUMIDORES = (("json-ld", _resumo_de_json_ld), ("microdata", _resumo_de_microdata), ("opengraph", _resumo_de_opengraph))


def _resumir(found: dict) -> dict:
    for formato, resumir_formato in _RESUMIDORES:
        resumo = resumir_formato(found.get(formato))
        if resumo:
            return resumo
    return {}


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
    return {"resumo": _resumir(found), "raw": found}


__all__ = ["extract", "SYNTAXES"]

"""Alegações a partir dos metadados que o próprio site declara (JSON-LD via `extruct`).

É o primeiro produtor de `Claim` (ADR 010): o publicador da página afirma, em
JSON-LD, coisas como "esta organização tem este CNPJ" ou "este artigo foi
publicado em tal data". Aqui isso vira alegações **do site** — nada é tomado como
fato. A autoria é o domínio da captura; o localizador é o caminho do item dentro
do JSON-LD (`json-ld[0]/@graph[2]`) mais a propriedade.

Decisão de granularidade (ADR 010): uma alegação por **atributo ou relação** de
uma entidade — o CNPJ, o nome, o endereço, quem publica —, nunca por linha de
tabela. Só tipos e propriedades de uma lista curta e explícita entram: o que não
está nela é ignorado, não adivinhado. Só JSON-LD por ora (microdata e OpenGraph
não geram alegações).

`gerar` é pura (sem banco); `registrar` grava pelo serviço único
`alegacoes.registrar_alegacao`, nunca levanta e devolve a contagem.
"""

import json
import logging
from dataclasses import dataclass, field
from importlib import metadata
from urllib.parse import urlsplit

from ..alegacoes import LITERAL_MAXIMO, EvidenciaEntrada, registrar_alegacao, truncar_trecho
from ..classificacao_dominio import host_da_url
from ..referencias import cnpj_valido, normalizar_referencia, so_digitos

logger = logging.getLogger(__name__)

PRODUTOR = "extruct-jsonld"
LOCALIZADOR_TIPO = "extruct.json-ld"


def _versao_lib() -> str:
    try:
        return metadata.version("extruct")
    except metadata.PackageNotFoundError:
        return "desconhecida"


def versao() -> str:
    """Versão do mapeamento (1) + da biblioteca `extruct`."""
    return f"1/extruct-{_versao_lib()}"


_ORGANIZACAO = {"Organization", "Corporation", "LocalBusiness", "NGO", "GovernmentOrganization",
                "NewsMediaOrganization", "EducationalOrganization"}
_PESSOA = {"Person"}
_ARTIGO = {"Article", "NewsArticle", "BlogPosting", "Report", "ScholarlyArticle"}

#: (propriedades literais, propriedades que são URL, relações) por categoria.
_PROPRIEDADES = {
    "organizacao": (("name", "legalName", "taxID", "vatID", "telephone", "email", "foundingDate", "address"),
                    ("url", "sameAs"), ()),
    "pessoa": (("name", "jobTitle"), ("sameAs",), ("worksFor",)),
    "artigo": (("headline", "datePublished", "dateModified"), (), ("author", "publisher")),
}


@dataclass(frozen=True)
class Candidata:
    sujeito_ref: str
    predicado: str
    autor_ref: str
    localizador: dict
    trecho: str
    objeto_ref: str | None = None
    objeto_literal: str | None = None


@dataclass
class Contagem:
    criadas: int = 0
    existentes: int = 0
    rejeitadas: int = 0
    ignoradas: int = 0  # valor vazio, estruturado demais ou longo demais
    por_predicado: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.criadas + self.existentes

    def como_dict(self) -> dict:
        return {"criadas": self.criadas, "existentes": self.existentes,
                "rejeitadas": self.rejeitadas, "ignoradas": self.ignoradas}


def _tipos(item: dict) -> set[str]:
    bruto = item.get("@type")
    lista = bruto if isinstance(bruto, list) else [bruto]
    return {str(t).rsplit("/", 1)[-1].rsplit("#", 1)[-1].split(":")[-1] for t in lista if t}


def _categoria(tipos: set[str]) -> str | None:
    if tipos & _ARTIGO:
        return "artigo"
    if tipos & _ORGANIZACAO:
        return "organizacao"
    if tipos & _PESSOA:
        return "pessoa"
    return None


def _itens(raw_jsonld, prefixo: str = "json-ld"):
    """Percorre o JSON-LD, abrindo `@graph`, e devolve `(item, caminho)`."""
    if isinstance(raw_jsonld, dict):
        raw_jsonld = [raw_jsonld]
    for n, item in enumerate(raw_jsonld or []):
        yield from _expandir(item, f"{prefixo}[{n}]")


def _expandir(item, caminho: str):
    if not isinstance(item, dict):
        return
    if isinstance(item.get("@graph"), list):
        for m, sub in enumerate(item["@graph"]):
            yield from _expandir(sub, f"{caminho}/@graph[{m}]")
    if _tipos(item):
        yield item, caminho


def _http(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    partes = urlsplit(valor.strip())
    return valor.strip() if partes.scheme in ("http", "https") and partes.netloc else None


def _como_lista(valor):
    return valor if isinstance(valor, list) else [valor]


def _endereco(valor) -> str | None:
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        partes = [valor.get(k) for k in ("streetAddress", "addressLocality", "addressRegion",
                                          "postalCode", "addressCountry")]
        return ", ".join(str(p) for p in partes if isinstance(p, str) and p.strip()) or None
    return None


def _literal(valor, propriedade: str) -> str | None:
    """Texto do valor, ou `None` se vazio ou estruturado demais para virar literal."""
    if propriedade == "address":
        valor = _endereco(valor)
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return valor or None


def _sujeito(item: dict, categoria: str, caminho: str, blob_hash: str) -> str:
    candidatos = []
    if categoria == "organizacao":
        for chave in ("taxID", "vatID"):
            digitos = so_digitos(str(item.get(chave) or ""))
            if cnpj_valido(digitos):
                candidatos.append(f"cnpj:{digitos}")
    for chave in (("url", "@id") if categoria in ("artigo", "organizacao") else ("@id",)):
        url = _http(item.get(chave))
        if url:
            candidatos.append(f"url:{url}")
    for candidato in candidatos:
        try:
            return normalizar_referencia(candidato)
        except ValueError:
            continue
    return f"mencao:{blob_hash}#{caminho}"


def _evidencia(propriedade: str, valor_bruto, caminho: str, blob_hash: str) -> tuple[dict, str]:
    localizador = {"item": caminho, "propriedade": propriedade}
    trecho = truncar_trecho(json.dumps({propriedade: valor_bruto}, ensure_ascii=False, default=str))
    return localizador, trecho


def gerar(dados_extruct: dict | None, *, url_captura: str, blob_hash: str) -> tuple[list[Candidata], int]:
    """Alegações candidatas do JSON-LD e quantos valores foram ignorados. Pura."""
    host = host_da_url(url_captura)
    itens = ((dados_extruct or {}).get("raw") or {}).get("json-ld")
    if not host or not itens:
        return [], 0
    try:
        autor = normalizar_referencia(f"dominio:{host.removeprefix('www.')}")
    except ValueError:
        return [], 0

    candidatas: list[Candidata] = []
    ignoradas = 0
    for item, caminho in _itens(itens):
        categoria = _categoria(_tipos(item))
        if categoria is None:
            continue
        literais, urls, relacoes = _PROPRIEDADES[categoria]
        sujeito = _sujeito(item, categoria, caminho, blob_hash)

        def somar(propriedade, bruto, *, ref=None, literal=None):
            nonlocal ignoradas
            if ref is None and (literal is None or len(literal) > LITERAL_MAXIMO):
                ignoradas += 1
                return
            localizador, trecho = _evidencia(propriedade, bruto, caminho, blob_hash)
            candidatas.append(Candidata(
                sujeito_ref=sujeito, predicado=f"schema:{propriedade}", autor_ref=autor,
                localizador=localizador, trecho=trecho, objeto_ref=ref, objeto_literal=literal))

        for propriedade in literais:
            if propriedade in item:
                for bruto in _como_lista(item[propriedade]):
                    somar(propriedade, bruto, literal=_literal(bruto, propriedade))
        for propriedade in urls:
            if propriedade in item:
                for bruto in _como_lista(item[propriedade]):
                    url = _http(bruto)
                    somar(propriedade, bruto, ref=f"url:{url}" if url else None)
        for propriedade in relacoes:
            if propriedade in item:
                for bruto in _como_lista(item[propriedade]):
                    dict_ = bruto if isinstance(bruto, dict) else {}
                    url = _http(dict_.get("@id")) or _http(dict_.get("url")) or _http(bruto)
                    nome = _literal(dict_.get("name") if dict_ else bruto, "name")
                    if url:
                        somar(propriedade, bruto, ref=f"url:{url}")
                    else:
                        somar(propriedade, bruto, literal=nome)
    return candidatas, ignoradas


def registrar(artefato, dados_extruct: dict | None, blob_hash: str) -> Contagem:
    """Grava as alegações do JSON-LD do artefato. Nunca levanta; devolve a contagem."""
    contagem = Contagem()
    try:
        candidatas, contagem.ignoradas = gerar(
            dados_extruct, url_captura=(artefato.content or {}).get("url", ""), blob_hash=blob_hash)
    except Exception:
        logger.exception("[%s] falha ao gerar alegações do extruct", artefato.id)
        return contagem

    for c in candidatas:
        try:
            _, criada = registrar_alegacao(
                artefato=artefato, sujeito_ref=c.sujeito_ref, predicado=c.predicado,
                objeto_ref=c.objeto_ref, objeto_literal=c.objeto_literal, autor_ref=c.autor_ref,
                produtor=PRODUTOR, produtor_versao=versao(),
                evidencias=[EvidenciaEntrada(blob_hash, LOCALIZADOR_TIPO, c.localizador, c.trecho)],
            )
        except ValueError:
            contagem.rejeitadas += 1
            continue
        except Exception:
            logger.exception("[%s] falha ao gravar alegação %s", artefato.id, c.predicado)
            contagem.rejeitadas += 1
            continue
        if criada:
            contagem.criadas += 1
        else:
            contagem.existentes += 1
        contagem.por_predicado[c.predicado] = contagem.por_predicado.get(c.predicado, 0) + 1
    return contagem

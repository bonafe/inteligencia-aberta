"""Laudo de exames → alegações `saude:` → projeção `ResultadoExame` (ADR 022, etapa 5).

O mesmo caminho dos Gastos, com o mínimo de cada passo:

1. **Parsing puro** (`extrair_resultados`): sem banco e sem LLM. Um resultado é qualquer objeto de
   uma lista com o nome do exame (`exame`, `analito`, `parametro`...) e o resultado (`resultado`,
   `valor`...); unidade, referência e material quando houver. A data da coleta e o laboratório vêm
   do documento.
2. **Registro**: a leitura escolhida vira alegações por `registrar_alegacao`: extração válida do
   parser de página (`registrar_extracao`, também chamada pelo núcleo quando o schema tem o
   consumidor `saude.exames`) ou estruturação por LLM (`registrar`). `saude:Exame` tem piso
   `confidencial` e `eleva_fonte`: o laudo sobe a confidencial na primeira alegação, auditado.
3. **Projeção** (`projetar_artefato`): das alegações ativas da leitura escolhida para os resultados.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from django.db import transaction

from apps.artifacts.alegacoes import LITERAL_MAXIMO, EvidenciaEntrada, registrar_alegacao, truncar_trecho
from apps.artifacts.extractors.valores import parse_valor
from apps.artifacts.models import Claim, EstruturacaoLLM, ExtracaoPagina
from apps.extensoes.contrato import mais_alto
from config.conteudo_hash import formato_valido as hash_valido

from .extensao import EXTENSAO
from .models import LeituraExames, ResultadoExame

PRODUTOR_LLM, PRODUTOR_LLM_VERSAO = "estruturacao-llm", "saude-1"
PRODUTOR_PARSER = "parser-pagina"
PRODUTORES = (PRODUTOR_LLM, PRODUTOR_PARSER)

_ANALITO = ("exame", "analito", "parametro", "determinacao", "teste", "nome", "item")
_RESULTADO = ("resultado", "valor", "result", "valorresultado")
_UNIDADE = ("unidade", "unid", "un")
_MATERIAL = ("material", "amostra")
_SUJEITO = re.compile(r"/exame/(\d+)$")
_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")


def _chave(nome: str) -> str:
    sem = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem.lower())


def chave_analito(analito: str) -> str:
    """Para juntar o histórico do mesmo exame entre laudos: sem acento, maiúsculo, espaço simples."""
    sem = unicodedata.normalize("NFKD", analito).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^\w\s]", " ", sem.upper()).split())[:300]


def parse_data(texto: str) -> date | None:
    if not (m := _DATA.search(texto or "")):
        return None
    dia, mes, ano = (int(g) for g in m.groups())
    try:
        return date(ano + 2000 if ano < 100 else ano, mes, dia)
    except ValueError:
        return None


@dataclass
class Resultado:
    indice: int
    campos: dict[str, str]          # predicado → texto
    original: dict


def _primeiro(textos: dict, nomes) -> str:
    return next((textos[n] for n in nomes if n in textos), "")


def _campos(item: dict) -> dict[str, str] | None:
    textos = {_chave(k): str(v).strip() for k, v in item.items()
              if isinstance(v, (str, int, float)) and str(v).strip()}
    analito, resultado = _primeiro(textos, _ANALITO), _primeiro(textos, _RESULTADO)
    if not analito or not resultado:
        return None
    referencia = next((v for k, v in textos.items() if k.startswith(("referencia", "valoresdereferencia",
                                                                      "valorreferencia", "intervalo"))), "")
    campos = {"saude:analito": analito, "saude:resultado": resultado, "saude:unidade": _primeiro(textos, _UNIDADE),
              "saude:referencia": referencia, "saude:material": _primeiro(textos, _MATERIAL),
              "saude:dataColeta": next((v for k, v in textos.items() if "coleta" in k or k == "data"), "")}
    return {p: v[:LITERAL_MAXIMO] for p, v in campos.items() if v}


def extrair_resultados(dados) -> tuple[list[Resultado], dict[str, str]]:
    """Os resultados de um laudo, em ordem, e o que o documento diz dele (data da coleta, laboratório)."""
    resultados: list[Resultado] = []
    documento: dict[str, str] = {}

    def visitar(no):
        if isinstance(no, dict):
            for k, v in no.items():
                ck = _chave(k)
                if isinstance(v, (str, int)) and str(v).strip():
                    if "coleta" in ck and "saude:dataColeta" not in documento:
                        documento["saude:dataColeta"] = str(v).strip()[:LITERAL_MAXIMO]
                    elif "laboratorio" in ck and "saude:laboratorio" not in documento:
                        documento["saude:laboratorio"] = str(v).strip()[:LITERAL_MAXIMO]
                visitar(v)
        elif isinstance(no, list):
            for item in no:
                if isinstance(item, dict) and (campos := _campos(item)):
                    resultados.append(Resultado(len(resultados), campos, item))
                else:
                    visitar(item)

    visitar(dados)
    return resultados, documento


# ── registro ────────────────────────────────────────────────────────────────

@dataclass
class Registro:
    resultados: int = 0
    alegacoes_criadas: int = 0
    substituiu: bool = False


def _registrar(document_text, dados, *, base, produtor, versao, modelo, localizador_tipo, localizador,
               user_id=None) -> Registro:
    from apps.artifacts.tasks import correlacao_do_artefato
    from apps.events.emit import emit

    artefato = document_text.document
    if not hash_valido(artefato.blob_hash or ""):
        raise ValueError("a captura não tem hash de conteúdo; sem ele não há evidência")
    resultados, documento = extrair_resultados(dados)
    registro = Registro(resultados=len(resultados))
    comum = dict(correlation_id=correlacao_do_artefato(artefato.id), subject_type="artifact",
                 subject_id=artefato.id, tenant_id=artefato.tenant_id, user_id=user_id, source="portal")
    if not resultados:
        emit("saude.exames", "vazio", message="a leitura não tem lista de resultados de exame", **comum)
        return registro
    autor = f"urn:uuid:{artefato.id}"

    def alegar(sujeito, predicado, valor, loc, trecho):
        _, criada = registrar_alegacao(
            artefato=artefato, sujeito_ref=sujeito, predicado=predicado, objeto_literal=valor, autor_ref=autor,
            produtor=produtor, produtor_versao=versao, modelo=modelo,
            evidencias=[EvidenciaEntrada(blob_hash=artefato.blob_hash, localizador_tipo=localizador_tipo,
                                         localizador=loc, trecho=trecho)],
        )
        registro.alegacoes_criadas += int(criada)

    with transaction.atomic():
        for predicado, valor in documento.items():
            alegar(base, predicado, valor, dict(localizador), truncar_trecho(valor))
        for r in resultados:
            trecho = truncar_trecho(json.dumps(r.original, ensure_ascii=False))
            for predicado, valor in r.campos.items():
                alegar(f"{base}/exame/{r.indice}", predicado, valor, {**localizador, "indice": r.indice}, trecho)
        anterior = LeituraExames.objects.filter(artefato=artefato).values_list("base", flat=True).first()
        registro.substituiu = anterior is not None and anterior != base
        LeituraExames.objects.update_or_create(artefato=artefato,
                                               defaults={"tenant_id": artefato.tenant_id, "base": base})
        projetar_artefato(artefato)
    emit("saude.exames", "ok", message=f"{registro.resultados} resultados de exame registrados",
         payload={"resultados": registro.resultados, "alegacoes_criadas": registro.alegacoes_criadas}, **comum)
    return registro


def registrar_extracao(extracao: ExtracaoPagina, *, user_id=None) -> Registro:
    """A extração **válida** do parser aprovado vira alegações e resultados, sem LLM."""
    if extracao.status != ExtracaoPagina.Status.VALIDO or not extracao.dados:
        raise ValueError("só uma extração que validou pode virar resultados de exame")
    doc, schema = extracao.document_text, extracao.schema
    return _registrar(doc, extracao.dados, base=f"mencao:{doc.document.blob_hash}#parser/{schema.id}",
                      produtor=PRODUTOR_PARSER, versao=f"v{schema.versao}", modelo="", localizador_tipo=PRODUTOR_PARSER,
                      localizador={"schema": str(schema.id), "versao": schema.versao, "extracao": str(extracao.id)},
                      user_id=user_id)


def registrar(estruturacao: EstruturacaoLLM, *, user_id=None) -> Registro:
    """A estruturação por LLM escolhida vira alegações e resultados."""
    if estruturacao.status != EstruturacaoLLM.Status.CONCLUIDO or not estruturacao.structured_data:
        raise ValueError("só uma estruturação concluída pode virar resultados de exame")
    doc = estruturacao.document_text
    return _registrar(doc, estruturacao.structured_data,
                      base=f"mencao:{doc.document.blob_hash}#estruturacao/{estruturacao.id}",
                      produtor=PRODUTOR_LLM, versao=PRODUTOR_LLM_VERSAO,
                      modelo=f"{estruturacao.provider}:{estruturacao.model_name}", localizador_tipo=PRODUTOR_LLM,
                      localizador={"estruturacao": str(estruturacao.id)}, user_id=user_id)


# ── projeção ────────────────────────────────────────────────────────────────

def projetar_artefato(artefato) -> int:
    from django.db.models import Q

    ResultadoExame.objects.filter(artefato=artefato).delete()
    leitura = LeituraExames.objects.filter(artefato=artefato).first()
    if leitura is None:
        return 0
    documento: dict[str, str] = {}
    sujeitos: dict[str, dict[str, str]] = {}
    da_leitura = Q(sujeito_ref=leitura.base) | Q(sujeito_ref__startswith=leitura.base + "/exame/")
    for c in Claim.objects.filter(da_leitura, artefato=artefato, produtor__in=PRODUTORES, predicado__startswith="saude:",
                                  estado=Claim.Estado.ATIVA):
        alvo = documento if c.sujeito_ref == leitura.base else sujeitos.setdefault(c.sujeito_ref, {})
        alvo[c.predicado] = c.objeto_literal
    nivel = mais_alto(artefato.classification_level, EXTENSAO.tipo("saude:Exame").piso)
    novos = []
    for sujeito, campos in sujeitos.items():
        if not (m := _SUJEITO.search(sujeito)) or "saude:analito" not in campos or "saude:resultado" not in campos:
            continue
        data_texto = campos.get("saude:dataColeta") or documento.get("saude:dataColeta", "")
        novos.append(ResultadoExame(
            tenant_id=artefato.tenant_id, artefato=artefato, sujeito_ref=sujeito, indice=int(m.group(1)),
            analito=campos["saude:analito"][:300], analito_chave=chave_analito(campos["saude:analito"]),
            resultado=campos["saude:resultado"][:200], valor=_numero(campos["saude:resultado"]),
            unidade=campos.get("saude:unidade", "")[:50], referencia=campos.get("saude:referencia", "")[:300],
            material=campos.get("saude:material", "")[:100], data=parse_data(data_texto), data_texto=data_texto[:100],
            laboratorio=documento.get("saude:laboratorio", "")[:200], classification_level=nivel,
        ))
    ResultadoExame.objects.bulk_create(novos)
    return len(novos)


def _numero(texto: str):
    """"5,4" → 5.4; "< 0,5", "Não reagente" ou "1/40" → None (não é um número para o gráfico)."""
    if not re.fullmatch(r"\s*-?[\d.,]+\s*", texto or ""):
        return None
    return parse_valor(texto)

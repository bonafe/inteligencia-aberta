"""Extração determinística via o parser verificado do `dom2parser`.

Desde a versão 8af008b, `dom2parser.compress(html)` devolve, além da
representação compacta (`.text`), um `ParserSpec` (`.parser`): seletor de
registro + locator por campo, já medido contra o documento original
(`verified.precision/recall`). Desde 519fd31 um registro pode abranger vários
irmãos consecutivos (`RecordEntry.span`, ex.: `dt` + `dd`) e cada campo diz a
qual irmão seu locator é relativo (`FieldEntry.sibling`); registros sem campo
descoberto rendem `{"_text": ...}`. Ambos os campos têm default, então configs
gravados por versões anteriores continuam carregando. O parser é **dado**, não código — é executado
por lxml através de `dom2parser.parser.executor.execute`, nunca por `exec()`.

Este módulo transforma um `ParserSpec` — fresco, sintetizado nesta mesma
captura por `dom2parser.compress()` em `tasks.py` — em `structured_data`.
Não há reuso de schema entre capturas: o dado de uma página pode mudar a
cada visita, e um schema salvo só saberia extrair os campos que tinham
valor na captura que o gerou.

Mantido sem dependência de Django para poder ser testado isoladamente.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION_PREFIX = "dom2parser:"

# Teto de linhas persistidas por registro em `structured_data` (JSONField).
# Páginas normais têm dezenas/centenas de linhas; o teto só protege contra
# listas virtualizadas gigantes.
MAX_ROWS_PER_RECORD = 2000


def _exact(record) -> bool:
    verified = record.verified or {}
    return verified.get("precision", 0) >= 1.0 and verified.get("recall", 0) >= 1.0


def usable_records(spec) -> list:
    """Registros do spec que valem a pena executar: verificados exatos.

    Registros sem campo descoberto (link de tag, item de lista simples) ainda
    são registros — o executor devolve o próprio texto em `_text`.
    """
    if spec is None:
        return []
    return [r for r in spec.records if _exact(r)]


def _rows(extraction) -> list[dict]:
    rows = []
    for record in extraction.kept:
        # O executor só marca `all_empty` quando o spec pede; linhas vazias
        # (espaçadores, separadores) não são dado — descarta aqui.
        if not any(v for v in record.values.values()):
            continue
        rows.append(record.values)
        if len(rows) >= MAX_ROWS_PER_RECORD:
            logger.warning("dom_parser: registro truncado em %d linhas", MAX_ROWS_PER_RECORD)
            break
    return rows


def diagnosticar(spec) -> dict:
    """Descreve por que um spec produz (ou não) registros aproveitáveis.

    Existia um ponto cego caro aqui: quando `usable_records` voltava vazio, a
    função simplesmente devolvia None, sem log e sem nenhum vestígio no banco —
    e "o dom2parser não gerou dados" ficava indistinguível de "o dom2parser
    quebrou". Este diagnóstico é devolvido a quem chama (tasks.py), que o grava
    como payload de evento. O módulo continua sem depender de Django.
    """
    if spec is None:
        return {"spec": False, "registros_no_spec": 0, "registros_utilizaveis": 0, "falhas": []}

    utilizaveis = usable_records(spec)
    nao_exatos = [
        {
            "nome": r.name,
            "seletor": r.selector,
            "precision": (r.verified or {}).get("precision"),
            "recall": (r.verified or {}).get("recall"),
        }
        for r in spec.records
        if not _exact(r)
    ]
    return {
        "spec": True,
        "schema_version": spec.schema_version,
        "registros_no_spec": len(spec.records),
        "registros_utilizaveis": len(utilizaveis),
        "registros_reprovados": nao_exatos[:20],
        "falhas": [str(f)[:200] for f in list(spec.failures)[:20]],
        "total_falhas": len(spec.failures),
    }


def extract_with_spec(spec, html: str, diagnostico: dict | None = None) -> dict | None:
    """Executa o parser contra o HTML original.

    Retorna `{"structured_data": {"registros": {nome: [linhas]}}, "extractor_version": ...}`
    ou None se nenhum registro produziu linhas.

    Quando um dict é passado em `diagnostico`, ele é preenchido com o motivo
    exato de um resultado vazio, para que o chamador possa registrá-lo.
    """
    diag = diagnostico if diagnostico is not None else {}
    diag.update(diagnosticar(spec))

    records = usable_records(spec)
    if not records:
        diag["motivo"] = "nenhum registro do spec atingiu precision/recall 1.0"
        logger.info(
            "dom_parser: spec sem registro utilizável — %d registros, %d falhas",
            diag.get("registros_no_spec", 0), diag.get("total_falhas", 0),
        )
        return None

    from dom2parser.html_io import parse_html
    from dom2parser.parser.executor import Extraction, extract_records
    from dom2parser.parser.validate import validate

    root = parse_html(html)
    registros: dict[str, list[dict]] = {}
    sem_linhas: list[str] = []
    com_erro: list[str] = []
    for record in records:
        try:
            extraction = Extraction(records=extract_records(record, root))
        except Exception:
            com_erro.append(record.name)
            logger.warning("dom_parser: seletor '%s' falhou — registro ignorado", record.selector, exc_info=True)
            continue
        rows = _rows(extraction)
        if not rows:
            sem_linhas.append(record.name)
            logger.info("dom_parser: registro '%s' (%s) sem linhas", record.name, record.selector)
            continue
        report = validate(record, extraction)
        logger.info(
            "dom_parser: registro '%s' (%s) — %d linhas, %d puladas, campos=%s",
            record.name, record.selector, len(rows), report.skipped,
            {m.name: m.present for m in report.fields},
        )
        registros[record.name] = rows

    diag["registros_sem_linhas"] = sem_linhas
    diag["registros_com_erro"] = com_erro
    diag["linhas_por_registro"] = {nome: len(linhas) for nome, linhas in registros.items()}

    if not registros:
        diag["motivo"] = (
            "os registros executaram mas nenhum produziu linha com conteúdo"
            if sem_linhas else "todos os seletores levantaram erro na execução"
        )
        return None

    return {
        "structured_data": {"registros": registros},
        "extractor_version": f"{EXTRACTOR_VERSION_PREFIX}{spec.schema_version}",
    }


__all__ = [
    "EXTRACTOR_VERSION_PREFIX",
    "diagnosticar",
    "extract_with_spec",
    "usable_records",
]

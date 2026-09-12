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

Este módulo tem duas responsabilidades:

1. Transformar um `ParserSpec` (fresco, desta captura) ou um dict serializado
   (vindo de `URLPatternCache.extractor_config`) em `structured_data`.
2. Serializar o `ParserSpec` no formato de `extractor_config` (versão 2.0),
   para que capturas seguintes do mesmo padrão de URL reaproveitem o parser
   sem recomprimir nem chamar LLM.

Mantido sem dependência de Django para poder ser testado isoladamente.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONFIG_VERSION = "2.0"
GENERATED_BY = "dom2parser"
EXTRACTOR_VERSION_PREFIX = "dom2parser:"

# Teto de linhas persistidas por registro em `structured_data` (JSONField).
# Páginas normais têm dezenas/centenas de linhas; o teto só protege contra
# listas virtualizadas gigantes.
MAX_ROWS_PER_RECORD = 2000


def is_parser_config(config: dict | None) -> bool:
    """True se `extractor_config` foi gerado pelo dom2parser (formato 2.0)."""
    return bool(config) and config.get("generated_by") == GENERATED_BY and "parser" in config


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


def config_from_spec(spec) -> dict | None:
    """Serializa o parser para `URLPatternCache.extractor_config`.

    Devolve None quando o spec não tem nenhum registro verificado — nesse caso
    não há nada a reaproveitar e o cache deve ficar vazio para que a próxima
    captura tente de novo (ou caia na Estratégia C via LLM).
    """
    records = usable_records(spec)
    if not records:
        return None
    from dom2parser.parser.spec import ParserSpec

    trimmed = ParserSpec(
        schema_version=spec.schema_version,
        records=records,
        failures=list(spec.failures),
    )
    return {
        "version": CONFIG_VERSION,
        "generated_by": GENERATED_BY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser": trimmed.to_dict(),
    }


def _spec_from_config(config: dict):
    from dom2parser.parser.spec import ParserSpec

    return ParserSpec.from_dict(config["parser"])


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
    ou None se nenhum registro produziu linhas — a ausência é o sinal usado por
    `_update_schema_health()` (tasks.py) para detectar schema obsoleto.

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


def extract_with_config(config: dict, html: str, diagnostico: dict | None = None) -> dict | None:
    """Executa um parser previamente gravado em `extractor_config` (formato 2.0)."""
    try:
        spec = _spec_from_config(config)
    except Exception:
        if diagnostico is not None:
            diagnostico["motivo"] = "extractor_config gravado no cache não desserializa"
        logger.exception("dom_parser: extractor_config inválido — ignorado")
        return None
    return extract_with_spec(spec, html, diagnostico)


__all__ = [
    "CONFIG_VERSION",
    "EXTRACTOR_VERSION_PREFIX",
    "config_from_spec",
    "diagnosticar",
    "extract_with_config",
    "extract_with_spec",
    "is_parser_config",
    "usable_records",
]

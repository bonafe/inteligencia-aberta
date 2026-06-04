import logging
import re

logger = logging.getLogger(__name__)

_BRL_CLEAN_RE = re.compile(r"[^\d,.\-]")


def _parse_brl(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    negative = s.startswith("-") or s.endswith("D")
    s = s.lstrip("-").rstrip("D")
    s = _BRL_CLEAN_RE.sub("", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _parse_date_br(s: str) -> str:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s.strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return s


def _apply_transform(text: str, transform: str, element=None):
    if transform == "text":
        return text or None
    if transform == "brl_float":
        return _parse_brl(text)
    if transform == "date_br":
        return _parse_date_br(text) if text else None
    if transform.startswith("attr:") and element is not None:
        return element.get(transform[5:], None) or None
    return text or None


def _extract_fields(soup, fields_config: dict) -> dict:
    result = {}
    for field_name, cfg in fields_config.items():
        selector = cfg.get("selector", "")
        transform = cfg.get("transform", "text")
        if not selector:
            continue
        try:
            el = soup.select_one(selector)
            if el is None:
                logger.debug("schema_extractor: seletor '%s' → nenhum elemento", selector)
                continue
            text = el.get_text(strip=True)
            val = _apply_transform(text, transform, el)
            if val is not None and val != "":
                result[field_name] = val
        except Exception:
            logger.warning("schema_extractor: seletor inválido '%s' — ignorado", selector)
    return result


def _extract_tables(soup, tables_config: list) -> list[list[dict]]:
    result = []
    for table_cfg in tables_config:
        selector = table_cfg.get("selector", "table")
        columns = table_cfg.get("columns", {})
        if not columns:
            continue
        try:
            tables = soup.select(selector)
        except Exception:
            logger.warning("schema_extractor: seletor de tabela inválido '%s' — ignorado", selector)
            continue
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            data_rows = []
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not any(cells):
                    continue
                record = {}
                for col_name, col_cfg in columns.items():
                    idx = col_cfg.get("index", 0)
                    transform = col_cfg.get("transform", "text")
                    if idx < len(cells):
                        val = _apply_transform(cells[idx], transform)
                        if val is not None:
                            record[col_name] = val
                if record:
                    data_rows.append(record)
            if data_rows:
                result.append(data_rows)
    return result


def schema_driven_extract(html: str, url: str, title: str, config: dict) -> dict:
    """Extract structured data using a JSON schema of CSS selectors.

    No exec() or eval() — the schema is interpreted data, not code.
    Falls back to extract_fallback if the result is empty.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        extracted_fields = _extract_fields(soup, config.get("fields", {}))
        extracted_tables = _extract_tables(soup, config.get("tables", []))

        text_parts = [
            f"{k.replace('_', ' ').title()}: {v}"
            for k, v in extracted_fields.items()
        ]
        for table_rows in extracted_tables:
            for row in table_rows:
                text_parts.append(" | ".join(f"{k}: {v}" for k, v in row.items()))

        text = "\n".join(text_parts)

        if not text:
            logger.info("schema_driven_extract: nenhum dado extraído — fallback")
            from .strategies import extract_fallback
            return extract_fallback(html, url, title)

        structured_data: dict = {}
        if extracted_fields:
            structured_data.update(extracted_fields)
        if extracted_tables:
            structured_data["tabelas"] = extracted_tables

        logger.info(
            "schema_driven_extract: %d campos, %d tabelas, %d chars",
            len(extracted_fields), len(extracted_tables), len(text),
        )

        return {
            "text": text,
            "structured_data": structured_data or None,
            "extractor_version": f"schema_driven:{config.get('version', '1.0')}",
        }

    except Exception:
        logger.exception("schema_driven_extract falhou — fallback")
        from .strategies import extract_fallback
        return extract_fallback(html, url, title)

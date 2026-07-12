import logging
import re

logger = logging.getLogger(__name__)

_PROCESS_CNJ_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_DATE_BR_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")

EXTRACTOR_VERSION = "1.0"

_PARTY_LABELS = {
    "polo_ativo": [
        "polo ativo", "autor", "autora", "requerente", "exequente",
        "impetrante", "apelante", "reclamante",
    ],
    "polo_passivo": [
        "polo passivo", "réu", "ré", "requerido", "requerida",
        "executado", "executada", "impetrado", "apelado", "reclamado",
    ],
}

_FIN_DATE_HEADERS = ["data", "dt", "date"]
_FIN_DESC_HEADERS = [
    "histórico", "historico", "descrição", "descricao", "discriminação",
    "discriminacao", "lançamento", "lancamento", "memo", "desc", "estabelecimento",
]
_FIN_VALUE_HEADERS = ["valor", "quantia", "débito", "debito", "crédito", "credito", "r$", "vlr"]
_FIN_BALANCE_HEADERS = ["saldo"]
_MOV_DATE_HEADERS = ["data", "dt"]
_MOV_DESC_HEADERS = ["movimento", "descrição", "descricao", "andamento", "histórico", "historico"]


def _col_index(headers: list[str], patterns: list[str]) -> int | None:
    for i, h in enumerate(headers):
        h_low = h.lower().strip()
        if any(p in h_low for p in patterns):
            return i
    return None


def _parse_brl(s: str) -> float | None:
    s = s.strip()
    negative = s.startswith("-") or s.endswith("D")
    s = s.lstrip("-").rstrip("D").replace("R$", "").strip()
    if not s:
        return None
    # Thousand separator is "." and decimal is "," in pt-BR
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


# ── Texto de busca — sempre trafilatura ──────────────────────────────────────
#
# Fonte única do campo "text" do DocumentText, independente de page_type ou de
# qual caminho de extração estruturada rodou (LLM, schema-driven, extrator
# determinístico). trafilatura é uma lib madura e testada especificamente para
# extração de prosa; heurísticas por tipo de página e respostas de LLM são
# frágeis demais para essa responsabilidade — ficam restritas a structured_data.

def extract_narrative_text(html: str) -> str:
    import trafilatura
    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    if not text:
        text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)
    return text or ""


# ── Extratores — produzem apenas structured_data ─────────────────────────────

def extract_financial_table(html: str, url: str, title: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        transactions = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            raw_headers = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])]
            if not raw_headers:
                continue

            date_col = _col_index(raw_headers, _FIN_DATE_HEADERS)
            desc_col = _col_index(raw_headers, _FIN_DESC_HEADERS)
            val_col = _col_index(raw_headers, _FIN_VALUE_HEADERS)
            bal_col = _col_index(raw_headers, _FIN_BALANCE_HEADERS)

            # Positional fallback
            if date_col is None and desc_col is None and val_col is None and len(raw_headers) >= 3:
                date_col, desc_col = 0, 1
                if len(raw_headers) >= 4:
                    val_col = len(raw_headers) - 2
                    bal_col = len(raw_headers) - 1
                else:
                    val_col = len(raw_headers) - 1

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue

                def cell(idx):
                    return cells[idx] if idx is not None and idx < len(cells) else ""

                date_val = cell(date_col)
                desc_val = cell(desc_col)
                val_str = cell(val_col)
                bal_str = cell(bal_col)

                if not date_val or date_val.lower() in ("data", "dt", "date"):
                    continue

                value = _parse_brl(val_str)
                balance = _parse_brl(bal_str)

                tx = {"data": date_val, "descricao": desc_val, "valor": value}
                if balance is not None:
                    tx["saldo"] = balance
                transactions.append(tx)

        if not transactions:
            return extract_fallback(html, url, title)

        return {
            "structured_data": {
                "tipo": "tabular_financeiro",
                "transacoes": transactions,
                "total_transacoes": len(transactions),
            },
            "extractor_version": f"financial_table:{EXTRACTOR_VERSION}",
        }
    except Exception:
        logger.exception("extract_financial_table failed")
        return extract_fallback(html, url, title)


def extract_generic_table(html: str, url: str, title: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tables_data = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
            data_rows = [
                [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                for r in rows[1:]
            ]
            data_rows = [r for r in data_rows if any(r)]
            if headers or data_rows:
                tables_data.append({"cabecalho": headers, "linhas": data_rows})

        if not tables_data:
            return extract_fallback(html, url, title)

        return {
            "structured_data": {"tipo": "tabular_generico", "tabelas": tables_data},
            "extractor_version": f"generic_table:{EXTRACTOR_VERSION}",
        }
    except Exception:
        logger.exception("extract_generic_table failed")
        return extract_fallback(html, url, title)


def extract_judicial_process(html: str, url: str, title: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        all_text = soup.get_text("\n", strip=True)

        numero_cnj = next(iter(_PROCESS_CNJ_RE.findall(all_text)), None)

        def _field(label: str) -> str:
            m = re.search(rf"{re.escape(label)}[:\s]+([^\n]+)", all_text, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        classe = _field("classe processual") or _field("classe")
        assunto = _field("assunto")

        parties: dict[str, list[str]] = {"polo_ativo": [], "polo_passivo": []}
        for key, labels in _PARTY_LABELS.items():
            for label in labels:
                for m in re.finditer(rf"{re.escape(label)}[:\s]+([^\n]+)", all_text, re.IGNORECASE):
                    name = m.group(1).strip()
                    if name and name not in parties[key]:
                        parties[key].append(name)

        movimentacoes = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            raw_h = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            has_date = any(any(kw in h for kw in _MOV_DATE_HEADERS) for h in raw_h)
            has_desc = any(any(kw in h for kw in _MOV_DESC_HEADERS) for h in raw_h)

            if not (has_date and has_desc):
                # Heuristic: date pattern in first data cell
                has_date = bool(rows[1].find_all(["td", "th"])
                                and _DATE_BR_RE.search(rows[1].get_text()))
                if not has_date:
                    continue
                has_desc = len(raw_h) >= 2

            if not (has_date and has_desc):
                continue

            date_col = _col_index(raw_h, _MOV_DATE_HEADERS) or 0
            desc_col = _col_index(raw_h, _MOV_DESC_HEADERS) or (1 if len(raw_h) > 1 else 0)

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) <= max(date_col, desc_col):
                    continue
                d, desc = cells[date_col], cells[desc_col]
                if d and desc:
                    movimentacoes.append({"data": d, "descricao": desc})

        has_data = bool(
            numero_cnj or classe or assunto
            or parties["polo_ativo"] or parties["polo_passivo"] or movimentacoes
        )
        if not has_data:
            return extract_fallback(html, url, title)

        return {
            "structured_data": {
                "tipo": "processo_judicial",
                "numero_cnj": numero_cnj,
                "classe": classe,
                "assunto": assunto,
                "partes": parties,
                "movimentacoes": movimentacoes,
            },
            "extractor_version": f"judicial_process:{EXTRACTOR_VERSION}",
        }
    except Exception:
        logger.exception("extract_judicial_process failed")
        return extract_fallback(html, url, title)


def extract_company_profile(html: str, url: str, title: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        fields: dict[str, str] = {}

        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                key = dt.get_text(strip=True).rstrip(":").lower()
                val = dd.get_text(strip=True)
                if key and val:
                    fields[key] = val

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).rstrip(":").lower()
                    val = cells[1].get_text(strip=True)
                    if key and val and len(key) < 60 and key not in fields:
                        fields[key] = val

        all_text = soup.get_text(" ", strip=True)
        cnpjs = _CNPJ_RE.findall(all_text)
        cnpj = cnpjs[0] if cnpjs else None

        if not fields and not cnpj:
            return extract_fallback(html, url, title)

        return {
            "structured_data": {
                "tipo": "perfil_pessoa_juridica",
                "cnpj": cnpj,
                "campos": fields,
            },
            "extractor_version": f"company_profile:{EXTRACTOR_VERSION}",
        }
    except Exception:
        logger.exception("extract_company_profile failed")
        return extract_fallback(html, url, title)


def extract_legal_document(html: str, url: str, title: str) -> dict:
    # Prosa formal sem campos estruturáveis — só um marcador de classificação.
    return {
        "structured_data": {"tipo": "documento_juridico"},
        "extractor_version": f"legal_document:{EXTRACTOR_VERSION}",
    }


def extract_mixed(html: str, url: str, title: str) -> dict:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        tables_data = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
            data_rows = [
                [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                for r in rows[1:]
                if any(td.get_text(strip=True) for td in r.find_all(["td", "th"]))
            ]
            tables_data.append({"cabecalho": headers, "linhas": data_rows})

        if not tables_data:
            return extract_fallback(html, url, title)

        return {
            "structured_data": {"tipo": "misto", "tabelas": tables_data},
            "extractor_version": f"mixed:{EXTRACTOR_VERSION}",
        }
    except Exception:
        logger.exception("extract_mixed failed")
        return extract_fallback(html, url, title)


def extract_fallback(html: str, url: str, title: str) -> dict:
    # Sem extração estruturada — artigo/desconhecido, ou quando um extrator
    # especializado não encontrou nada. O texto de busca vem sempre de
    # extract_narrative_text(), chamado uma única vez em tasks.py.
    return {
        "structured_data": None,
        "extractor_version": f"fallback:{EXTRACTOR_VERSION}",
    }


# ── Router ──────────────────────────────────────────────────────────────────

_ROUTER = {
    "artigo": extract_fallback,
    "tabular_financeiro": extract_financial_table,
    "tabular_generico": extract_generic_table,
    "processo_judicial": extract_judicial_process,
    "perfil_pessoa_juridica": extract_company_profile,
    "documento_juridico": extract_legal_document,
    "misto": extract_mixed,
    "desconhecido": extract_fallback,
}


def route(page_type: str, html: str, url: str, title: str, cache_obj=None) -> dict:
    # Estratégia C: use schema-driven extractor when extractor_config is available
    if cache_obj is not None and cache_obj.extractor_config:
        from .schema_extractor import schema_driven_extract
        logger.info("route — page_type=%s → schema_driven_extract", page_type)
        return schema_driven_extract(html, url, title, cache_obj.extractor_config)

    extractor = _ROUTER.get(page_type, extract_fallback)
    logger.info("route — page_type=%s → %s", page_type, extractor.__name__)
    return extractor(html, url, title)

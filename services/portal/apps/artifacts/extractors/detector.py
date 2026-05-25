import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIGITS_RE = re.compile(r"^\d{3,}$")

_MONETARY_RE = re.compile(r"R\$\s*[\d.,]+", re.IGNORECASE)
_DATE_BR_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
_DATE_ISO2_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PROCESS_CNJ_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")


def normalize_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        segments = parsed.path.split("/")
        normalized = []
        for seg in segments:
            if not seg:
                normalized.append(seg)
            elif _UUID_RE.match(seg):
                normalized.append("*")
            elif _DATE_ISO_RE.match(seg):
                normalized.append("*")
            elif _DIGITS_RE.match(seg):
                normalized.append("*")
            else:
                normalized.append(seg)

        path_pattern = "/".join(normalized).rstrip("/") or "/"
        return domain, path_pattern
    except Exception:
        return "", ""


def analyze_html(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        tables = soup.find_all("table")
        table_row_count = sum(len(t.find_all("tr")) for t in tables)
        table_text = " ".join(t.get_text(" ", strip=True) for t in tables)
        table_char_count = len(table_text)

        # Remove tables to measure prose separately
        soup_prose = BeautifulSoup(html, "html.parser")
        for t in soup_prose.find_all("table"):
            t.decompose()
        text_char_count = len(soup_prose.get_text(" ", strip=True))

        total = table_char_count + text_char_count
        table_ratio = round(table_char_count / total, 3) if total > 0 else 0.0

        all_text = soup.get_text(" ", strip=True)
        monetary_count = len(_MONETARY_RE.findall(all_text))
        date_count = len(_DATE_BR_RE.findall(all_text)) + len(_DATE_ISO2_RE.findall(all_text))
        process_number_count = len(_PROCESS_CNJ_RE.findall(all_text))
        cnpj_count = len(_CNPJ_RE.findall(all_text))
        has_article = bool(soup.find("article"))
        has_main = bool(soup.find("main"))
        paragraph_count = len([p for p in soup.find_all("p") if len(p.get_text(strip=True)) > 50])

        return {
            "table_count": len(tables),
            "table_row_count": table_row_count,
            "table_char_count": table_char_count,
            "text_char_count": text_char_count,
            "table_ratio": table_ratio,
            "monetary_count": monetary_count,
            "date_count": date_count,
            "process_number_count": process_number_count,
            "cnpj_count": cnpj_count,
            "has_article": has_article,
            "has_main": has_main,
            "paragraph_count": paragraph_count,
        }
    except Exception:
        logger.exception("analyze_html failed")
        return {
            "table_count": 0, "table_row_count": 0, "table_char_count": 0,
            "text_char_count": 0, "table_ratio": 0.0, "monetary_count": 0,
            "date_count": 0, "process_number_count": 0, "cnpj_count": 0,
            "has_article": False, "has_main": False, "paragraph_count": 0,
        }


def classify(metrics: dict) -> tuple[str, float]:
    if metrics["process_number_count"] >= 1:
        return "processo_judicial", 0.95

    if (metrics["table_ratio"] > 0.55
            and metrics["monetary_count"] > 5
            and metrics["date_count"] > 5):
        return "tabular_financeiro", 0.90

    if (metrics["cnpj_count"] >= 1
            and metrics["table_ratio"] < 0.5
            and metrics["paragraph_count"] < 10):
        return "perfil_pessoa_juridica", 0.85

    if metrics["table_ratio"] > 0.50 and metrics["table_row_count"] > 8:
        return "tabular_generico", 0.80

    if ((metrics["has_article"] or metrics["has_main"])
            and metrics["paragraph_count"] > 5
            and metrics["table_ratio"] < 0.25):
        return "artigo", 0.85

    if metrics["paragraph_count"] > 20 and metrics["table_ratio"] < 0.15:
        return "documento_juridico", 0.75

    if metrics["table_ratio"] > 0.20 and metrics["paragraph_count"] > 10:
        return "misto", 0.65

    return "desconhecido", 0.50


def detect_page_type(
    html: str, url: str, tenant_id
) -> tuple[str, float, str, str | None]:
    """Returns (page_type, confidence, detection_source, cache_id)."""
    from django.db import models as django_models
    from apps.artifacts.models import URLPatternCache

    domain, path_pattern = normalize_url(url)
    cache_obj = None
    cache_id = None

    if domain:
        cache_obj = URLPatternCache.objects.filter(
            tenant_id=tenant_id, domain=domain, path_pattern=path_pattern
        ).first()

        if cache_obj:
            cache_id = str(cache_obj.id)
            if cache_obj.confidence >= 0.9 and not cache_obj.needs_review:
                URLPatternCache.objects.filter(id=cache_obj.id).update(
                    hit_count=django_models.F("hit_count") + 1
                )
                return cache_obj.page_type, cache_obj.confidence, "cache", cache_id

    metrics = analyze_html(html)
    page_type, confidence = classify(metrics)

    if domain and path_pattern:
        if cache_obj is not None:
            if cache_obj.page_type != page_type:
                URLPatternCache.objects.filter(id=cache_obj.id).update(
                    divergence_count=django_models.F("divergence_count") + 1
                )
                cache_obj.refresh_from_db(fields=["divergence_count"])
                if cache_obj.divergence_count >= 3:
                    URLPatternCache.objects.filter(id=cache_obj.id).update(needs_review=True)
                    logger.warning(
                        "URLPatternCache needs_review: %s%s (detected=%s, cached=%s)",
                        domain, path_pattern, page_type, cache_obj.page_type,
                    )
            else:
                URLPatternCache.objects.filter(id=cache_obj.id).update(
                    hit_count=django_models.F("hit_count") + 1,
                    confidence=confidence,
                )
        else:
            try:
                new_cache = URLPatternCache.objects.create(
                    tenant_id=tenant_id,
                    domain=domain,
                    path_pattern=path_pattern,
                    page_type=page_type,
                    confidence=confidence,
                )
                cache_id = str(new_cache.id)
            except Exception:
                logger.exception("URLPatternCache create failed")

    return page_type, confidence, "structural_analysis", cache_id

import logging
import re
from bs4 import BeautifulSoup, Comment, NavigableString

logger = logging.getLogger(__name__)

_NOISE_TAGS = frozenset({
    "script", "style", "link", "meta", "svg", "canvas",
    "noscript", "iframe", "picture", "source", "audio", "video",
})
_KEEP_ATTRS = frozenset({"class", "id", "name", "aria-label", "type"})
_KEEP_DATA_ATTRS = frozenset({"data-campo", "data-field", "data-label", "data-value", "data-key"})
_FRAMEWORK_ATTR_RE = re.compile(r"^(data-v-[a-f0-9]|data-reactid|ng-|wire:|phx-|jsf-)")
_EVENT_ATTR_RE = re.compile(r"^on[a-z]+$")

MAX_SKELETON_BYTES = 20 * 1024
TEXT_LIMIT = 80
MAX_TABLE_ROWS = 40
TABLE_ROWS_KEEP_HEAD = 20
TABLE_ROWS_KEEP_TAIL = 15


def _sample_table_rows(soup: BeautifulSoup) -> None:
    """Cap oversized tables to a representative row sample, in place.

    A single large table (e.g. a bank statement with hundreds of transactions)
    can otherwise consume the entire skeleton budget and get cut off mid-table
    by the byte truncation below — leaving the LLM with a header and a
    handful of rows instead of the full picture. Sampling drops whole `<tr>`
    elements (never a partial row) and keeps head + tail rows so the LLM sees
    both the table's shape and its most recent entries.
    """
    for table in soup.find_all("table"):
        rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if len(rows) <= MAX_TABLE_ROWS:
            continue

        head = rows[:TABLE_ROWS_KEEP_HEAD]
        tail = rows[-TABLE_ROWS_KEEP_TAIL:] if TABLE_ROWS_KEEP_TAIL else []
        keep_ids = {id(tr) for tr in head} | {id(tr) for tr in tail}
        omitted = len(rows) - len(keep_ids)

        if omitted > 0:
            placeholder = soup.new_tag("tr")
            td = soup.new_tag("td")
            td.string = f"… {omitted} linhas omitidas …"
            placeholder.append(td)
            head[-1].insert_after(placeholder)

        for tr in rows:
            if id(tr) not in keep_ids:
                tr.decompose()

        logger.info(
            "_sample_table_rows: tabela com %d linhas reduzida a %d (+%d omitidas)",
            len(rows), len(keep_ids), omitted,
        )


def _safe_byte_truncate(text: str, max_bytes: int) -> str:
    """Last-resort byte truncation that never splits a tag or attribute.

    Snaps back to the last complete '>' within the budget instead of cutting
    at an arbitrary byte offset — table-row sampling should make this path
    rare, but it stays as a hard safety net for the 20 KB cap.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    last_close = cut.rfind(b">")
    if last_close != -1:
        cut = cut[: last_close + 1]
    return cut.decode("utf-8", errors="ignore")


def compress_html_skeleton(html: str) -> str:
    """Compress HTML to a structural skeleton for LLM classification.

    Strips noise (scripts, styles, framework attrs, long text) while keeping
    tags, classes, IDs and short text samples. Large tables are row-sampled
    (head + tail, never a partial row) before the 20 KB cap is applied, so
    truncation never lands mid-table.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag_name in _NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        for tag in soup.find_all(True):
            new_attrs = {}
            for attr, val in list(tag.attrs.items()):
                if attr in _KEEP_ATTRS:
                    new_attrs[attr] = val
                elif attr in _KEEP_DATA_ATTRS:
                    new_attrs[attr] = val
                elif attr == "href" and isinstance(val, str) and val.startswith("http"):
                    try:
                        from urllib.parse import urlparse
                        new_attrs["href"] = urlparse(val).netloc
                    except Exception:
                        pass
                elif (
                    attr.startswith("data-")
                    and not _FRAMEWORK_ATTR_RE.match(attr)
                    and len(attr) < 30
                ):
                    new_attrs[attr] = val
                # drop event handlers and framework-specific attrs silently
            tag.attrs = new_attrs

        for node in soup.find_all(string=True):
            if not isinstance(node, NavigableString):
                continue
            stripped = node.strip()
            if not stripped:
                node.replace_with("")
            elif len(stripped) > TEXT_LIMIT:
                node.replace_with(stripped[:TEXT_LIMIT] + "…")
            elif stripped != str(node):
                node.replace_with(stripped)

        _sample_table_rows(soup)

        result = str(soup)
        encoded = result.encode("utf-8")

        original_kb = len(html.encode("utf-8")) / 1024
        skeleton_kb = len(encoded) / 1024
        reduction = (1 - skeleton_kb / max(original_kb, 0.1)) * 100

        if len(encoded) > MAX_SKELETON_BYTES:
            result = _safe_byte_truncate(result, MAX_SKELETON_BYTES)
            logger.info(
                "compress_html_skeleton: %.1f KB → 20 KB (truncado após amostragem de tabelas, %.0f%% redução)",
                original_kb, reduction,
            )
        else:
            logger.info(
                "compress_html_skeleton: %.1f KB → %.1f KB (%.0f%% redução)",
                original_kb, skeleton_kb, reduction,
            )

        return result

    except Exception:
        logger.exception("compress_html_skeleton falhou — retornando texto bruto truncado")
        return html.encode("utf-8")[:MAX_SKELETON_BYTES].decode("utf-8", errors="ignore")

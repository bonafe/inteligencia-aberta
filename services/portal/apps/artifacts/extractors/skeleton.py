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


def compress_html_skeleton(html: str) -> str:
    """Compress HTML to a structural skeleton for LLM classification.

    Strips noise (scripts, styles, framework attrs, long text) while keeping
    tags, classes, IDs and short text samples. Output is capped at 20 KB.
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

        result = str(soup)
        encoded = result.encode("utf-8")

        original_kb = len(html.encode("utf-8")) / 1024
        skeleton_kb = len(encoded) / 1024
        reduction = (1 - skeleton_kb / max(original_kb, 0.1)) * 100

        if len(encoded) > MAX_SKELETON_BYTES:
            result = encoded[:MAX_SKELETON_BYTES].decode("utf-8", errors="ignore")
            logger.info(
                "compress_html_skeleton: %.1f KB → 20 KB (truncado, %.0f%% redução)",
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

"""
backend/app/knowledge_sync/web_extractor.py

Semantic HTML extractor for the Website Knowledge Sync engine.

Pure-stdlib (html.parser) implementation that:
  - strips non-content elements (script/style/nav/footer/iframe/forms...),
  - skips boilerplate regions (cookie banners, consent, popups, sidebars)
    matched by id/class heuristics,
  - extracts <title>, meta description, rel=canonical, headings and main text,
  - preserves table structure: cells are emitted as pipe-separated fields
    with one row per line (column→row→value relationships survive for fee
    tables, schedules, contact directories, etc.),
  - resolves all <a href> links to absolute URLs,
  - normalizes whitespace for downstream chunking.

No external parsing dependencies — deterministic and testable.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

_TEXT_BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "tr", "table", "br", "blockquote", "figure",
    "figcaption", "details", "summary", "pre", "address", "label", "hr",
}

_STRIP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "math", "template",
    "form", "textarea", "select", "button", "canvas", "object", "embed",
    "video", "audio",
}

# Boilerplate regions whose id/class signals non-content.
_BOILERPLATE_RE = re.compile(
    r"cookie|consent|gdpr|privacy[_-]?banner|popup|modal|overlay|newsletter|"
    r"subscribe|sidebar|social|got[_-]?to[_-]?top|pagination|breadcrumb|"
    r"share[_-]?buttons?|follow[_-]?us|nav\b|^nav$|footer\b|^footer$|header\b|"
    r"site[_-]?footer|site[_-]?header|page[_-]?nav|menu[_-]?wrap",
    re.IGNORECASE,
)

_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_MULTI_BLANK = re.compile(r"\n{3,}")
_SKIP_URL_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "#")


class SemanticExtractor(HTMLParser):
    """Extract semantic text, title, description and links from an HTML page."""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url or ""
        self.title: str = ""
        self.description: str = ""
        self.canonical: str = ""
        self.links: list[str] = []
        self.headings: list[str] = []
        self.text: str = ""
        # Internal state
        self._texts: list[str] = []
        self._skipped: list[bool] = []          # stack mirror of active elements
        self._in_title = False
        self._title_parts: list[str] = []
        self._desc_found = False
        self._heading_parts: list[str] | None = None
        self._in_table = False
        self._row_cells = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_skipped(self) -> bool:
        return any(self._skipped)

    def _is_boilerplate(self, tag: str, raw: str) -> bool:
        if tag in ("script", "style", "noscript", "iframe", "svg", "math",
                   "template", "form", "textarea", "select", "button", "canvas",
                   "object", "embed", "video", "audio"):
            return True
        if tag in ("nav", "footer", "aside"):
            return True
        if _BOILERPLATE_RE.search(raw):
            return True
        return False

    def _resolve_url(self, href: str) -> str:
        href = href.strip()
        if not href or href.startswith(_SKIP_URL_SCHEMES):
            return ""
        if href.startswith("//"):
            href = "https:" + href
        return urljoin(self.base_url or "", href)

    # ------------------------------------------------------------------
    # HTMLParser overrides
    # ------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        lower = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        raw = f"{attr_map.get('id', '')} {attr_map.get('class', '')}"

        if lower == "title":
            self._in_title = True
            self._title_parts = []
        if lower == "link":
            rel = attr_map.get("rel", "").lower().split()
            href = attr_map.get("href", "")
            if href and "canonical" in rel and not self.canonical:
                resolved = self._resolve_url(href)
                if resolved:
                    self.canonical = resolved
        if lower == "meta":
            name = attr_map.get("name", "").lower()
            prop = attr_map.get("property", "").lower()
            content = attr_map.get("content", "")
            if not self._desc_found and (name == "description" or prop == "og:description"):
                self.description = content
                self._desc_found = True
            if prop == "og:title" and not self._title_parts:
                self._title_parts = [content]
        if lower == "a":
            href = attr_map.get("href", "")
            resolved = self._resolve_url(href)
            if resolved:
                self.links.append(resolved)
        if lower in {"h1", "h2", "h3"}:
            self._heading_parts = []
        # Table structure preservation: one row per line, cells pipe-separated.
        if lower == "table":
            self._in_table = True
            self._row_cells = 0
            self._texts.append("\n")
        elif self._in_table and lower == "tr":
            self._row_cells = 0
            self._texts.append("\n")
        elif self._in_table and lower in {"td", "th"}:
            if self._row_cells > 0:
                self._texts.append(" | ")
            self._row_cells += 1

        blocked = self._is_boilerplate(lower, raw)
        self._skipped.append(blocked)

        if lower in _TEXT_BLOCK_TAGS and not blocked:
            self._texts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
            self.title = _WHITESPACE.sub(" ", "".join(self._title_parts)).strip()
        if lower in {"h1", "h2", "h3"}:
            heading = _WHITESPACE.sub(" ", " ".join(self._heading_parts or [])).strip()
            if heading:
                self.headings.append(heading)
            self._heading_parts = None
        if lower in _TEXT_BLOCK_TAGS and not self._skipped:
            self._texts.append("\n")
        if lower == "table":
            self._in_table = False
            self._row_cells = 0
        if self._skipped:
            self._skipped.pop()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._is_skipped():
            return
        if not data.strip():
            return
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        self._texts.append(data + " ")

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------
    def result(self) -> dict:
        title = _WHITESPACE.sub(" ", self.title).strip()
        description = _WHITESPACE.sub(" ", self.description).strip()
        canonical = _WHITESPACE.sub(" ", self.canonical).strip()
        text = "".join(self._texts)
        text = re.sub(r"[ \t]+", " ", text)
        text = text.replace("\n ", "\n")
        text = text.replace(" \n", "\n")
        text = _MULTI_BLANK.sub("\n\n", text).strip()
        self.title = title
        self.description = description
        self.canonical = canonical
        self.text = text
        unique_links: list[str] = []
        seen: set[str] = set()
        for link in self.links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        self.links = unique_links
        return {
            "title": title,
            "description": description,
            "canonical": canonical,
            "text": text,
            "links": unique_links,
            "headings": list(self.headings),
        }


def extract_html(html: str, base_url: str = "") -> dict:
    """Convenience: parse an HTML string and return semantic page data."""
    parser = SemanticExtractor(base_url=base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.result()
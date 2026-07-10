"""Zero-key HTML→text extraction: stdlib only, deterministic.

BeautifulSoup-grade in ambition, html.parser in dependency weight:
scripts/styles dropped, block elements become line breaks, whitespace
collapsed. A keyed Firecrawl-grade fetcher replaces the whole fetch
capability behind the same seam — it never needs this module.
"""

from __future__ import annotations

from html.parser import HTMLParser


_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
_BLOCK = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
        "h6", "tr", "table", "section", "article", "header", "footer",
        "blockquote", "pre",
    }
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [" ".join(line.split()) for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (text, title) for one HTML document; plain text passes through."""
    lowered = html[:2048].lower()
    if "<html" not in lowered and "<body" not in lowered and "<div" not in lowered \
            and "<p" not in lowered and "<!doctype" not in lowered:
        collapsed = "\n".join(
            " ".join(line.split()) for line in html.splitlines()
        )
        return "\n".join(line for line in collapsed.split("\n") if line), ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Defensive: a malformed document yields whatever was parsed.
        pass
    return parser.text(), " ".join(parser.title.split())


__all__ = ["html_to_text"]

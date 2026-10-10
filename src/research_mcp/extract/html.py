"""HTML → markdown. trafilatura and lxml are imported inside the functions: importing trafilatura takes seconds."""

import html as html_lib
import re
from dataclasses import dataclass

THIN_CHARS = 300
# trafilatura is roughly quadratic on repetitive markup (1 MB of repeated <p> took ~24 s in review);
# real pages stay well below this (a 2.07 MB Wikipedia article took 4.3 s). Larger HTML uses plain text.
MAX_EXTRACT_CHARS = 3_000_000
CHALLENGE_MARKERS = ("just a moment", "cf-chl", "attention required", "captcha", "access denied")
_DROP_XPATH = "//script|//style|//nav|//footer|//aside|//noscript|//template"
_BLOCK_TAGS = ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
               "header", "main", "blockquote", "pre", "table", "ul", "ol", "dd", "dt")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-:.]+)""", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# lxml refuses str input that still carries an encoding declaration.
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


@dataclass(frozen=True)
class Extracted:
    title: str
    markdown: str

    @property
    def thin(self) -> bool:
        return len(self.markdown) < THIN_CHARS


def decode_body(body: bytes, header_charset: str | None) -> str:
    """Charset from the HTTP header, then <meta charset>, then UTF-8, then charset-normalizer's best guess."""
    meta = _META_CHARSET.search(body[:4096])
    for candidate in (header_charset, meta.group(1).decode("ascii", "ignore") if meta else None, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    from charset_normalizer import from_bytes

    best = from_bytes(body).best()
    return str(best) if best is not None else body.decode("utf-8", errors="replace")


def extract_html(html: str, url: str) -> Extracted:
    """Main text as markdown (links, images, tables kept). Falls back to plain body text when trafilatura
    finds too little; whichever is longer wins. Oversized HTML skips trafilatura entirely."""
    if len(html) > MAX_EXTRACT_CHARS:
        return Extracted(title=_tag_title(html) or url, markdown=plain_body_text(html))
    import trafilatura

    markdown = (trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
        favor_recall=True,
    ) or "").strip()
    if len(markdown) < THIN_CHARS:
        fallback = plain_body_text(html)
        if len(fallback) > len(markdown):
            markdown = fallback
    return Extracted(title=page_title(html, url), markdown=markdown)


def page_title(html: str, url: str) -> str:
    import trafilatura

    metadata = trafilatura.extract_metadata(html, default_url=url)
    if metadata is not None and metadata.title:
        return " ".join(metadata.title.split())
    return _tag_title(html) or url


def _tag_title(html: str) -> str:
    match = _TITLE.search(html)
    return " ".join(html_lib.unescape(match.group(1)).split()) if match else ""


def plain_body_text(html: str) -> str:
    """Visible-ish text of <body> without scripts, styles and page chrome, one paragraph per line block."""
    from lxml import etree
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(_XML_DECLARATION.sub("", html, count=1))
    except (etree.ParserError, ValueError):
        return ""
    for element in tree.xpath(_DROP_XPATH):
        element.drop_tree()
    body = tree.find(".//body")
    root = body if body is not None else tree
    for block in root.iter(*_BLOCK_TAGS):  # keep block elements on separate lines
        block.tail = "\n" + (block.tail or "")
    lines = (" ".join(line.split()) for line in root.text_content().splitlines())
    return "\n\n".join(line for line in lines if line)


def looks_like_challenge(status: int, html: str, extracted: Extracted) -> bool:
    """A bot wall: blocking status or a known challenge marker, and almost no readable text."""
    if not extracted.thin:
        return False
    if status in (403, 503):
        return True
    head = html[:50_000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)

"""read_webpage: one page, in full, as markdown, in chunks."""

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import anyio

from research_mcp.errors import ToolError
from research_mcp.extract.html import decode_body, extract_html, looks_like_challenge
from research_mcp.extract.mediawiki import parse_api_urls, parse_response
from research_mcp.extract.pdf import extract_pdf
from research_mcp.extract.pages import Page, Window, paginate
from research_mcp.net.client import FetchResult
from research_mcp.net.urls import check_url, normalize_url
from research_mcp.services import Services
from research_mcp.tools.common import one_line, wrap_untrusted

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
UNLABELED_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
# PDFs (papers, books) are often bigger than web pages; the 5 MB text limit stays for everything else.
PDF_LIMIT = 20 * 1024 * 1024
# Every text/* type except HTML is shown as-is; these application/* types are text too.
TEXT_APPLICATION_TYPES = frozenset({
    "application/xml", "application/javascript", "application/x-javascript", "application/ecmascript",
    "application/yaml", "application/x-yaml", "application/toml", "application/sql", "application/x-sh",
})
THIN_NOTE = (
    "the page has little readable text ({chars} characters); it may need JavaScript, "
    "which this version cannot run yet"
)
MEDIAWIKI_NOTE = (
    "the page showed a bot check, so the article was read through the wiki's official API (api.php); "
    "the layout may differ slightly from the web page"
)


async def read_webpage(
    services: Services, url: str, offset: int = 0, max_chars: int = 20_000, render: str = "auto"
) -> str:
    target = check_url(url)
    if render == "always":
        raise _render_unavailable(services.settings.render)
    key = (normalize_url(target), render)
    page = services.pages.get(key)
    if page is None:
        page = await _load(services, target)
        services.pages.put(key, page)
    return format_page(page, paginate(page.markdown, offset, max_chars))


def format_page(page: Page, window: Window) -> str:
    next_offset = "end" if window.next_offset is None else str(window.next_offset)
    lines = [
        f"[read_webpage] {page.url}",
        f"render: {page.render}   fetched: {page.fetched_at}",
        f"chars: {window.start}–{window.end} of {window.total}   next_offset: {next_offset}",
        wrap_untrusted(f"title: {page.title}\n\n{window.text}"),
    ]
    if page.note:
        lines.append(f"note: {page.note}")
    return "\n".join(lines)


async def _load(services: Services, url: str) -> Page:
    result = await services.fetcher.get(url, type_limits=dict.fromkeys(PDF_TYPES, PDF_LIMIT))
    content_type = result.content_type
    if result.status == 429:
        raise ToolError("rate_limited", "the site answered 429 Too Many Requests", "wait a few seconds and try again")
    if content_type in HTML_TYPES or (not content_type and _sniff_html(result.body)):
        try:
            return await _html_page(result)
        except ToolError as err:
            if err.code != "blocked_by_site":
                raise
            page = await _mediawiki_page(services, result.url)
            if page is None:
                raise
            return page
    if result.status >= 400:
        raise _http_error(result.status)
    if content_type in PDF_TYPES or (content_type in UNLABELED_TYPES and b"%PDF-" in result.body[:1024]):
        return await _pdf_page(result)
    if content_type.startswith("image/"):
        raise ToolError("not_text", f"{result.url} is an image ({content_type})", "use download_file to save it")
    if content_type == "application/json" or content_type.endswith("+json"):
        return _text_page(result, _pretty_json(decode_body(result.body, result.charset)))
    if content_type.startswith("text/") or content_type in TEXT_APPLICATION_TYPES or content_type.endswith("+xml"):
        return _text_page(result, decode_body(result.body, result.charset))
    raise ToolError(
        "unsupported_content",
        f"{content_type or 'unknown content'} cannot be read as text",
        "only web pages, text formats (plain text, CSV, JSON, XML, code) and PDF documents with a text layer are supported",
    )


async def _html_page(result: FetchResult) -> Page:
    html = decode_body(result.body, result.charset)
    # abandon_on_cancel: the tool deadline must win even while trafilatura is still busy in its thread.
    extracted = await anyio.to_thread.run_sync(extract_html, html, result.url, abandon_on_cancel=True)
    if looks_like_challenge(result.status, html, extracted):
        raise ToolError(
            "blocked_by_site",
            f"{urlsplit(result.url).hostname} showed a bot check or blocked the request (HTTP {result.status})",
            "this site cannot be read by this tool",
        )
    if result.status >= 400:
        raise _http_error(result.status)
    note = THIN_NOTE.format(chars=len(extracted.markdown)) if extracted.thin else None
    return Page(result.url, extracted.title, extracted.markdown, "static", _now(), note)


async def _mediawiki_page(services: Services, page_url: str) -> Page | None:
    """The same wiki article through the wiki's own api.php, or None if that does not work either."""
    for api_url in parse_api_urls(page_url):
        try:
            response = await services.fetcher.get(api_url)
        except ToolError:
            continue
        parsed = parse_response(response.body) if response.status == 200 else None
        if parsed is None:
            continue
        title, html = parsed
        extracted = await anyio.to_thread.run_sync(
            extract_html, f"<html><body>{html}</body></html>", page_url, abandon_on_cancel=True
        )
        return Page(page_url, title or extracted.title, extracted.markdown, "static", _now(), MEDIAWIKI_NOTE)
    return None


async def _pdf_page(result: FetchResult) -> Page:
    pdf = await anyio.to_thread.run_sync(extract_pdf, result.body, abandon_on_cancel=True)
    name = urlsplit(result.url).path.rsplit("/", 1)[-1] or result.url
    notes = [f"PDF with {pdf.pages} page{'' if pdf.pages == 1 else 's'}; the text is marked [page N]"]
    if pdf.extracted_pages < pdf.pages:
        notes.append(f"only pages 1-{pdf.extracted_pages} were read before the time limit")
    if pdf.empty_pages:
        notes.append(f"{pdf.empty_pages} of {pdf.extracted_pages} pages have no text (scanned or image-only)")
    return Page(result.url, pdf.title or one_line(name), pdf.text, "pdf", _now(), "; ".join(notes))


def _text_page(result: FetchResult, text: str) -> Page:
    name = urlsplit(result.url).path.rsplit("/", 1)[-1] or result.url
    return Page(result.url, one_line(name), text.strip(), "static", _now())


def _pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return text


def _sniff_html(body: bytes) -> bool:
    head = body[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or b"<html" in head


def _http_error(status: int) -> ToolError:
    return ToolError("http_error", f"the site answered HTTP {status}", "check the URL or try again later")


def _render_unavailable(setting: str) -> ToolError:
    reason = (
        "browser rendering is turned off (RESEARCH_MCP_RENDER=off)"
        if setting == "off"
        else "browser rendering is not available in this version yet"
    )
    return ToolError("render_unavailable", reason, "use render='auto' or render='never'")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

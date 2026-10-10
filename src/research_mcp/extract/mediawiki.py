"""MediaWiki fallback for read_webpage: when a wiki article's HTML page answers with a bot check (Fandom
does this intermittently behind Cloudflare), the same article is read through the wiki's own public API,
`api.php?action=parse`. Same User-Agent, an official endpoint built for programs: nothing is bypassed."""

import json
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit


def parse_api_urls(page_url: str) -> list[str]:
    """Candidate action=parse URLs for a wiki article URL, most likely first; [] if it is not one.

    /wiki/<title> pages try <prefix>/api.php (Fandom) before <prefix>/w/api.php (Wikipedia), where
    <prefix> keeps language paths such as Fandom's /es. index.php?title=... uses the api.php beside it.
    """
    parts = urlsplit(page_url)
    marker = parts.path.find("/wiki/")
    if marker != -1:
        title = unquote(parts.path[marker + len("/wiki/"):])
        prefix = parts.path[:marker]
        api_paths = [f"{prefix}/api.php", f"{prefix}/w/api.php"]
    elif parts.path.endswith("/index.php"):
        title = parse_qs(parts.query).get("title", [""])[0]
        api_paths = [parts.path[: -len("index.php")] + "api.php"]
    else:
        return []
    if not title:
        return []
    query = urlencode({
        "action": "parse", "page": title, "prop": "text|displaytitle",
        "format": "json", "formatversion": "2", "redirects": "1",
    })
    return [urlunsplit((parts.scheme, parts.netloc, path, query, "")) for path in api_paths]


def parse_response(body: bytes) -> tuple[str, str] | None:
    """(title, article HTML) from an action=parse JSON answer; None for API errors or anything else."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    parsed = data.get("parse") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        return None
    html, title = parsed.get("text"), parsed.get("title")
    if not isinstance(html, str) or not html.strip():
        return None
    return (title if isinstance(title, str) else ""), html

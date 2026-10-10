"""URL validation (before any request) and normalization (cache keys, de-duplication)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from research_mcp.errors import ToolError

MAX_URL_CHARS = 2048
DEFAULT_PORTS = {"http": 80, "https": 443}
TRACKING_PARAMS = frozenset({"fbclid", "gclid"})


def check_url(url: str) -> str:
    """Return the URL stripped of surrounding whitespace, or raise ToolError(invalid_url).

    Only checks the shape. Whether the host is public is decided at connect time (net.guard).
    """
    text = url.strip()
    if len(text) > MAX_URL_CHARS:
        raise _invalid(f"the URL is longer than {MAX_URL_CHARS} characters")
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise _invalid(f"could not parse the URL {text[:200]!r}") from None
    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise _invalid(f"only http and https URLs can be read, not {parts.scheme or 'a URL without a scheme'!r}")
    if not parts.hostname:
        raise _invalid("the URL has no host name")
    if parts.username is not None or parts.password is not None:
        raise _invalid("URLs with a user name or password are not allowed")
    return text


def normalize_url(url: str) -> str:
    """Canonical form: lowercase scheme and host, no default port, no fragment, no tracking parameters,
    no trailing slash except for the root path."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    netloc = host if port is None or port == DEFAULT_PORTS.get(scheme) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def _invalid(message: str) -> ToolError:
    return ToolError("invalid_url", message, "check the URL; it must start with http:// or https://")

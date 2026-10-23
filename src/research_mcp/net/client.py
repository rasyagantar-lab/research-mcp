"""The one HTTP client every tool uses: guarded connections, manual redirects, size caps, retries."""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin

import anyio
import httpcore2
import httpx2

from research_mcp.errors import ToolError
from research_mcp.net.guard import GuardedBackend
from research_mcp.net.urls import check_url

TEXT_LIMIT = 5 * 1024 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
BACKOFF = (0.5, 1.5)
MAX_RETRY_AFTER = 5.0
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([^\s;\"']+)", re.IGNORECASE)


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes
    disposition: str | None = None  # raw Content-Disposition header; download_file takes the file name from it


@dataclass(frozen=True)
class _Attempt:
    result: FetchResult | None
    location: str | None
    retry_after: float | None


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        backend: httpcore2.AsyncNetworkBackend | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        connect_timeout: float = 5.0,
        total_timeout: float = 15.0,
    ) -> None:
        transport = httpx2.AsyncHTTPTransport(trust_env=False)
        # httpx2's transport has no network_backend option, so swap in a pool that connects through the guard.
        transport._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=20,
            network_backend=backend if backend is not None else GuardedBackend(),
        )
        self._client = httpx2.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx2.Timeout(total_timeout, connect=connect_timeout),
            headers={"User-Agent": user_agent, "Accept": ACCEPT, "Accept-Language": "en;q=0.9, *;q=0.5"},
        )
        self._sleep = sleep
        self._total_timeout = total_timeout

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, url: str, *, max_bytes: int = TEXT_LIMIT, type_limits: Mapping[str, int] | None = None
    ) -> FetchResult:
        """GET with up to 5 redirects (each hop re-validated). Returns any final status; the caller maps it.

        `type_limits` raises the size cap for specific content types (e.g. PDFs); every other type keeps `max_bytes`.
        """
        current = check_url(url)
        for _hop in range(MAX_REDIRECTS + 1):
            attempt = await self._get_with_retries(current, max_bytes, type_limits)
            if attempt.result is not None:
                return attempt.result
            assert attempt.location is not None
            current = check_url(urljoin(current, attempt.location))
        raise ToolError("http_error", f"more than {MAX_REDIRECTS} redirects", "open the final address directly")

    async def _get_with_retries(
        self, url: str, max_bytes: int, type_limits: Mapping[str, int] | None
    ) -> _Attempt:
        for attempt_no in range(len(BACKOFF) + 1):
            last_try = attempt_no == len(BACKOFF)
            try:
                with anyio.fail_after(self._total_timeout):
                    attempt = await self._once(url, max_bytes, type_limits)
            except (TimeoutError, httpx2.TimeoutException):
                if last_try:
                    raise ToolError(
                        "timeout", f"the site did not answer within {self._total_timeout:.0f} seconds",
                        "try again later",
                    ) from None
                await self._sleep(BACKOFF[attempt_no])
                continue
            result = attempt.result
            if result is not None and _retryable(result.status) and not last_try:
                delay = attempt.retry_after if attempt.retry_after is not None else BACKOFF[attempt_no]
                await self._sleep(min(delay, MAX_RETRY_AFTER))
                continue
            return attempt
        raise AssertionError("unreachable")

    async def _once(self, url: str, max_bytes: int, type_limits: Mapping[str, int] | None) -> _Attempt:
        try:
            async with self._client.stream("GET", url) as response:
                location = response.headers.get("location")
                if response.status_code in REDIRECT_STATUSES and location:
                    return _Attempt(None, location, None)
                content_type_header = response.headers.get("content-type", "")
                limit = (type_limits or {}).get(content_type_header.split(";")[0].strip().lower(), max_bytes)
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise _too_large(limit)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise _too_large(limit)
                    chunks.append(chunk)
                charset = _CHARSET.search(content_type_header)
                result = FetchResult(
                    url=str(response.url),
                    status=response.status_code,
                    content_type=content_type_header.split(";")[0].strip().lower(),
                    charset=charset.group(1) if charset else None,
                    body=b"".join(chunks),
                    disposition=response.headers.get("content-disposition"),
                )
                return _Attempt(result, None, _retry_after(response.headers.get("retry-after")))
        except httpx2.TimeoutException:
            raise
        except httpx2.ConnectError as exc:
            raise ToolError(
                "http_error", f"could not connect to {httpx2.URL(url).host} ({exc})",
                "check the address; the site may be down",
            ) from None
        except httpx2.HTTPError as exc:
            raise ToolError("http_error", f"the connection failed ({type(exc).__name__})", "try again later") from None


def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


def _too_large(max_bytes: int) -> ToolError:
    limit = f"{max_bytes // (1024 * 1024)} MB" if max_bytes >= 1024 * 1024 else f"{max_bytes // 1024} KB"
    return ToolError("too_large", f"the response is larger than {limit}", "pick a smaller page")

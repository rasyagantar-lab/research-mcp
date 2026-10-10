"""search_web: meta-search through ddgs (no API key), behind a small provider interface."""

import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from typing import Protocol

import anyio

from research_mcp.errors import ToolError
from research_mcp.net.urls import normalize_url
from research_mcp.tools.common import one_line, wrap_untrusted

log = logging.getLogger("research_mcp.search")

RECENCY = {"any": None, "day": "d", "week": "w", "month": "m", "year": "y"}
SEARCH_DEADLINE = 30.0
RETRY_DELAY = 1.5
SNIPPET_CHARS = 300
GRACE_AFTER_FIRST_HITS = 2.0


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchFailed(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        # "rate_limited" | "timeout" | "failed": no engine answered at all.
        # "incomplete": nothing matched, but some engines did not answer, so "no results" is not certain.
        self.kind = kind
        self.detail = detail


class SearchProvider(Protocol):
    def search(self, query: str, *, max_results: int, timelimit: str | None, region: str) -> list[SearchHit]:
        """Blocking call. Returns [] when nothing matched; raises SearchFailed when engines failed
        (kind "incomplete" when nothing matched but some engines did not answer)."""
        ...


class DdgsProvider:
    """Asks every ddgs text engine separately, in parallel, and merges whatever comes back.

    ddgs's own "auto" mode stops waiting as soon as one engine raises, so a single blocked engine could
    throw away the results of slower engines that did answer; here one engine failing only costs its own
    results. Measured on the user's network (2026-10-06): DuckDuckGo is DNS-hijacked by the ISP and hangs
    until the timeout, and Yahoo answers in about a second. So the search returns `grace` seconds after
    the first hits instead of waiting for stragglers, and an engine that runs out of time is reported as a
    timeout, never as "0 results".
    """

    def __init__(
        self, timeout: float = 8, engines: tuple[str, ...] | None = None, grace: float = GRACE_AFTER_FIRST_HITS
    ) -> None:
        self._timeout = timeout
        self._engines = engines
        self._grace = grace

    def search(self, query: str, *, max_results: int, timelimit: str | None, region: str) -> list[SearchHit]:
        from ddgs.engines import ENGINES

        table = ENGINES["text"]
        engines = sorted(self._engines or tuple(table), key=lambda name: (-_priority(table, name), name))
        outcomes = self._ask_all(engines, query, max_results, timelimit, region)
        log.info("search engines: %s", " ".join(
            f"{engine}={_label(result, verbose=True)}" for engine, result in outcomes.items()
        ))
        answered = [result for result in outcomes.values() if isinstance(result, list)]
        if not answered:
            raise _all_failed(outcomes)
        hits = _rank(query, _interleave(answered))
        failed = {engine: _label(result) for engine, result in outcomes.items() if not isinstance(result, list)}
        if not hits and failed:
            names = ", ".join(f"{engine}: {label}" for engine, label in failed.items())
            raise SearchFailed("incomplete", f"{len(failed)} of {len(outcomes)} search engines did not answer ({names})")
        return hits

    def _ask_all(self, engines: list[str], query: str, max_results: int, timelimit: str | None, region: str):
        """Every engine's outcome; engines still busy when the search finishes count as timeouts."""
        from ddgs.exceptions import TimeoutException

        pool = ThreadPoolExecutor(max_workers=len(engines), thread_name_prefix="search")
        try:
            futures = {
                pool.submit(self._ask, engine, query, max_results, timelimit, region): engine for engine in engines
            }
            outcomes: dict[str, list[SearchHit] | Exception] = {}
            hard_stop = time.monotonic() + self._timeout + 1.0
            stop = hard_stop
            pending = set(futures)
            while pending:
                remaining = stop - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    outcomes[futures[future]] = result
                    if isinstance(result, list) and result and stop == hard_stop:
                        stop = min(hard_stop, time.monotonic() + self._grace)
            for future in pending:
                outcomes[futures[future]] = TimeoutException("still no answer when the search finished")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)  # stragglers finish on their own; nobody waits
        return {engine: outcomes[engine] for engine in engines}

    def _ask(self, engine: str, query: str, max_results: int, timelimit: str | None, region: str):
        """One engine's hits ([] for "no results"), or the exception it raised."""
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, TimeoutException

        started = time.monotonic()
        try:
            rows = DDGS(timeout=self._timeout).text(
                query, region=region, safesearch="moderate", timelimit=timelimit,
                max_results=max_results, backend=engine,
            )
        except DDGSException as exc:
            if str(exc) != "No results found.":
                return exc
            if time.monotonic() - started >= self._timeout:
                # ddgs reports an engine that missed its timeout as "No results found.", not as a timeout.
                return TimeoutException(f"no answer within {self._timeout:g} s")
            return []
        except Exception as exc:  # one engine failing in an unexpected way must not sink the whole search
            return exc
        return [
            SearchHit(title=row.get("title") or "", url=row["href"], snippet=row.get("body") or "")
            for row in rows
            if row.get("href")
        ]


def _priority(table: dict, name: str) -> float:
    """ddgs's own engine priority (Wikipedia 2, Grokipedia 1.9, the rest 1); unknown names count as 1."""
    return float(getattr(table.get(name), "priority", 1))


def _label(result: list[SearchHit] | Exception, verbose: bool = False) -> str:
    from ddgs.exceptions import RatelimitException, TimeoutException

    if isinstance(result, list):
        return str(len(result))
    if isinstance(result, TimeoutException):
        return "timeout"
    if isinstance(result, RatelimitException):
        return "rate_limited"
    return f"error({type(result).__name__})" if verbose else "error"


def _interleave(results: list[list[SearchHit]]) -> list[SearchHit]:
    """First hit of every engine, then every second hit, and so on (engines in priority order)."""
    merged: list[SearchHit] = []
    for rank in range(max(len(hits) for hits in results)):
        merged.extend(hits[rank] for hits in results if rank < len(hits))
    return merged


def _rank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
    """ddgs's own ordering: Wikipedia first, then hits with the query words in title and snippet, title only,
    snippet only, neither. Stable inside each group, so engine priority still breaks ties."""
    from ddgs.similarity import SimpleFilterRanker

    docs = [{"title": hit.title, "href": hit.url, "body": hit.snippet, "index": str(i)} for i, hit in enumerate(hits)]
    return [hits[int(doc["index"])] for doc in SimpleFilterRanker().rank(docs, query)]


def _all_failed(outcomes: dict[str, Exception]) -> SearchFailed:
    from ddgs.exceptions import RatelimitException, TimeoutException

    errors = list(outcomes.values())
    detail = "; ".join(f"{engine}: {type(exc).__name__}: {str(exc)[:120]}" for engine, exc in outcomes.items())
    if any(isinstance(exc, RatelimitException) for exc in errors):
        return SearchFailed("rate_limited", detail)
    if all(isinstance(exc, TimeoutException) for exc in errors):
        return SearchFailed("timeout", detail)
    return SearchFailed("failed", detail)


async def search_web(
    provider: SearchProvider,
    query: str,
    max_results: int = 8,
    recency: str = "any",
    region: str = "us-en",
    *,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    deadline: float = SEARCH_DEADLINE,
) -> str:
    query = one_line(query)
    if not query:
        raise ToolError("invalid_argument", "the search query is empty", "pass the words to search for")
    timelimit = RECENCY[recency]
    try:
        with anyio.fail_after(deadline):
            hits, note = await _search_with_retry(provider, query, max_results, timelimit, region, sleep)
    except TimeoutError:
        raise ToolError(
            "timeout", f"the search took longer than {deadline:.0f} seconds", "try again, or use fewer words"
        ) from None
    return format_hits(query, _dedupe(hits)[:max_results], recency, region, note)


async def _search_with_retry(
    provider, query, max_results, timelimit, region, sleep
) -> tuple[list[SearchHit], str | None]:
    """The hits plus, for an incomplete empty search, a note saying which engines did not answer."""
    call = partial(provider.search, query, max_results=max_results, timelimit=timelimit, region=region)
    last: SearchFailed | None = None
    for attempt in range(2):
        try:
            return await anyio.to_thread.run_sync(call, abandon_on_cancel=True), None
        except SearchFailed as exc:
            if exc.kind == "incomplete":
                return [], exc.detail  # retrying would only wait for the same silent engines again
            last = exc
            if attempt == 0:
                await sleep(RETRY_DELAY)
    assert last is not None
    if last.kind == "timeout":
        raise ToolError("timeout", "the search engines did not answer in time", "try again in a few seconds")
    raise ToolError(
        "rate_limited", f"all search engines refused or failed ({last.detail[:200]})", "wait a few seconds and try again"
    )


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        try:
            key = normalize_url(hit.url)
        except ValueError:  # e.g. a bad port in one result must not sink the whole search
            key = hit.url
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


def format_hits(query: str, hits: list[SearchHit], recency: str, region: str, note: str | None = None) -> str:
    settings = f"(recency={recency}, region={region})"
    if not hits:
        lines = [f'[search_web] "{query}" — no results {settings}']
        if note:
            lines.append(f"Note: {note}, so results may be incomplete.")
            lines.append("Tip: try different or fewer keywords, or another web search tool if one is available.")
        else:
            lines.append("Tip: try different or fewer keywords.")
        return "\n".join(lines)
    entries = []
    for number, hit in enumerate(hits, start=1):
        snippet = one_line(hit.snippet)
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[: SNIPPET_CHARS - 1].rstrip() + "…"
        entries.append(f"{number}. {one_line(hit.title) or '(no title)'}\n   {hit.url}\n   {snippet}")
    return "\n".join([
        f'[search_web] "{query}" — {len(hits)} results {settings}',
        wrap_untrusted("\n".join(entries)),
        "Tip: call read_webpage(url) to read a result in full.",
    ])

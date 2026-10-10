import time

import pytest
from fakes import FakeSearch

from research_mcp.errors import ToolError
from research_mcp.tools import search as search_module
from research_mcp.tools.common import UNTRUSTED_END, UNTRUSTED_START
from research_mcp.tools.search import DdgsProvider, SearchFailed, SearchHit, search_web

HITS = [
    SearchHit("First   result\n", "https://example.com/a?utm_source=x", "Snippet one."),
    SearchHit("Duplicate", "https://EXAMPLE.com/a/", "Same page again."),
    SearchHit("Second", "https://example.org/b", "x" * 400),
]


async def no_sleep(seconds):
    pass


@pytest.mark.anyio
async def test_formats_numbered_fenced_results():
    output = await search_web(FakeSearch(HITS), "python asyncio", sleep=no_sleep)
    lines = output.splitlines()
    assert lines[0] == '[search_web] "python asyncio" — 2 results (recency=any, region=us-en)'
    assert lines[1] == UNTRUSTED_START
    assert lines[2] == "1. First result"
    assert lines[3] == "   https://example.com/a?utm_source=x"
    assert lines[4] == "   Snippet one."
    assert lines[5] == "2. Second"
    assert UNTRUSTED_END in lines
    assert lines[-1] == "Tip: call read_webpage(url) to read a result in full."


@pytest.mark.anyio
async def test_long_snippets_are_cut_to_300_characters():
    output = await search_web(FakeSearch(HITS), "q", sleep=no_sleep)
    snippet = next(line for line in output.splitlines() if line.strip().startswith("xxx")).strip()
    assert len(snippet) == 300 and snippet.endswith("…")


@pytest.mark.anyio
async def test_passes_options_to_the_provider_and_trims_results():
    provider = FakeSearch(HITS)
    output = await search_web(provider, "  q  ", max_results=1, recency="week", region="id-id", sleep=no_sleep)
    assert provider.calls == [{"query": "q", "max_results": 1, "timelimit": "w", "region": "id-id"}]
    assert "— 1 results" in output


@pytest.mark.anyio
async def test_one_malformed_result_url_does_not_break_the_search():
    hits = [SearchHit("Bad port", "http://example.com:99999/x", "odd"), SearchHit("Good", "https://example.org/", "ok")]
    output = await search_web(FakeSearch(hits), "q", sleep=no_sleep)
    assert "2 results" in output
    assert "http://example.com:99999/x" in output


@pytest.mark.anyio
async def test_incomplete_search_says_so_and_is_not_retried():
    provider = FakeSearch(SearchFailed("incomplete", "1 of 8 search engines did not answer (duckduckgo: timeout)"))
    output = await search_web(provider, "obscure query", sleep=no_sleep)
    lines = output.splitlines()
    assert lines[0] == '[search_web] "obscure query" — no results (recency=any, region=us-en)'
    assert lines[1] == "Note: 1 of 8 search engines did not answer (duckduckgo: timeout), so results may be incomplete."
    assert lines[2].startswith("Tip: try different or fewer keywords")
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_no_results_is_not_an_error():
    output = await search_web(FakeSearch([]), "zzzz", sleep=no_sleep)
    assert output.startswith('[search_web] "zzzz" — no results')


@pytest.mark.anyio
async def test_blank_query_is_rejected():
    with pytest.raises(ToolError) as caught:
        await search_web(FakeSearch(HITS), "   ", sleep=no_sleep)
    assert caught.value.code == "invalid_argument"


@pytest.mark.anyio
async def test_retries_once_after_a_rate_limit():
    sleeps = []

    async def record(seconds):
        sleeps.append(seconds)

    provider = FakeSearch(SearchFailed("rate_limited", "202 Ratelimit"), HITS)
    output = await search_web(provider, "q", sleep=record)
    assert sleeps == [1.5]
    assert len(provider.calls) == 2
    assert "2 results" in output


@pytest.mark.anyio
@pytest.mark.parametrize(("kind", "code"), [("rate_limited", "rate_limited"), ("failed", "rate_limited"),
                                            ("timeout", "timeout")])
async def test_two_failures_become_an_error(kind, code):
    provider = FakeSearch(SearchFailed(kind, "boom"), SearchFailed(kind, "boom"))
    with pytest.raises(ToolError) as caught:
        await search_web(provider, "q", sleep=no_sleep)
    assert caught.value.code == code


@pytest.mark.anyio
async def test_deadline_stops_a_hanging_search():
    class Hanging:
        def search(self, query, **kwargs):
            time.sleep(1.0)
            return HITS

    started = time.perf_counter()
    with pytest.raises(ToolError) as caught:
        await search_web(Hanging(), "q", sleep=no_sleep, deadline=0.2)
    assert caught.value.code == "timeout"
    assert time.perf_counter() - started < 0.9


def row(name):
    return {"title": name, "href": f"https://example.com/{name}", "body": f"about {name}"}


def fake_ddgs(monkeypatch, answers):
    """Patch ddgs.DDGS: answers maps engine name -> list of rows, or an exception to raise."""
    import ddgs

    calls = []

    class FakeDDGS:
        def __init__(self, timeout):
            self.timeout = timeout

        def text(self, query, **kwargs):
            calls.append(dict(kwargs, query=query, timeout=self.timeout))
            answer = answers[kwargs["backend"]]
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    return calls


def test_ddgs_provider_asks_each_engine_separately_and_interleaves(monkeypatch):
    from ddgs.exceptions import DDGSException

    calls = fake_ddgs(monkeypatch, {
        "alpha": [row("a1"), row("a2"), {"title": "no link", "href": ""}],
        "beta": DDGSException("RequestError: invalid peer certificate: certificate expired"),
        "gamma": [row("g1")],
    })
    hits = DdgsProvider(timeout=8, engines=("alpha", "beta", "gamma")).search(
        "q", max_results=5, timelimit="m", region="us-en")
    assert [hit.title for hit in hits] == ["a1", "g1", "a2"]
    assert hits[0] == SearchHit("a1", "https://example.com/a1", "about a1")
    assert sorted(call["backend"] for call in calls) == ["alpha", "beta", "gamma"]
    assert calls[0] | {"backend": None} == {"query": "q", "timeout": 8, "region": "us-en", "safesearch": "moderate",
                                            "timelimit": "m", "max_results": 5, "backend": None}


def test_ddgs_provider_uses_every_text_engine_by_default(monkeypatch):
    from ddgs.engines import ENGINES

    calls = fake_ddgs(monkeypatch, {name: [] for name in ENGINES["text"]})
    DdgsProvider().search("q", max_results=5, timelimit=None, region="us-en")
    assert sorted(call["backend"] for call in calls) == sorted(ENGINES["text"])


def test_engines_without_results_mean_no_results_not_failure(monkeypatch):
    from ddgs.exceptions import DDGSException

    fake_ddgs(monkeypatch, {"alpha": DDGSException("No results found."), "beta": []})
    assert DdgsProvider(engines=("alpha", "beta")).search(
        "q", max_results=5, timelimit=None, region="us-en") == []


def test_no_hits_while_an_engine_failed_is_reported_as_incomplete(monkeypatch):
    from ddgs.exceptions import DDGSException

    fake_ddgs(monkeypatch, {"alpha": DDGSException("No results found."), "beta": [],
                            "gamma": DDGSException("RequestError: blocked")})
    with pytest.raises(SearchFailed) as caught:
        DdgsProvider(engines=("alpha", "beta", "gamma")).search("q", max_results=5, timelimit=None, region="us-en")
    assert caught.value.kind == "incomplete"
    assert caught.value.detail == "1 of 3 search engines did not answer (gamma: error)"


def slow_ddgs(monkeypatch, answers, delays):
    """Like fake_ddgs, but an engine listed in delays sleeps that long before answering."""
    import ddgs

    class SlowDDGS:
        def __init__(self, timeout):
            pass

        def text(self, query, **kwargs):
            engine = kwargs["backend"]
            time.sleep(delays.get(engine, 0))
            answer = answers[engine]
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(ddgs, "DDGS", SlowDDGS)


def test_an_engine_that_runs_out_of_time_is_a_timeout_not_an_empty_answer(monkeypatch, caplog):
    import logging

    from ddgs.exceptions import DDGSException

    # ddgs reports an engine that missed its timeout as "No results found.", not as a timeout.
    slow_ddgs(monkeypatch, {"alpha": [], "slow": DDGSException("No results found.")}, {"slow": 0.3})
    with caplog.at_level(logging.INFO, logger="research_mcp"), pytest.raises(SearchFailed) as caught:
        DdgsProvider(timeout=0.25, engines=("alpha", "slow")).search("q", max_results=5, timelimit=None, region="us-en")
    assert caught.value.kind == "incomplete"
    assert "slow: timeout" in caught.value.detail
    assert "slow=timeout" in caplog.text


def test_search_stops_waiting_for_stragglers_soon_after_the_first_hits(monkeypatch, caplog):
    import logging

    slow_ddgs(monkeypatch, {"alpha": [row("a1")], "slow": [row("s1")]}, {"slow": 1.5})
    started = time.perf_counter()
    with caplog.at_level(logging.INFO, logger="research_mcp"):
        hits = DdgsProvider(timeout=5, grace=0.2, engines=("alpha", "slow")).search(
            "q", max_results=5, timelimit=None, region="us-en")
    assert time.perf_counter() - started < 1.0
    assert [hit.title for hit in hits] == ["a1"]
    assert "slow=timeout" in caplog.text


def test_hits_are_ranked_like_ddgs_does(monkeypatch):
    fake_ddgs(monkeypatch, {
        "alpha": [{"title": "Ajagara", "href": "https://grokipedia.com/page/ajagara", "body": "a python or boa"}],
        "beta": [{"title": "Python tutorial", "href": "https://example.com/py", "body": "learn python"}],
        "gamma": [{"title": "Python (programming language)", "href": "https://en.wikipedia.org/wiki/Python",
                   "body": "a language"}],
    })
    hits = DdgsProvider(engines=("alpha", "beta", "gamma")).search(
        "python", max_results=5, timelimit=None, region="us-en")
    assert [hit.title for hit in hits] == ["Python (programming language)", "Python tutorial", "Ajagara"]


def test_engines_with_higher_ddgs_priority_come_first_within_a_rank(monkeypatch):
    fake_ddgs(monkeypatch, {
        "yahoo": [{"title": "Python from yahoo", "href": "https://example.com/y", "body": "python"}],
        "grokipedia": [{"title": "Python from grokipedia", "href": "https://example.com/g", "body": "python"}],
    })
    hits = DdgsProvider(engines=("yahoo", "grokipedia")).search("python", max_results=5, timelimit=None,
                                                                 region="us-en")
    assert [hit.title for hit in hits] == ["Python from grokipedia", "Python from yahoo"]


@pytest.mark.parametrize(("errors", "kind"), [
    (("RatelimitException", "DDGSException"), "rate_limited"),
    (("TimeoutException", "TimeoutException"), "timeout"),
    (("DDGSException", "DDGSException"), "failed"),
])
def test_search_fails_only_when_every_engine_errors(monkeypatch, errors, kind):
    from ddgs import exceptions

    fake_ddgs(monkeypatch, {
        "alpha": getattr(exceptions, errors[0])("alpha broke"),
        "beta": getattr(exceptions, errors[1])("beta broke"),
    })
    with pytest.raises(SearchFailed) as caught:
        DdgsProvider(engines=("alpha", "beta")).search("q", max_results=5, timelimit=None, region="us-en")
    assert caught.value.kind == kind
    assert "alpha" in caught.value.detail and "beta" in caught.value.detail


def test_engine_outcomes_are_logged_without_the_query(monkeypatch, caplog):
    import logging

    from ddgs.exceptions import DDGSException

    fake_ddgs(monkeypatch, {"alpha": [row("a1"), row("a2")], "beta": DDGSException("RequestError: blocked")})
    with caplog.at_level(logging.INFO, logger="research_mcp"):
        DdgsProvider(engines=("alpha", "beta")).search("secret words", max_results=5, timelimit=None, region="us-en")
    assert "alpha=2" in caplog.text
    assert "beta=error" in caplog.text
    assert "secret words" not in caplog.text


def test_recency_values_match_the_tool_schema():
    assert set(search_module.RECENCY) == {"any", "day", "week", "month", "year"}

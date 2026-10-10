"""Real internet checks. Not part of the default run: `pytest -m live`."""

import pytest

from research_mcp.config import load_settings
from research_mcp.services import open_services
from research_mcp.tools.search import DdgsProvider
from research_mcp.tools.webpage import read_webpage

pytestmark = pytest.mark.live


def test_ddgs_returns_real_results():
    hits = DdgsProvider().search("python programming language", max_results=5, timelimit=None, region="us-en")
    assert hits
    assert all(hit.url.startswith("http") for hit in hits)


@pytest.mark.anyio
async def test_reads_a_real_wikipedia_article_in_chunks():
    async with open_services(load_settings({})) as services:
        first = await read_webpage(services, "https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert "title: Python (programming language)" in first
    assert "next_offset: end" not in first


@pytest.mark.anyio
async def test_reads_a_fandom_article_directly_or_through_the_wiki_api():
    """Fandom sometimes answers with a Cloudflare bot check; either way the article text must come back."""
    async with open_services(load_settings({})) as services:
        output = await read_webpage(services, "https://blackclover.fandom.com/wiki/Chapter_316")
    assert "title: Chapter 316" in output
    assert "Asta" in output


@pytest.mark.anyio
async def test_reads_a_real_pdf_page_by_page():
    async with open_services(load_settings({})) as services:
        output = await read_webpage(services, "https://arxiv.org/pdf/1706.03762")
    assert output.splitlines()[1].startswith("render: pdf")
    assert "[page 1]" in output
    assert "note: PDF with" in output

import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from fakes import FakeSearch
from mcp import Client, StdioServerParameters

from research_mcp import server as server_module
from research_mcp.server import build_server
from research_mcp.tools.search import SearchHit


@pytest.fixture
async def client(services):
    @asynccontextmanager
    async def factory():
        yield services

    async with Client(build_server(factory)) as connected:
        yield connected


@pytest.mark.anyio
async def test_lists_two_read_only_tools_without_output_schema(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search_web", "read_webpage"}
    for tool in tools.values():
        assert tool.output_schema is None
        assert tool.annotations.read_only_hint is True
        assert "ctx" not in tool.input_schema["properties"]
    search_props = tools["search_web"].input_schema["properties"]
    assert set(search_props) == {"query", "max_results", "recency", "region"}
    assert search_props["recency"]["enum"] == ["any", "day", "week", "month", "year"]
    assert search_props["region"]["default"] == "us-en"  # wt-wt sends ddgs to the non-existent wt.wikipedia.org
    read_props = tools["read_webpage"].input_schema["properties"]
    assert set(read_props) == {"url", "offset", "max_chars", "render"}
    assert read_props["max_chars"]["maximum"] == 50_000
    assert "PDF" in tools["read_webpage"].description


@pytest.mark.anyio
async def test_search_result_is_plain_text(client):
    result = await client.call_tool("search_web", {"query": "example"})
    assert result.is_error is False
    assert result.structured_content is None
    assert [block.type for block in result.content] == ["text"]
    assert "1. Example" in result.content[0].text


@pytest.mark.anyio
async def test_read_webpage_through_mcp(client, site):
    site.add("/", "<html><head><title>Home</title></head><body><p>" + "Welcome to the site. " * 30 + "</p></body></html>")
    result = await client.call_tool("read_webpage", {"url": site.url("/")})
    assert result.is_error is False
    assert "title: Home" in result.content[0].text


@pytest.mark.anyio
async def test_tool_errors_become_is_error_results(client):
    result = await client.call_tool("read_webpage", {"url": "http://private.test/"})
    assert result.is_error is True
    assert result.structured_content is None
    assert result.content[0].text.startswith("Error [blocked_private_address]:")


@pytest.mark.anyio
async def test_unexpected_crash_is_reported_without_a_traceback(client, services):
    services.search = FakeSearch(RuntimeError("secret internal detail"))
    result = await client.call_tool("search_web", {"query": "boom"})
    assert result.is_error is True
    assert result.content[0].text.startswith("Error [internal_error]:")
    assert "secret internal detail" not in result.content[0].text
    assert "Traceback" not in result.content[0].text


@pytest.mark.anyio
async def test_tool_deadline(client, services, monkeypatch):
    import time

    class Slow:
        def search(self, query, **kwargs):
            time.sleep(1.0)
            return [SearchHit("late", "https://example.com/", "")]

    services.search = Slow()
    monkeypatch.setattr(server_module, "TOOL_DEADLINE", 0.2)
    result = await client.call_tool("search_web", {"query": "slow"})
    assert result.content[0].text.startswith("Error [timeout]:")


@pytest.mark.anyio
async def test_tool_deadline_covers_html_extraction(client, site, monkeypatch):
    import time

    from research_mcp.extract.html import Extracted
    from research_mcp.tools import webpage

    def slow_extract(html, url):
        time.sleep(2.0)
        return Extracted("late", "late text")

    site.add("/slow-extract", "<html><body><p>x</p></body></html>")
    monkeypatch.setattr(webpage, "extract_html", slow_extract)
    monkeypatch.setattr(server_module, "TOOL_DEADLINE", 0.3)
    started = time.perf_counter()
    result = await client.call_tool("read_webpage", {"url": site.url("/slow-extract")})
    assert result.content[0].text.startswith("Error [timeout]:")
    assert time.perf_counter() - started < 1.5


@pytest.mark.anyio
async def test_tools_write_nothing_to_stdout(client, site, capfd):
    site.add("/", "<html><body><p>" + "Quiet page text. " * 40 + "</p></body></html>")
    await client.call_tool("read_webpage", {"url": site.url("/")})
    await client.call_tool("search_web", {"query": "example"})
    assert capfd.readouterr().out == ""


@pytest.mark.anyio
async def test_logs_whether_the_client_offers_sampling(services, caplog, monkeypatch):
    async def sampler(context, params):
        raise NotImplementedError

    @asynccontextmanager
    async def factory():
        yield services

    monkeypatch.setattr(server_module, "_reported_protocol", False)
    with caplog.at_level(logging.INFO, logger="research_mcp"):
        async with Client(build_server(factory), sampling_callback=sampler) as connected:
            await connected.call_tool("search_web", {"query": "example"})
    assert "sampling: yes" in caplog.text


def test_importing_the_server_does_not_load_heavy_libraries():
    code = (
        "import sys, research_mcp.server, research_mcp.__main__; "
        "print([m for m in ('trafilatura', 'ddgs', 'lxml', 'primp', 'PIL', 'pypdf') if m in sys.modules])"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


@pytest.mark.anyio
async def test_stdio_server_starts_lists_tools_and_blocks_localhost(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp"],
        env={**os.environ, "RESEARCH_MCP_LOG_DIR": str(tmp_path), "PYTHONUTF8": "1"},
    )
    async with Client(params) as stdio_client:
        names = {tool.name for tool in (await stdio_client.list_tools()).tools}
        result = await stdio_client.call_tool("read_webpage", {"url": "http://127.0.0.1:9/"})
    assert names == {"search_web", "read_webpage"}
    assert result.content[0].text.startswith("Error [blocked_private_address]:")
    logs = list(tmp_path.glob("research-mcp-*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "client protocol version:" in log_text
    assert "sampling: no" in log_text  # the SDK client offers no sampling unless given a callback
    assert "read_webpage 127.0.0.1/ -> blocked_private_address" in log_text

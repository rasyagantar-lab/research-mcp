import socket
import time

import pytest

from research_mcp.errors import ToolError
from research_mcp.net.client import Fetcher


@pytest.mark.anyio
async def test_returns_body_status_and_content_type(site, make_fetcher):
    site.add("/page", "<p>hello</p>", content_type="text/html; charset=ISO-8859-1")
    result = await make_fetcher().get(site.url("/page"))
    assert result.status == 200
    assert result.body == b"<p>hello</p>"
    assert result.content_type == "text/html"
    assert result.charset == "ISO-8859-1"
    assert result.url == site.url("/page")


@pytest.mark.anyio
async def test_sends_the_user_agent(site, make_fetcher):
    site.add("/ua", "ok")
    await make_fetcher().get(site.url("/ua"))
    assert site.requests[-1][1]["User-Agent"] == "test-agent/1.0"


@pytest.mark.anyio
async def test_follows_relative_redirects_and_reports_the_final_url(site, make_fetcher):
    site.add("/old", status=301, headers={"Location": "/new"})
    site.add("/new", "moved here")
    result = await make_fetcher().get(site.url("/old"))
    assert result.body == b"moved here"
    assert result.url == site.url("/new")


@pytest.mark.anyio
async def test_redirect_to_a_private_host_is_blocked(site, make_fetcher):
    site.add("/jump", status=302, headers={"Location": site.url("/secret", host="private.test")})
    site.add("/secret", "internal data")
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/jump"))
    assert caught.value.code == "blocked_private_address"
    assert "/secret" not in site.paths()


@pytest.mark.anyio
async def test_redirect_to_another_scheme_is_rejected(site, make_fetcher):
    site.add("/jump", status=302, headers={"Location": "file:///C:/Windows/win.ini"})
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/jump"))
    assert caught.value.code == "invalid_url"


@pytest.mark.anyio
async def test_more_than_five_redirects_fail(site, make_fetcher):
    for n in range(7):
        site.add(f"/r{n}", status=302, headers={"Location": f"/r{n + 1}"})
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/r0"))
    assert caught.value.code == "http_error"
    assert "redirects" in caught.value.message


@pytest.mark.anyio
async def test_streamed_body_over_the_limit_is_cut_off(site, make_fetcher):
    chunk = b"x" * 4096
    chunked_body = b"".join(b"%x\r\n%s\r\n" % (len(chunk), chunk) for _ in range(4)) + b"0\r\n\r\n"
    site.add("/big", chunked_body, content_type="text/plain", headers={"Transfer-Encoding": "chunked"})
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/big"), max_bytes=8192)
    assert caught.value.code == "too_large"


@pytest.mark.anyio
async def test_declared_length_over_the_limit_fails_before_reading(site, make_fetcher):
    site.add("/big", b"y" * 20_000, content_type="text/plain")
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/big"), max_bytes=8192)
    assert caught.value.code == "too_large"
    assert "8 KB" in caught.value.message


@pytest.mark.anyio
async def test_type_limits_raise_the_cap_for_the_listed_content_types_only(site, make_fetcher):
    site.add("/doc.pdf", b"p" * 20_000, content_type="application/pdf")
    site.add("/doc.txt", b"t" * 20_000, content_type="text/plain")
    fetcher = make_fetcher()
    limits = {"application/pdf": 100_000}
    assert len((await fetcher.get(site.url("/doc.pdf"), max_bytes=8192, type_limits=limits)).body) == 20_000
    with pytest.raises(ToolError) as caught:
        await fetcher.get(site.url("/doc.txt"), max_bytes=8192, type_limits=limits)
    assert caught.value.code == "too_large"


@pytest.mark.anyio
async def test_a_listed_content_type_is_still_capped_by_its_own_limit(site, make_fetcher):
    site.add("/doc.pdf", b"p" * 20_000, content_type="application/pdf")
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(site.url("/doc.pdf"), max_bytes=8192, type_limits={"application/pdf": 10_000})
    assert caught.value.code == "too_large"


@pytest.mark.anyio
async def test_retries_a_503_then_succeeds(site, make_fetcher):
    answers = [(503, {"Content-Type": "text/plain"}, b"busy"), (200, {"Content-Type": "text/plain"}, b"ok")]
    site.routes["/flaky"] = lambda: answers.pop(0)
    sleeps = []
    result = await make_fetcher(sleeps=sleeps).get(site.url("/flaky"))
    assert result.status == 200
    assert sleeps == [0.5]


@pytest.mark.anyio
async def test_retry_after_is_respected_and_capped(site, make_fetcher):
    answers = [
        (429, {"Content-Type": "text/plain", "Retry-After": "3"}, b"slow down"),
        (429, {"Content-Type": "text/plain", "Retry-After": "120"}, b"slow down"),
        (200, {"Content-Type": "text/plain"}, b"ok"),
    ]
    site.routes["/limited"] = lambda: answers.pop(0)
    sleeps = []
    await make_fetcher(sleeps=sleeps).get(site.url("/limited"))
    assert sleeps == [3.0, 5.0]


@pytest.mark.anyio
async def test_gives_up_after_two_retries_and_returns_the_last_answer(site, make_fetcher):
    site.add("/down", "error page", status=500)
    result = await make_fetcher().get(site.url("/down"))
    assert result.status == 500
    assert site.paths().count("/down") == 3


@pytest.mark.anyio
async def test_slow_site_times_out_after_retries(site, make_fetcher):
    def slow():
        time.sleep(0.6)
        return 200, {"Content-Type": "text/plain"}, b"late"

    site.routes["/slow"] = slow
    with pytest.raises(ToolError) as caught:
        await make_fetcher(total_timeout=0.2).get(site.url("/slow"))
    assert caught.value.code == "timeout"


@pytest.mark.anyio
async def test_connection_refused_is_an_http_error(make_fetcher):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    with pytest.raises(ToolError) as caught:
        await make_fetcher().get(f"http://site.test:{free_port}/")
    assert caught.value.code == "http_error"


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
async def test_default_guard_blocks_local_addresses(site, host):
    fetcher = Fetcher("test-agent/1.0")
    try:
        with pytest.raises(ToolError) as caught:
            await fetcher.get(f"http://{host}:{site.port}/")
    finally:
        await fetcher.aclose()
    assert caught.value.code == "blocked_private_address"
    assert site.requests == []

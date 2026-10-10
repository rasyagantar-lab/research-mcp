import ipaddress

import httpcore2
import pytest

from research_mcp.errors import ToolError
from research_mcp.net.guard import BlockedAddressError, GuardedBackend, is_public_ip, system_resolver
from research_mcp.net.urls import check_url, normalize_url


@pytest.mark.parametrize("url", [
    "https://example.com/a?b=c",
    "  http://example.com  ",
    "http://[2606:4700::1]:8080/x",
])
def test_check_url_accepts_http_and_https(url):
    assert check_url(url) == url.strip()


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "example.com/no-scheme",
    "http://",
    "https://user:pass@example.com/",
    "http://example.com:99999/",
    "https://example.com/" + "a" * 2100,
])
def test_check_url_rejects_everything_else(url):
    with pytest.raises(ToolError) as caught:
        check_url(url)
    assert caught.value.code == "invalid_url"


@pytest.mark.parametrize(("url", "expected"), [
    ("HTTPS://Example.COM:443/Path/?utm_source=x&b=2&fbclid=y#frag", "https://example.com/Path?b=2"),
    ("http://example.com", "http://example.com/"),
    ("http://example.com:8080/a/", "http://example.com:8080/a"),
    ("http://[2606:4700::1]:80/", "http://[2606:4700::1]/"),
])
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


@pytest.mark.parametrize(("address", "public"), [
    ("8.8.8.8", True),
    ("2001:4860:4860::8888", True),
    ("::ffff:8.8.8.8", True),
    ("127.0.0.1", False),
    ("10.1.2.3", False),
    ("172.16.0.1", False),
    ("192.168.1.1", False),
    ("100.64.0.1", False),
    ("169.254.169.254", False),
    ("0.0.0.0", False),
    ("224.0.0.1", False),
    ("239.255.255.250", False),
    ("240.0.0.1", False),
    ("255.255.255.255", False),
    ("::1", False),
    ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("fe80::1", False),
    ("fc00::1", False),
    ("ff02::1", False),
    ("2002:7f00:1::", False),
    ("64:ff9b::7f00:1", False),
])
def test_is_public_ip(address, public):
    assert is_public_ip(ipaddress.ip_address(address)) is public


class FakeInner(httpcore2.AsyncNetworkBackend):
    def __init__(self, refuse=()):
        self.connected = []
        self.refuse = set(refuse)

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        if host in self.refuse:
            raise httpcore2.ConnectError(f"refused {host}")
        return f"stream to {host}"

    async def sleep(self, seconds):
        pass


def resolver_from(*answers):
    calls = []

    async def resolve(host, port):
        calls.append(host)
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    resolve.calls = calls
    return resolve


@pytest.mark.anyio
async def test_connects_by_ip_when_every_address_is_public():
    inner = FakeInner()
    backend = GuardedBackend(inner, resolver=resolver_from(["93.184.215.14"]))
    assert await backend.connect_tcp("example.com", 443) == "stream to 93.184.215.14"
    assert inner.connected == ["93.184.215.14"]


@pytest.mark.anyio
async def test_one_private_address_blocks_the_host():
    inner = FakeInner()
    backend = GuardedBackend(inner, resolver=resolver_from(["93.184.215.14", "10.0.0.5"]))
    with pytest.raises(BlockedAddressError) as caught:
        await backend.connect_tcp("mixed.example", 80)
    assert caught.value.code == "blocked_private_address"
    assert inner.connected == []


@pytest.mark.anyio
async def test_blocked_host_logs_every_address_with_its_verdict(caplog):
    import logging

    backend = GuardedBackend(FakeInner(), resolver=resolver_from(["93.184.215.14", "10.0.0.5"]))
    with caplog.at_level(logging.WARNING, logger="research_mcp"), pytest.raises(BlockedAddressError):
        await backend.connect_tcp("mixed.example", 80)
    assert "mixed.example" in caplog.text
    assert "10.0.0.5 (non-public)" in caplog.text
    assert "93.184.215.14 (public)" in caplog.text


@pytest.mark.anyio
async def test_blocked_message_points_at_dns_but_never_shows_the_address():
    backend = GuardedBackend(FakeInner(), resolver=resolver_from(["10.9.8.7"]))
    with pytest.raises(BlockedAddressError) as caught:
        await backend.connect_tcp("intranet.example", 80)
    text = caught.value.render()
    assert text.startswith("Error [blocked_private_address]: intranet.example points to a private")
    assert "DNS" in text
    assert "10.9.8.7" not in text  # internal addresses stay in the local log, out of what the model sees


@pytest.mark.anyio
async def test_tries_the_next_address_when_one_refuses():
    inner = FakeInner(refuse={"2606:4700::1"})
    backend = GuardedBackend(inner, resolver=resolver_from(["2606:4700::1", "93.184.215.14"]))
    assert await backend.connect_tcp("dual.example", 80) == "stream to 93.184.215.14"
    assert inner.connected == ["2606:4700::1", "93.184.215.14"]


@pytest.mark.anyio
async def test_raises_the_last_error_when_every_address_refuses():
    backend = GuardedBackend(FakeInner(refuse={"93.184.215.14"}), resolver=resolver_from(["93.184.215.14"]))
    with pytest.raises(httpcore2.ConnectError):
        await backend.connect_tcp("down.example", 80)


@pytest.mark.anyio
async def test_dns_rebinding_is_caught_on_the_next_connection():
    resolver = resolver_from(["93.184.215.14"], ["127.0.0.1"])
    backend = GuardedBackend(FakeInner(), resolver=resolver)
    await backend.connect_tcp("rebind.example", 80)
    with pytest.raises(BlockedAddressError):
        await backend.connect_tcp("rebind.example", 80)
    assert resolver.calls == ["rebind.example", "rebind.example"]


@pytest.mark.anyio
async def test_dns_failure_becomes_a_connect_error():
    backend = GuardedBackend(FakeInner(), resolver=resolver_from(OSError("getaddrinfo failed")))
    with pytest.raises(httpcore2.ConnectError):
        await backend.connect_tcp("nope.invalid", 80)


@pytest.mark.anyio
async def test_unix_sockets_are_refused():
    with pytest.raises(BlockedAddressError):
        await GuardedBackend(FakeInner()).connect_unix_socket("/tmp/socket")


@pytest.mark.anyio
async def test_system_resolver_resolves_localhost_without_network():
    addresses = await system_resolver("localhost", 80)
    assert addresses
    assert all(not is_public_ip(ipaddress.ip_address(a)) for a in addresses)

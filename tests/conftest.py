import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakes import FakeSearch

from research_mcp.config import load_settings
from research_mcp.extract.pages import PageCache
from research_mcp.net.client import Fetcher
from research_mcp.net.guard import GuardedBackend
from research_mcp.services import Services
from research_mcp.tools.search import SearchHit


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        site = self.server.site
        site.requests.append((self.path, dict(self.headers)))
        route = site.routes.get(self.path)
        if route is None:
            status, headers, body = 404, {"Content-Type": "text/plain"}, b"not found"
        else:
            status, headers, body = route() if callable(route) else route
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if "Content-Length" not in headers and "Transfer-Encoding" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Site:
    """A real HTTP server on 127.0.0.1. Tests reach it as http://site.test:<port>/ through a fake resolver."""

    host = "site.test"

    def __init__(self):
        self.routes = {}
        self.requests = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.site = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def url(self, path="/", host=None):
        return f"http://{host or self.host}:{self.port}{path}"

    def add(self, path, body=b"", status=200, content_type="text/html; charset=utf-8", headers=None):
        all_headers = {"Content-Type": content_type} if content_type else {}
        all_headers.update(headers or {})
        self.routes[path] = (status, all_headers, body if isinstance(body, bytes) else body.encode("utf-8"))

    def paths(self):
        return [path for path, _ in self.requests]


@pytest.fixture
def site():
    server = Site()
    server._thread.start()
    yield server
    server._server.shutdown()
    server._server.server_close()


TEST_HOSTS = {"site.test": ["127.0.0.1"], "other.test": ["127.0.0.1"], "private.test": ["127.0.0.2"]}


async def fake_resolver(host, port):
    if host not in TEST_HOSTS:
        raise OSError(f"unknown host {host}")
    return list(TEST_HOSTS[host])


def loopback_counts_as_public(ip):
    """In tests 127.0.0.1 plays a public site; everything else (127.0.0.2, real private ranges) stays blocked."""
    return str(ip) == "127.0.0.1"


@pytest.fixture
async def make_fetcher():
    created = []

    def make(sleeps=None, **kwargs):
        async def fake_sleep(seconds):
            if sleeps is not None:
                sleeps.append(seconds)

        backend = GuardedBackend(resolver=fake_resolver, ip_allowed=loopback_counts_as_public)
        fetcher = Fetcher("test-agent/1.0", backend=backend, sleep=fake_sleep, **kwargs)
        created.append(fetcher)
        return fetcher

    yield make
    for fetcher in created:
        await fetcher.aclose()


@pytest.fixture
def fake_search():
    return FakeSearch([SearchHit("Example", "https://example.com/", "An example page.")])


@pytest.fixture
async def services(make_fetcher, fake_search):
    return Services(settings=load_settings({}), fetcher=make_fetcher(), pages=PageCache(), search=fake_search)


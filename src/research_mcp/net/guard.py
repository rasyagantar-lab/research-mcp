"""SSRF guard at connect time: every address a host resolves to must be public, checked on each new connection.

Checking when the socket is opened (not only before the request) means a DNS answer that changes between
check and use (rebinding) cannot slip a private address through, and every redirect hop is covered too.
"""

import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import anyio
import httpcore2

from research_mcp.errors import ToolError

log = logging.getLogger("research_mcp.guard")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]
IpPolicy = Callable[[IPAddress], bool]


def is_public_ip(ip: IPAddress) -> bool:
    """Public unicast only: rejects loopback, private, shared (CGNAT), link-local (cloud metadata),
    multicast, reserved and unspecified addresses, including IPv4 hidden in IPv4-mapped IPv6."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


class BlockedAddressError(ToolError):
    def __init__(self, host: str) -> None:
        super().__init__(
            "blocked_private_address",
            f"{host} points to a private or local network address",
            "only public websites can be read; if this is a public site, the network's DNS may have given a "
            "wrong (local) answer, so try again in a moment or from another network",
        )


class GuardedBackend(httpcore2.AsyncNetworkBackend):
    """httpcore2 network backend that resolves the host itself, vets every address, then connects by IP.

    TLS still verifies the certificate against the original host name (httpcore2 passes it as SNI).
    """

    def __init__(
        self,
        inner: httpcore2.AsyncNetworkBackend | None = None,
        *,
        resolver: Resolver = system_resolver,
        ip_allowed: IpPolicy = is_public_ip,
    ) -> None:
        self._inner = inner if inner is not None else httpcore2.AnyIOBackend()
        self._resolver = resolver
        self._ip_allowed = ip_allowed

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            addresses = await self._resolver(host, port)
        except OSError as exc:
            raise httpcore2.ConnectError(f"could not resolve {host}: {exc}") from exc
        if not addresses:
            raise httpcore2.ConnectError(f"could not resolve {host}")
        verdicts = [(address, self._ip_allowed(ipaddress.ip_address(address))) for address in addresses]
        if not all(allowed for _, allowed in verdicts):
            # The addresses go to the local log only: the tool result must not reveal internal network layout.
            log.warning("blocked %s: resolved to %s", host, ", ".join(
                f"{address} ({'public' if allowed else 'non-public'})" for address, allowed in verdicts
            ))
            raise BlockedAddressError(host)
        last_error: Exception | None = None
        for address in addresses:  # e.g. Windows resolves localhost to ::1 before 127.0.0.1
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore2.AsyncNetworkStream:
        raise BlockedAddressError("a local socket")

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)

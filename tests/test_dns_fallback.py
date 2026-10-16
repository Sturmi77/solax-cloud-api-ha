"""DNS fallback: keep polling the last known IPs when the API host does not resolve."""

from __future__ import annotations

import socket
import ssl
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import aiohttp
import pytest
import trustme
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult

from custom_components.solax_cloud_evc.const import DATA_URL, DOMAIN
from custom_components.solax_cloud_evc.dns_fallback import (
    FallbackResolver,
    async_create_fallback_session,
)

from .conftest import TOTAL_ENERGY, realtime

API_HOST = urlsplit(DATA_URL).hostname
DNS_ERROR = "DNS server returned answer with no data"


class StubDNS(AbstractResolver):
    """Resolves every host to 127.0.0.1:<port> until switched to failing."""

    def __init__(self, port: int = 443) -> None:
        self.port = port
        self.fail = False
        self.lookups = 0
        self.failures = 0

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        self.lookups += 1
        if self.fail:
            self.failures += 1
            raise OSError(None, DNS_ERROR)  # what aiohttp's AsyncResolver raises
        return [
            {
                "hostname": host,
                "host": "127.0.0.1",
                "port": self.port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    async def close(self) -> None:
        pass


# ── Resolver unit tests ──────────────────────────────────────────────────────


async def test_resolver_returns_cached_answer_on_dns_error():
    """After one good lookup a DNS error is answered from the cache."""
    stub = StubDNS()
    resolver = FallbackResolver(stub)

    first = await resolver.resolve(API_HOST, 443)
    stub.fail = True
    second = await resolver.resolve(API_HOST, 443)

    assert second == first
    assert second[0]["hostname"] == API_HOST   # aiohttp keeps the hostname for SNI
    assert stub.failures == 1
    assert resolver.fallback_count == 1


async def test_resolver_without_cache_raises():
    """Without a previous good answer the DNS error propagates."""
    stub = StubDNS()
    stub.fail = True
    resolver = FallbackResolver(stub)

    with pytest.raises(OSError, match=DNS_ERROR):
        await resolver.resolve(API_HOST, 443)
    assert resolver.fallback_count == 0


async def test_resolver_refreshes_cache_after_recovery():
    """A successful lookup after an outage replaces the cached answer."""
    stub = StubDNS(port=1111)
    resolver = FallbackResolver(stub)
    await resolver.resolve(API_HOST, 443)
    stub.port = 2222                       # load balancer moved
    await resolver.resolve(API_HOST, 443)
    stub.fail = True

    assert (await resolver.resolve(API_HOST, 443))[0]["port"] == 2222


# ── End to end over TLS ──────────────────────────────────────────────────────


@pytest.fixture
def tls():
    """CA plus a server certificate for the real API hostname."""
    ca = trustme.CA()
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert(API_HOST).configure_cert(server_ctx)
    client_ctx = ssl.create_default_context()
    ca.configure_trust(client_ctx)
    return server_ctx, client_ctx


@pytest.fixture
async def api_server(tls, socket_enabled):
    """HTTPS server on 127.0.0.1 posing as the SolaxCloud API."""
    server_ctx, _ = tls
    seen: list[dict] = []
    sni: list[str | None] = []
    server_ctx.sni_callback = lambda _sock, name, _ctx: sni.append(name)

    async def realtime_data(request: web.Request) -> web.Response:
        seen.append({"sni": sni[-1], "host": request.host})
        resp = web.json_response(realtime(totalChargeEnergy=9990.7 + len(seen)))
        resp.force_close()   # new connection per request → every poll resolves again
        return resp

    app = web.Application()
    app.router.add_get(urlsplit(DATA_URL).path, realtime_data)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield port, seen
    await runner.cleanup()


async def test_session_keeps_working_on_dns_error_with_tls(api_server, tls):
    """A simulated DNS failure is bridged with the cached IP; SNI/cert still match."""
    port, seen = api_server
    _, client_ctx = tls
    stub = StubDNS(port)
    connector = aiohttp.TCPConnector(resolver=FallbackResolver(stub), ssl=client_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(DATA_URL) as resp:
            assert resp.status == 200

        stub.fail = True
        connector.clear_dns_cache()
        async with session.get(DATA_URL) as resp:
            assert resp.status == 200
            assert (await resp.json())["code"] == 10000

    assert stub.failures == 1
    assert [s["sni"] for s in seen] == [API_HOST, API_HOST]
    assert all(s["host"] == API_HOST for s in seen)


async def test_without_fallback_dns_error_breaks_request(api_server, tls):
    """Control: the same failure with the plain resolver is a ClientConnectorDNSError."""
    port, _ = api_server
    _, client_ctx = tls
    stub = StubDNS(port)
    connector = aiohttp.TCPConnector(resolver=stub, ssl=client_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(DATA_URL) as resp:
            assert resp.status == 200
        stub.fail = True
        connector.clear_dns_cache()
        with pytest.raises(aiohttp.ClientConnectorDNSError):
            await session.get(DATA_URL)


async def test_coordinator_keeps_polling_during_dns_outage(hass, config_entry, api_server, tls):
    """The integration's real session polls on through a DNS outage."""
    port, seen = api_server
    _, client_ctx = tls
    stub = StubDNS(port)
    with (
        patch(
            "custom_components.solax_cloud_evc.coordinator.async_create_fallback_session",
            async_create_fallback_session,
        ),
        patch("custom_components.solax_cloud_evc.dns_fallback.DefaultResolver", return_value=stub),
        patch("custom_components.solax_cloud_evc.dns_fallback.client_context", return_value=client_ctx),
        patch("custom_components.solax_cloud_evc.coordinator.asyncio.sleep", AsyncMock()),
    ):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert hass.states.get(TOTAL_ENERGY).state == "9991.7"

        stub.fail = True
        coordinator._session.connector.clear_dns_cache()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert stub.failures == 1
        assert coordinator.last_update_success
        assert coordinator.consecutive_failures == 0
        assert hass.states.get(TOTAL_ENERGY).state == "9992.7"
        assert [s["sni"] for s in seen] == [API_HOST, API_HOST]

        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert coordinator._session.closed

"""DNS fallback for the SolaxCloud API host.

openapi-eu.solaxcloud.com is a CNAME on an Aliyun load balancer with a 60 s TTL.
Its DNS answers repeatedly go missing for about an hour ("DNS server returned answer
with no data") while the load balancer itself stays reachable. During such an outage
every poll failed with ClientConnectorDNSError.

FallbackResolver wraps aiohttp's default resolver and remembers the last successful
answer per (host, port, family). When a lookup fails it returns that answer instead,
so requests go to the last known IPs. The URL still carries the hostname, so aiohttp
keeps sending it as TLS SNI and Host header and verifies the certificate against it.
"""

from __future__ import annotations

import logging
import socket
import time

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util.ssl import client_context

_LOGGER = logging.getLogger(__name__)


class FallbackResolver(AbstractResolver):
    """Resolver that falls back to the last good answer when DNS fails."""

    def __init__(self, resolver: AbstractResolver | None = None) -> None:
        """Wrap ``resolver`` (aiohttp's default resolver if omitted)."""
        self._resolver = resolver if resolver is not None else DefaultResolver()
        # (host, port, family) → (monotonic time of the lookup, answer)
        self._cache: dict[tuple[str, int, int], tuple[float, list[ResolveResult]]] = {}
        self._failing: set[str] = set()   # hosts currently served from the cache
        self.fallback_count: int = 0      # lookups answered from the cache

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """Resolve ``host``; on DNS failure return the last good answer if known."""
        key = (host, port, family)
        try:
            result = await self._resolver.resolve(host, port, family)
        except OSError as err:
            # aiohttp resolvers raise OSError for every lookup failure; the
            # connector turns it into ClientConnectorDNSError.
            cached = self._cache.get(key)
            if cached is None:
                raise
            resolved_at, answer = cached
            self.fallback_count += 1
            if host not in self._failing:
                self._failing.add(host)
                _LOGGER.warning(
                    "SolaxCloud: DNS lookup for %s failed (%s) — using last known "
                    "address(es) %s from %d s ago",
                    host,
                    err,
                    ", ".join(r["host"] for r in answer),
                    time.monotonic() - resolved_at,
                )
            return [dict(r) for r in answer]  # type: ignore[misc]

        if result:
            self._cache[key] = (time.monotonic(), [dict(r) for r in result])  # type: ignore[misc]
        if host in self._failing:
            self._failing.discard(host)
            _LOGGER.info("SolaxCloud: DNS lookup for %s works again", host)
        return result

    async def close(self) -> None:
        """Close the wrapped resolver."""
        await self._resolver.close()


@callback
def async_create_fallback_session(
    hass: HomeAssistant, entry: ConfigEntry
) -> aiohttp.ClientSession:
    """Create an aiohttp session that resolves hosts through FallbackResolver.

    HA's shared session cannot take a custom resolver, so the integration owns
    this session. It is closed when the config entry unloads (also after a failed
    setup) or when Home Assistant shuts down.
    """
    connector = aiohttp.TCPConnector(resolver=FallbackResolver(), ssl=client_context())
    session = aiohttp.ClientSession(connector=connector)

    async def _close(_event: Event | None = None) -> None:
        await session.close()

    entry.async_on_unload(_close)
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _close))
    return session

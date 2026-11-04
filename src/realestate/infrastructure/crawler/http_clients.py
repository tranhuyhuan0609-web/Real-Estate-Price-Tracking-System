"""Per-crawl pool of HTTP clients: one keep-alive client per proxy, reused for every request."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

import httpx

from realestate.domain.entities.crawler_config import ProxySettings


class HttpClientPool:
    """Owns the ``httpx.AsyncClient`` objects of one crawl session.

    * No proxy configured: a single direct client.
    * Proxy enabled, rotation disabled: a single client through the first proxy.
    * Rotation enabled: one client per proxy; every request (including retries) moves to the
      next proxy, so a blocked proxy is not retried against itself.
    * Tracks failures and cooldown per proxy based on ProxySettings.

    Clients are created on ``__aenter__`` and kept for the whole crawl, so TCP/TLS connections
    are pooled instead of re-established per request.
    """

    def __init__(self, proxy_settings: ProxySettings | None, **client_kwargs: Any) -> None:
        proxies: list[str | None] = [None]
        rotate = False
        max_failures = 3
        cooldown_seconds = 60
        if proxy_settings is not None and proxy_settings.enabled and proxy_settings.proxies:
            rotate = proxy_settings.rotation_enabled
            proxies = list(proxy_settings.proxies) if rotate else [proxy_settings.proxies[0]]
            max_failures = proxy_settings.max_failures
            cooldown_seconds = proxy_settings.cooldown_seconds

        self._proxies = proxies
        self._rotate = rotate
        self._max_failures = max_failures
        self._cooldown_seconds = cooldown_seconds
        self._client_kwargs = client_kwargs
        self._clients: list[httpx.AsyncClient] = []
        self._index = 0
        self._failures: dict[httpx.AsyncClient, int] = {}
        self._cooldown_until: dict[httpx.AsyncClient, float] = {}

    async def __aenter__(self) -> HttpClientPool:
        for proxy in self._proxies:
            kwargs = dict(self._client_kwargs)
            if proxy is not None:
                kwargs["proxy"] = proxy
            self._clients.append(httpx.AsyncClient(**kwargs))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        clients, self._clients = self._clients, []
        for client in clients:
            await client.aclose()

    def record_success(self, client: httpx.AsyncClient) -> None:
        """Reset consecutive failure counter on successful response."""
        self._failures[client] = 0
        self._cooldown_until.pop(client, None)

    def record_failure(self, client: httpx.AsyncClient) -> None:
        """Increment failure counter and trigger cooldown if threshold reached."""
        fails = self._failures.get(client, 0) + 1
        self._failures[client] = fails
        if fails >= self._max_failures:
            self._cooldown_until[client] = time.monotonic() + self._cooldown_seconds
            self._failures[client] = 0

    def next(self) -> httpx.AsyncClient:
        """Client for the next request."""
        if not self._clients:
            raise RuntimeError("HttpClientPool used outside of 'async with'.")
        if not self._rotate or len(self._clients) == 1:
            return self._clients[0]

        now = time.monotonic()
        total = len(self._clients)
        chosen = None
        for i in range(total):
            idx = (self._index + i) % total
            candidate = self._clients[idx]
            cooldown_time = self._cooldown_until.get(candidate, 0.0)
            if cooldown_time <= now:
                chosen = candidate
                self._index = (idx + 1) % total
                break

        if chosen is None:
            # If all are cooling down, fall back to simple round-robin
            chosen = self._clients[self._index % total]
            self._index = (self._index + 1) % total

        return chosen

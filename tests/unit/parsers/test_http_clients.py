from unittest.mock import AsyncMock, MagicMock

import pytest

from realestate.domain.entities.crawler_config import ProxySettings
from realestate.infrastructure.crawler.http_clients import HttpClientPool

P = ["http://p1:8080", "http://p2:8080", "http://p3:8080"]


@pytest.fixture
def fake_httpx(monkeypatch):
    made = []

    def factory(**kwargs):
        client = MagicMock(proxy=kwargs.get("proxy"))
        client.aclose = AsyncMock()
        made.append(client)
        return client

    monkeypatch.setattr("httpx.AsyncClient", factory)
    return made


async def test_rotation_reuses_one_client_per_proxy(fake_httpx):
    settings = ProxySettings(enabled=True, rotation_enabled=True, proxies=P)
    async with HttpClientPool(settings, follow_redirects=True) as pool:
        used = [pool.next().proxy for _ in range(7)]
    assert used == [P[0], P[1], P[2], P[0], P[1], P[2], P[0]]
    assert len(fake_httpx) == 3  # not one client per request
    assert all(c.aclose.await_count == 1 for c in fake_httpx)


async def test_proxy_without_rotation_sticks_to_first(fake_httpx):
    async with HttpClientPool(ProxySettings(enabled=True, proxies=P[:1])) as pool:
        assert {pool.next().proxy for _ in range(3)} == {P[0]}
    assert len(fake_httpx) == 1


async def test_no_proxy_is_a_single_direct_client(fake_httpx):
    async with HttpClientPool(ProxySettings()) as pool:
        assert pool.next() is pool.next()
    assert fake_httpx[0].proxy is None


def test_pool_must_be_entered():
    with pytest.raises(RuntimeError):
        HttpClientPool(None).next()

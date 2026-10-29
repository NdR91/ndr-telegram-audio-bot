from contextlib import contextmanager
import asyncio

import pytest

from bot.transcription.pool import ProviderPool
from bot.transcription.resilience import CircuitBreaker


class Repo:
    def __init__(self): self.credentials={("openai", 1): "synthetic-old", ("openai", 2): "synthetic-new"}
    def read_credential(self, service, version): return self.credentials[(service, version)]
    @contextmanager
    def credential_lease(self, service, version): yield


class Fake:
    def __init__(self, credential): self.credential=credential; self.closed=0
    def accepted_formats(self,*args): return frozenset({"mp3"})
    async def aclose(self): self.closed+=1


async def test_pool_keeps_clients_scoped_to_credential_version():
    repo=Repo(); pool=ProviderPool(repo, {"openai": Fake})
    clients=[]
    class TrackingFake(Fake):
        def __init__(self, credential):
            super().__init__(credential); clients.append(self)
    pool=ProviderPool(repo, {"openai": TrackingFake})
    async with pool.for_credential("openai", 1) as old:
        async with pool.for_credential("openai", 2) as new:
            assert old._adapter.credential == "synthetic-old"
            assert new._adapter.credential == "synthetic-new"
    await pool.aclose(); await pool.aclose()
    assert len(clients) == 2 and all(client.closed == 1 for client in clients)


async def test_breaker_survives_new_requests():
    breaker=CircuitBreaker(1, 60)
    async def broken(): raise RuntimeError("offline")
    with pytest.raises(RuntimeError): await breaker.call(broken)
    with pytest.raises(Exception, match="temporaneamente"): await breaker.call(broken)


async def test_pool_close_waits_for_active_leases():
    repo=Repo(); clients=[]
    class TrackingFake(Fake):
        def __init__(self, credential): super().__init__(credential); clients.append(self)
    pool=ProviderPool(repo, {"openai": TrackingFake})
    entered, release=asyncio.Event(), asyncio.Event()
    async def hold_lease():
        async with pool.for_credential("openai", 1):
            entered.set(); await release.wait()
    request=asyncio.create_task(hold_lease())
    await entered.wait()
    closing=asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    assert not closing.done() and clients[0].closed == 0
    release.set(); await request; await closing
    assert clients[0].closed == 1

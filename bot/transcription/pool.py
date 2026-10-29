"""Application-scoped provider clients keyed by service and credential version."""

from contextlib import asynccontextmanager
import asyncio

from bot.transcription.adapters.gemini import GeminiAdapter
from bot.transcription.adapters.openai import OpenAIAdapter
from bot.transcription.adapters.openrouter import OpenRouterAdapter
from bot.transcription.errors import ConfigurationInvalid
from bot.transcription.resilience import CircuitBreaker


class _ResilientAdapter:
    def __init__(self, adapter, breaker):
        self._adapter, self._breaker = adapter, breaker

    def accepted_formats(self, model, role):
        return self._adapter.accepted_formats(model, role)

    async def list_models(self, role):
        return await self._breaker.call(self._adapter.list_models, role)

    async def audio_to_text(self, *args):
        return await self._breaker.call(self._adapter.audio_to_text, *args)

    async def transcribe(self, *args):
        return await self._breaker.call(self._adapter.transcribe, *args)

    async def refine(self, *args):
        return await self._breaker.call(self._adapter.refine, *args)


class ProviderPool:
    def __init__(self, repo, adapters=None):
        self._repo = repo
        self._factories = adapters or {
            "openai": OpenAIAdapter,
            "gemini": GeminiAdapter,
            "openrouter": OpenRouterAdapter,
        }
        self._entries = {}
        self._breakers = {}
        self._condition = asyncio.Condition()
        self._active_leases = 0
        self._close_complete = asyncio.Event()
        self._closed = False

    async def _adapter(self, service, version):
        key = service, version
        async with self._condition:
            if key not in self._entries:
                if service not in self._factories:
                    raise ConfigurationInvalid("Provider service is invalid")
                credential = self._repo.read_credential(service, version)
                self._entries[key] = self._factories[service](credential)
                self._breakers[key] = CircuitBreaker(3, 60)
        return self._entries[key]

    @asynccontextmanager
    async def for_credential(self, service, version):
        async with self._condition:
            if self._closed:
                raise RuntimeError("ProviderPool is closed")
            self._active_leases += 1
        try:
            with self._repo.credential_lease(service, version):
                adapter = await self._adapter(service, version)
                yield _ResilientAdapter(adapter, self._breakers[(service, version)])
        finally:
            async with self._condition:
                self._active_leases -= 1
                if self._active_leases == 0:
                    self._condition.notify_all()

    @asynccontextmanager
    async def lease(self, snapshot):
        async with self.for_credential(snapshot.values.service, snapshot.values.credential_version) as adapter:
            yield adapter

    async def aclose(self):
        async with self._condition:
            if self._closed:
                waiter = True
            else:
                self._closed = True
                waiter = False
                await self._condition.wait_for(lambda: self._active_leases == 0)
                entries, self._entries = self._entries, {}
        if waiter:
            await self._close_complete.wait()
            return
        try:
            results = await asyncio.gather(
                *(adapter.aclose() for adapter in entries.values()),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                raise failures[0]
        finally:
            self._close_complete.set()

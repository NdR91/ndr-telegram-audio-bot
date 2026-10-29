import pytest

from bot.transcription.catalog import CatalogService
from bot.transcription.models import ModelChoice


class Pool:
    def __init__(self, adapter): self.adapter = adapter
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def for_credential(self, service, version): yield self.adapter


async def test_catalog_filters_by_protocol_and_role():
    class Adapter:
        async def list_models(self, role):
            return [ModelChoice("text", "Text", frozenset({"refinement"}), frozenset()),
                    ModelChoice("audio", "Audio", frozenset({"transcription"}), frozenset({"mp3"}))]
    choices = await CatalogService(Pool(Adapter())).choices("openai", 1, "transcription")
    assert [choice.id for choice in choices] == ["audio"]


async def test_catalog_outage_preserves_selection():
    class Adapter:
        async def list_models(self, role): raise RuntimeError("synthetic outage")
    with pytest.raises(RuntimeError):
        await CatalogService(Pool(Adapter())).choices("openai", 1, "refinement")

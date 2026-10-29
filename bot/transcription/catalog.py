"""Role-aware provider model choices without inferred capabilities."""

from bot.transcription.models import ModelChoice, ModelRole, ServiceId


class CatalogService:
    def __init__(self, pool):
        self._pool = pool

    async def choices(self, service: ServiceId, credential_version: int, role: ModelRole) -> list[ModelChoice]:
        async with self._pool.for_credential(service, credential_version) as adapter:
            models = await adapter.list_models(role)
            return [choice for choice in models if role in choice.roles]

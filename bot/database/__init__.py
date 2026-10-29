"""Shared secret-store primitives; V1 database access stays outside V2 imports."""

from bot.database.secret_store import SecretStore, SecretStoreError

__all__ = ["SecretStore", "SecretStoreError"]

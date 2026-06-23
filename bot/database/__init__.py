"""
Unified application database for configuration, access control, and audit.

Provides a versioned SQLite schema with explicit migrations, a DatabaseManager
that handles initialization and data access, a SecretStore for encrypting
credentials at rest, and legacy import utilities.

Typical usage::

    from bot.database import DatabaseManager, SecretStore

    store = SecretStore("audio_files/.master_key")
    store.initialize()

    db = DatabaseManager("audio_files/app.sqlite3", secret_store=store)
    db.initialize()
    db.set_setting("telegram_token", "123:abc")
    db.close()
"""

from bot.database.repository import DatabaseManager
from bot.database.secret_store import SecretStore, SecretStoreError

__all__ = ["DatabaseManager", "SecretStore", "SecretStoreError"]

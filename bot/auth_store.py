"""SQLite-backed authorization persistence."""

import logging
import os
import sqlite3
from typing import Any, Dict

logger = logging.getLogger(__name__)

AUTH_CATEGORIES = ("admin", "users", "groups")


class SQLiteWhitelistStore:
    """Persistent whitelist store backed by SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_parent_dir()
        self._initialize()

    def _ensure_parent_dir(self) -> None:
        parent_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent_dir, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS whitelist_entries (
                    category TEXT NOT NULL,
                    entry_id INTEGER NOT NULL,
                    PRIMARY KEY (category, entry_id)
                )
                """
            )
            connection.commit()

    def load_authorized_data(self) -> Dict[str, list[int]]:
        data = {category: [] for category in AUTH_CATEGORIES}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT category, entry_id FROM whitelist_entries ORDER BY category, entry_id"
            ).fetchall()

        for row in rows:
            data[row["category"]].append(row["entry_id"])
        return data

    def bootstrap_if_empty(self, bootstrap_data: Dict[str, Any]) -> Dict[str, list[int]]:
        current_data = self.load_authorized_data()
        if any(current_data[category] for category in AUTH_CATEGORIES):
            return current_data

        normalized = {
            category: [int(entry_id) for entry_id in bootstrap_data.get(category, [])]
            for category in AUTH_CATEGORIES
        }

        with self._connect() as connection:
            for category, values in normalized.items():
                connection.executemany(
                    "INSERT OR IGNORE INTO whitelist_entries (category, entry_id) VALUES (?, ?)",
                    [(category, entry_id) for entry_id in values],
                )
            connection.commit()

        logger.info("Whitelist database bootstrapped from authorized.json")
        return self.load_authorized_data()

    def replace_authorized_data(self, authorized_data: Dict[str, list[int]]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM whitelist_entries")
            for category, values in authorized_data.items():
                connection.executemany(
                    "INSERT INTO whitelist_entries (category, entry_id) VALUES (?, ?)",
                    [(category, int(entry_id)) for entry_id in values],
                )
            connection.commit()

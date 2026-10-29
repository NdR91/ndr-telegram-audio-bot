"""Contextual V2 access control backed by the isolated application database."""

from datetime import datetime, timezone
from typing import Literal

from bot.database.v2_repository import V2Repository

AccessKind = Literal["owner", "user", "group"]


class AccessService:
    def __init__(self, repo: V2Repository):
        self.repo = repo

    def authorize(self, kind: AccessKind, entry_id: int) -> None:
        _validate(kind, entry_id)
        with self.repo.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO acl_entries(kind, entry_id) VALUES (?, ?)",
                (kind, entry_id),
            )
            if kind == "owner":
                connection.execute(
                    "INSERT INTO setup_state(setup_key, setup_value) VALUES ('owner_bot_id', COALESCE((SELECT setup_value FROM setup_state WHERE setup_key='current_bot_id'), '')) "
                    "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value",
                )

    def remove(self, kind: AccessKind, entry_id: int) -> None:
        _validate(kind, entry_id)
        with self.repo.transaction() as connection:
            if kind == "owner":
                count = connection.execute(
                    "SELECT COUNT(*) FROM acl_entries WHERE kind='owner'"
                ).fetchone()[0]
                if count <= 1:
                    raise ValueError("At least one Telegram owner must remain")
            connection.execute(
                "DELETE FROM acl_entries WHERE kind=? AND entry_id=?", (kind, entry_id)
            )
            if kind == "owner":
                remaining = connection.execute(
                    "SELECT 1 FROM acl_entries WHERE kind='owner' LIMIT 1"
                ).fetchone()
                if remaining is None:
                    connection.execute("DELETE FROM setup_state WHERE setup_key='owner_bot_id'")

    def allowed(self, user_id: int | None, chat_id: int, chat_type: str) -> bool:
        with self.repo.read() as connection:
            if chat_type == "private" and user_id is not None:
                row = connection.execute(
                    "SELECT 1 FROM acl_entries WHERE kind IN ('owner','user') AND entry_id=?",
                    (user_id,),
                ).fetchone()
                return row is not None
            if chat_type in {"group", "supergroup"}:
                return connection.execute(
                    "SELECT 1 FROM acl_entries WHERE kind='group' AND entry_id=?", (chat_id,)
                ).fetchone() is not None
        return False

    def has_owner(self) -> bool:
        current_bot = self.repo.get_setup_state("current_bot_id")
        owner_bot = self.repo.get_setup_state("owner_bot_id")
        if not current_bot or current_bot != owner_bot:
            return False
        with self.repo.read() as connection:
            return connection.execute(
                "SELECT 1 FROM acl_entries WHERE kind='owner' LIMIT 1"
            ).fetchone() is not None

    def list_entries(self) -> dict:
        with self.repo.read() as connection:
            rows = connection.execute(
                "SELECT kind, entry_id FROM acl_entries ORDER BY kind, entry_id"
            ).fetchall()
        return {kind: [int(row["entry_id"]) for row in rows if row["kind"] == kind]
                for kind in ("owner", "user", "group")}

    def mark_received(self, chat_id: int, at: datetime) -> None:
        """Record only when an authorized group's voice/audio update was observed."""
        _validate("group", chat_id)
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Reception timestamp must be timezone-aware")
        received_at = at.astimezone(timezone.utc).isoformat()
        key = f"access_group_received:{chat_id}"
        with self.repo.transaction() as connection:
            current = connection.execute(
                "SELECT setup_value FROM setup_state WHERE setup_key=?", (key,)
            ).fetchone()
            if current is None or current[0] < received_at:
                connection.execute(
                    "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) "
                    "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value",
                    (key, received_at),
                )

    def list_entries_with_status(self) -> dict:
        """Return ACL IDs and group receive timestamps, never message history."""
        with self.repo.read() as connection:
            rows = connection.execute(
                "SELECT kind, entry_id FROM acl_entries ORDER BY kind, entry_id"
            ).fetchall()
            received = dict(connection.execute(
                "SELECT setup_key, setup_value FROM setup_state "
                "WHERE setup_key LIKE 'access_group_received:%'"
            ).fetchall())
        result = {"owner": [], "user": [], "group": []}
        for row in rows:
            entry_id = int(row["entry_id"])
            if row["kind"] == "group":
                result["group"].append({
                    "id": entry_id,
                    "last_received": received.get(f"access_group_received:{entry_id}"),
                    "membership": "unchecked",
                })
            else:
                result[row["kind"]].append(entry_id)
        return result


def _validate(kind: str, entry_id: int) -> None:
    if kind not in {"owner", "user", "group"} or isinstance(entry_id, bool) or not isinstance(entry_id, int):
        raise ValueError("Access entry is invalid")
    if not -(2**63) <= entry_id <= (2**63 - 1):
        raise ValueError("Telegram ID is outside the supported integer range")
    if kind in {"owner", "user"} and entry_id <= 0:
        raise ValueError("Telegram user IDs must be positive integers")

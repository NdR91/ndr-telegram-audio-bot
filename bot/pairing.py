"""Single-use Telegram pairing invitations with browser confirmation."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from bot.access import AccessKind, AccessService
from bot.database.v2_repository import V2Repository


@dataclass(frozen=True)
class PairingInvite:
    id: str
    link: str
    expires_at: datetime


class PairingService:
    def __init__(self, repo: V2Repository, access: AccessService, clock: Callable[[], datetime] | None = None):
        self.repo, self.access = repo, access
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def issue(self, kind: AccessKind, bot_username: str | None = None) -> PairingInvite:
        if kind not in {"owner", "user", "group"}:
            raise ValueError("Pairing kind is invalid")
        nonce = secrets.token_urlsafe(32)
        invite_id = secrets.token_urlsafe(18)
        expires = self.clock() + timedelta(seconds=600)
        bot_id = self.repo.get_setup_state("current_bot_id")
        if not bot_id:
            raise ValueError("Telegram bot must be connected before issuing an invitation")
        with self.repo.transaction() as connection:
            connection.execute(
                "INSERT INTO pairing_invites(invite_id, nonce_hash, kind, bot_id, expires_at) VALUES (?, ?, ?, ?, ?)",
                (invite_id, _hash(nonce), kind, int(bot_id) if bot_id else None, expires.isoformat()),
            )
        username = (bot_username or self.repo.get_setup_state("current_bot_username") or "").lstrip("@")
        parameter = "startgroup" if kind == "group" else "start"
        link = f"https://t.me/{username}?{parameter}=pair_{nonce}" if username else f"/pair/{nonce}"
        return PairingInvite(invite_id, link, expires)

    def observe(self, nonce: str, user_id: int | None, chat_id: int, chat_type: str, name: str = "") -> None:
        with self.repo.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_invites WHERE nonce_hash=?", (_hash(nonce),)
            ).fetchone()
            if (row is None or row["observed_at"] or row["confirmed_at"]
                    or self.clock() >= datetime.fromisoformat(row["expires_at"])):
                raise ValueError("Pairing invitation is invalid or expired")
            if row["bot_id"] is not None and self.repo.get_setup_state("current_bot_id") != str(row["bot_id"]):
                raise ValueError("Pairing invitation belongs to another bot")
            kind = row["kind"]
            if kind == "owner" and (chat_type != "private" or user_id is None):
                raise ValueError("Owner invitations must be observed in a private chat")
            if kind == "user" and (chat_type != "private" or user_id is None):
                raise ValueError("User invitations must be observed in a private chat")
            if kind == "group" and chat_type not in {"group", "supergroup"}:
                raise ValueError("Group invitations must be observed in a group")
            candidate = user_id if kind in {"owner", "user"} else chat_id
            if row["candidate_id"] is not None and row["candidate_id"] != candidate:
                raise ValueError("Pairing invitation already has another candidate")
            connection.execute(
                "UPDATE pairing_invites SET candidate_id=?, candidate_name=?, candidate_chat_id=?, candidate_chat_type=?, observed_at=? WHERE invite_id=?",
                (candidate, name[:200], chat_id, chat_type, self.clock().isoformat(), row["invite_id"]),
            )

    def get(self, invite_id: str) -> dict:
        with self.repo.read() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_invites WHERE invite_id=?", (invite_id,)
            ).fetchone()
        if row is None or not row["observed_at"] or row["confirmed_at"]:
            raise ValueError("Pairing candidate is unavailable")
        if self.clock() >= datetime.fromisoformat(row["expires_at"]):
            raise ValueError("Pairing invitation is expired")
        return {key: row[key] for key in ("invite_id", "kind", "candidate_id", "candidate_name", "candidate_chat_type")}

    def confirm(self, invite_id: str) -> None:
        with self.repo.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_invites WHERE invite_id=?", (invite_id,)
            ).fetchone()
            if row is None or not row["observed_at"] or row["confirmed_at"] or self.clock() >= datetime.fromisoformat(row["expires_at"]):
                raise ValueError("Pairing invitation is invalid, expired or already used")
            if row["bot_id"] is not None and self.repo.get_setup_state("current_bot_id") != str(row["bot_id"]):
                raise ValueError("Pairing invitation belongs to another bot")
            connection.execute(
                "INSERT OR IGNORE INTO acl_entries(kind, entry_id) VALUES (?, ?)",
                (row["kind"], row["candidate_id"]),
            )
            if row["kind"] == "owner":
                connection.execute(
                    "INSERT INTO setup_state(setup_key, setup_value) VALUES ('owner_bot_id', ?) ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value",
                    (str(row["bot_id"] or self.repo.get_setup_state("current_bot_id") or ""),),
                )
            connection.execute(
                "UPDATE pairing_invites SET confirmed_at=? WHERE invite_id=?",
                (self.clock().isoformat(), invite_id),
            )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

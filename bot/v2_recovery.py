"""V2 one-time password recovery with UTC expiry and a local code command."""

import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone

from bot.database.secret_store import SecretStore
from bot.database.v2_repository import V2Repository
from bot.deployment import DeploymentSettings
from bot.web.auth import hash_password

TTL = timedelta(minutes=30)


def issue_recovery_code(repo: V2Repository, now: datetime | None = None) -> str:
    if not repo.get_setup_state("web_admin_hash"):
        raise ValueError("Web administrator has not been created")
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    instant = now or datetime.now(timezone.utc)
    with repo.transaction() as connection:
        connection.executemany(
            "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, updated_at=datetime('now')",
            (("v2_recovery_hash", hashlib.sha256(code.encode()).hexdigest()),
             ("v2_recovery_expires_at", (instant + TTL).isoformat())),
        )
    return code


def reset_password(repo: V2Repository, code: str, new_password: str,
                   now: datetime | None = None) -> bool:
    if not new_password:
        raise ValueError("Password is required")
    digest = hashlib.sha256(code.encode()).hexdigest()
    instant = now or datetime.now(timezone.utc)
    if not _recovery_code_is_valid(repo, digest, instant):
        return False
    encoded_password = hash_password(new_password)
    with repo.transaction() as connection:
        rows = dict(connection.execute(
            "SELECT setup_key, setup_value FROM setup_state WHERE setup_key IN ('v2_recovery_hash', 'v2_recovery_expires_at')"
        ).fetchall())
        try:
            expires = datetime.fromisoformat(rows.get("v2_recovery_expires_at", ""))
        except ValueError:
            return False
        stored = rows.get("v2_recovery_hash", "")
        if expires.tzinfo is None or instant >= expires or not secrets.compare_digest(stored, digest):
            return False
        connection.execute(
            "UPDATE setup_state SET setup_value=?, updated_at=datetime('now') WHERE setup_key='web_admin_hash'",
            (encoded_password,),
        )
        connection.execute(
            "UPDATE setup_state SET setup_value='', updated_at=datetime('now') WHERE setup_key IN ('v2_recovery_hash', 'v2_recovery_expires_at')"
        )
    return True


def _recovery_code_is_valid(repo: V2Repository, digest: str, instant: datetime) -> bool:
    with repo.read() as connection:
        rows = dict(connection.execute(
            "SELECT setup_key, setup_value FROM setup_state "
            "WHERE setup_key IN ('v2_recovery_hash', 'v2_recovery_expires_at')"
        ).fetchall())
    try:
        expires = datetime.fromisoformat(rows.get("v2_recovery_expires_at", ""))
    except ValueError:
        return False
    return (expires.tzinfo is not None and instant < expires
            and secrets.compare_digest(rows.get("v2_recovery_hash", ""), digest))


def main() -> None:
    settings = DeploymentSettings.from_environment()
    settings.validate_database_target()
    secret_store = SecretStore(str(settings.key_path))
    repo = V2Repository(settings.db_path, secret_store)
    try:
        repo.initialize()
        print(issue_recovery_code(repo))
    finally:
        repo.close()


if __name__ == "__main__":
    main()

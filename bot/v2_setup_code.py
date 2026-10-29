"""Locally generate or renew the one-time V2 administrator setup code."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from bot.database.secret_store import SecretStore
from bot.database.v2_repository import V2Repository
from bot.deployment import DeploymentSettings


def issue_setup_code(repo: V2Repository, now: datetime | None = None) -> str:
    if repo.get_setup_state("web_admin_hash"):
        raise ValueError("Web administrator already exists")
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    current = now or datetime.now(timezone.utc)
    with repo.transaction() as connection:
        connection.executemany(
            "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, updated_at=datetime('now')",
            (("setup_code_hash", hashlib.sha256(code.encode()).hexdigest()),
             ("setup_code_expires_at", (current + timedelta(minutes=30)).isoformat())),
        )
    return code


def main() -> None:
    settings = DeploymentSettings.from_environment()
    settings.validate_database_target()
    store = SecretStore(str(settings.key_path))
    repo = V2Repository(settings.db_path, store)
    try:
        repo.initialize()
        print(issue_setup_code(repo))
    finally:
        repo.close()


if __name__ == "__main__":
    main()

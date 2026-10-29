"""Infrastructure-only deployment settings for the V2 web application."""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentSettings:
    data_dir: Path
    db_path: Path
    key_path: Path
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    secure_cookie: bool = False

    @classmethod
    def from_environment(cls) -> "DeploymentSettings":
        data_dir = Path(os.environ.get("AUDIO_DIR", "audio_files")).expanduser()
        db_path = Path(os.environ.get("APPLICATION_DB", str(data_dir / "app-v2.sqlite3"))).expanduser()
        key_path = Path(os.environ.get("MASTER_KEY_FILE", str(data_dir / ".master_key-v2"))).expanduser()
        try:
            port = int(os.environ.get("WEB_PORT", "8080"))
        except ValueError as exc:
            raise ValueError("WEB_PORT must be between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("WEB_PORT must be between 1 and 65535")
        secure = os.environ.get("WEB_SECURE_COOKIE", "0")
        if secure not in {"0", "1"}:
            raise ValueError("WEB_SECURE_COOKIE must be 0 or 1")
        return cls(data_dir, db_path, key_path, os.environ.get("WEB_HOST", "0.0.0.0"), port,
                   os.environ.get("WEB_LOG_LEVEL", "info").lower(), secure == "1")

    def validate_database_target(self) -> None:
        """Reject a known V1 database before creating or modifying it."""
        if not self.db_path.exists():
            return
        try:
            connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "app_metadata" in tables:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(app_metadata)")}
                    if {"metadata_key", "metadata_value"} <= columns:
                        row = connection.execute("SELECT metadata_value FROM app_metadata WHERE metadata_key='database_kind'").fetchone()
                        if row and row[0] != "telegram-audio-bot-v2":
                            raise ValueError("APPLICATION_DB points to a non-V2 database; migration is not automatic")
                if tables and "app_metadata" not in tables:
                    raise ValueError("APPLICATION_DB is not a compatible V2 database")
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError("APPLICATION_DB is not a valid SQLite database") from exc

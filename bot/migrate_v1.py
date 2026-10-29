"""One-shot importer for operator-created V1 database and key copies."""

import argparse
import hmac
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from bot.database.secret_store import SecretStore, SecretStoreError
from bot.database.v2_repository import V2Repository
from bot.transcription.models import ConfigInput, RuntimeLimits
from bot.transcription.profiles import ProfileService


@dataclass(frozen=True)
class MigrationReport:
    imported: dict[str, int]
    needs_reconfiguration: bool
    warnings: tuple[str, ...]


def migrate_v1(source_db: Path, source_key: Path, target_db: Path,
               target_key: Path) -> MigrationReport:
    source_db, source_key, target_db, target_key = map(
        lambda value: Path(value).expanduser().resolve(),
        (source_db, source_key, target_db, target_key),
    )
    if len({source_db, source_key, target_db, target_key}) != 4:
        raise ValueError("Source and target paths must be distinct")
    if not source_db.is_file() or not source_key.is_file():
        raise ValueError("A V1 database copy and its key copy are required")

    # Idempotent retry is allowed only for our own marked V2 target.
    if target_db.exists():
        if not target_db.is_file():
            raise ValueError("V2 target must be a file path")
        try:
            check = sqlite3.connect(f"file:{quote(str(target_db))}?mode=ro", uri=True)
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "app_metadata" not in tables:
                raise ValueError("V2 target must be empty")
            marker = check.execute(
                "SELECT metadata_value FROM app_metadata WHERE metadata_key='v1_import_completed'"
            ).fetchone()
            if marker:
                return MigrationReport({}, False, ())
            raise ValueError("V2 target must be empty")
        except sqlite3.DatabaseError as exc:
            raise ValueError("V2 target is not a compatible migration target") from exc
        finally:
            try:
                check.close()
            except UnboundLocalError:
                pass

    source_secret = SecretStore(str(source_key))
    try:
        source_secret.initialize()
    except SecretStoreError as exc:
        raise ValueError("V1 key copy cannot be opened") from exc
    source = _open_source_readonly(source_db)
    try:
        snapshot = _read_source(source, source_secret)
    finally:
        source.close()

    target_secret = SecretStore(str(target_key))
    try:
        target_secret.initialize()
    except (OSError, SecretStoreError) as exc:
        raise ValueError("V2 key cannot be initialized") from exc
    repository = V2Repository(target_db, target_secret)
    try:
        repository.initialize()
        if not repository.import_v1_payload(snapshot["payload"]):
            return MigrationReport({}, snapshot["needs_reconfiguration"], snapshot["warnings"])
        legacy_revision = snapshot["payload"].get("revision")
        if legacy_revision:
            values = legacy_revision["values"]
            ProfileService(repository).create(
                "Configurazione importata V1",
                ConfigInput(
                    service=values["service"], mode=values["mode"],
                    audio_model=values["audio_model"], text_model=values["text_model"],
                    credential_version=1,
                    system_prompt=values["system_prompt"],
                    refine_template=values["refine_template"],
                    limits=RuntimeLimits(**values["limits"]),
                ),
            )
            # The old singleton draft is not a runtime source after import.
            with repository.transaction() as connection:
                connection.execute("DELETE FROM config_references")
                connection.execute("DELETE FROM test_receipts")
    except Exception:
        repository.close()
        # Remove only a database created by this invocation; never erase a preexisting target.
        try:
            target_db.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        repository.close()
    return MigrationReport(snapshot["imported"], snapshot["needs_reconfiguration"], snapshot["warnings"])


def _open_source_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.DatabaseError as exc:
        raise ValueError("V1 source is not a readable SQLite database") from exc


def _read_source(connection: sqlite3.Connection, secret_store: SecretStore) -> dict:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"setup_state", "app_settings", "admin_users", "authorized_users", "authorized_groups",
                "provider_connections", "pipeline_profiles"}
    if not required <= tables:
        raise ValueError("V1 database schema is not supported")
    setup = dict(connection.execute("SELECT setup_key, setup_value FROM setup_state"))
    settings = dict(connection.execute("SELECT setting_key, setting_value FROM app_settings"))
    payload = {"acl": [], "setup_state": [], "credentials": [], "revision": None}
    admins = [int(row[0]) for row in connection.execute("SELECT entry_id FROM admin_users ORDER BY entry_id")]
    payload["acl"].extend(("owner", entry_id) for entry_id in admins)
    payload["acl"].extend(("user", int(row[0])) for row in connection.execute(
        "SELECT entry_id FROM authorized_users ORDER BY entry_id"))
    payload["acl"].extend(("group", int(row[0])) for row in connection.execute(
        "SELECT entry_id FROM authorized_groups ORDER BY entry_id"))

    admin_hash = setup.get("admin_password_hash") or setup.get("web_admin_hash")
    if admin_hash:
        payload["setup_state"].append(("web_admin_hash", admin_hash))
    for name, value in (("telegram_token", settings.get("telegram_token")),):
        if value:
            payload["credentials"].append(("telegram", _decrypt(secret_store, value)))

    profile, credentials, draft, needs_reconfiguration, warnings = _profile(connection, setup, secret_store)
    payload["credentials"].extend(credentials)
    payload["revision"] = draft

    valid_limits = _limits(settings)
    if draft:
        draft["values"]["limits"] = valid_limits
    imported = {
        "acl": len(payload["acl"]),
        "credentials": len(payload["credentials"]),
        "admin_hash": int(bool(admin_hash)),
        "draft": int(draft is not None),
    }
    return {"payload": payload, "imported": imported,
            "needs_reconfiguration": needs_reconfiguration, "warnings": tuple(warnings)}


def _profile(connection, setup, secret_store):
    active = setup.get("active_pipeline_profile")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not active:
        return None, [], None, False, []
    row = connection.execute("SELECT * FROM pipeline_profiles WHERE id=?", (active,)).fetchone()
    if row is None:
        return None, [], None, True, ["The saved profile needs V2 reconfiguration."]
    if not {"pipeline_stages", "provider_models", "pipeline_stage_fallbacks"} <= tables:
        provider_ids = {row[key] for key in ("transcription_provider_id", "text_provider_id")
                        if key in row.keys() and row[key] is not None}
        credential_records = []
        for provider_id in provider_ids:
            provider = connection.execute(
                "SELECT adapter_type, encrypted_credentials FROM provider_connections WHERE id=?",
                (provider_id,),
            ).fetchone()
            service = _service(provider["adapter_type"]) if provider else None
            encrypted = provider["encrypted_credentials"] if provider else None
            if service and encrypted:
                credential_records.append((service, encrypted))
        credentials, conflicts = _collect_credentials(credential_records, secret_store)
        warnings = ["The saved profile needs V2 reconfiguration."]
        if conflicts:
            warnings.append("Conflicting service credentials were not imported.")
        return None, credentials, None, True, warnings
    stages = connection.execute(
        "SELECT s.stage_type, s.primary_model_id, m.model_id, m.provider_id, p.adapter_type, p.encrypted_credentials "
        "FROM pipeline_stages s LEFT JOIN provider_models m ON m.id=s.primary_model_id "
        "LEFT JOIN provider_connections p ON p.id=m.provider_id WHERE s.profile_id=? ORDER BY s.id",
        (active,),
    ).fetchall()
    fallbacks = connection.execute(
        "SELECT COUNT(*) FROM pipeline_stage_fallbacks f JOIN pipeline_stages s ON s.id=f.stage_id WHERE s.profile_id=?",
        (active,),
    ).fetchone()[0]
    credential_records = []
    for stage in stages:
        service = _service(stage["adapter_type"])
        encrypted = stage["encrypted_credentials"]
        if service and encrypted:
            credential_records.append((service, encrypted))
    credentials, conflicts = _collect_credentials(credential_records, secret_store)
    compatible_shape = (
        row["mode"] == "two_stage" and not row["fallback_policy"] and not fallbacks
        and len(stages) == 2
        and {stage["stage_type"] for stage in stages} == {"transcription", "refinement"}
        and all(stage["model_id"] for stage in stages)
    )
    if not compatible_shape:
        warnings = ["The saved profile needs V2 reconfiguration."]
        if conflicts:
            warnings.append("Conflicting service credentials were not imported.")
        return None, credentials, None, True, warnings
    transcription = next(stage for stage in stages if stage["stage_type"] == "transcription")
    refinement = next(stage for stage in stages if stage["stage_type"] == "refinement")
    service_a = _service(transcription["adapter_type"])
    service_b = _service(refinement["adapter_type"])
    if not service_a or service_a != service_b or service_a in conflicts:
        warnings = ["The saved profile needs V2 reconfiguration."]
        if service_a in conflicts or service_b in conflicts:
            warnings.append("Conflicting service credentials were not imported.")
        return None, credentials, None, True, warnings
    if not row["refine_template"] or "{raw_text}" not in row["refine_template"]:
        return None, credentials, None, True, ["The saved profile needs V2 reconfiguration."]
    encrypted = transcription["encrypted_credentials"] or refinement["encrypted_credentials"]
    if not encrypted:
        return None, credentials, None, True, ["The saved profile needs V2 reconfiguration."]
    credential = _decrypt(secret_store, encrypted)
    config = {
        "service": service_a,
        "mode": "two_stage",
        "audio_model": transcription["model_id"],
        "text_model": refinement["model_id"],
        "system_prompt": row["system_prompt"] or "",
        "refine_template": row["refine_template"],
    }
    return None, [(service_a, credential)], {"values": config}, False, []


def _service(adapter: str | None) -> str | None:
    normalized = (adapter or "").lower()
    aliases = {"openai": "openai", "gemini": "gemini", "gemini-native": "gemini",
               "openrouter": "openrouter", "openai-compatible": "openrouter"}
    return aliases.get(normalized)


def _decrypt(secret_store: SecretStore, ciphertext: str) -> str:
    try:
        return secret_store.decrypt(ciphertext)
    except SecretStoreError as exc:
        raise ValueError("V1 key copy cannot decrypt the required credentials") from exc


def _collect_credentials(records: list[tuple[str, str]], secret_store: SecretStore) -> tuple[list[tuple[str, str]], set[str]]:
    credentials: dict[str, str] = {}
    conflicts: set[str] = set()
    for service, ciphertext in records:
        value = _decrypt(secret_store, ciphertext)
        if service in credentials and not hmac.compare_digest(credentials[service], value):
            conflicts.add(service)
        else:
            credentials[service] = value
    return [(service, value) for service, value in credentials.items() if service not in conflicts], conflicts


def _limits(settings: dict) -> dict:
    defaults = RuntimeLimits()
    mapping = {
        "rate_limit_max_per_user": "max_per_user",
        "rate_limit_cooldown": "cooldown_seconds",
        "rate_limit_max_concurrent_global": "max_concurrent_global",
        "rate_limit_max_file_size_mb": "max_file_size_mb",
        "rate_limit_queue_enabled": "queue_enabled",
        "rate_limit_max_queue_size": "max_queue_size",
        "rate_limit_max_queued_per_user": "max_queued_per_user",
    }
    result = vars(defaults).copy()
    bounds = {
        "max_per_user": (1, 10), "cooldown_seconds": (0, 3600),
        "max_concurrent_global": (1, 30), "max_file_size_mb": (1, 20),
        "max_queue_size": (0, 100), "max_queued_per_user": (1, 10),
    }
    for setting, attribute in mapping.items():
        value = settings.get(setting)
        if value is None:
            continue
        try:
            if attribute == "queue_enabled":
                normalized = value.lower()
                if normalized not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                    continue
                parsed = normalized in {"1", "true", "yes", "on"}
            else:
                parsed = int(value)
                minimum, maximum = bounds[attribute]
                if not minimum <= parsed <= maximum:
                    continue
        except (ValueError, AttributeError):
            continue
        result[attribute] = parsed
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import a copied V1 SQLite database into an empty V2 database")
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--source-key", required=True, type=Path)
    parser.add_argument("--target-db", required=True, type=Path)
    parser.add_argument("--target-key", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = migrate_v1(args.source_db, args.source_key, args.target_db, args.target_key)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"imported": report.imported,
                      "needs_reconfiguration": report.needs_reconfiguration,
                      "warnings": report.warnings}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

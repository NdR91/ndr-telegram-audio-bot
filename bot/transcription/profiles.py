"""Transactional catalog and role persistence for V2 transcription profiles."""

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal

from bot.database.v2_repository import V2Repository
from bot.transcription.configuration import VALIDATOR_VERSION, _validate_config
from bot.transcription.errors import ConfigurationInvalid, RevisionConflict
from bot.transcription.errors import TestRequired
from bot.transcription.models import (
    ConfigInput,
    ConfigRevision,
    CredentialOwner,
    RuntimeLimits,
    TestReceipt,
)


ProfileRole = Literal["primary", "fallback"]


@dataclass(frozen=True)
class ProfileRevision:
    profile_id: int
    revision: int
    name: str
    values: ConfigInput
    created_at: str


@dataclass(frozen=True)
class ProfileDraft:
    profile_id: int
    revision: int | None
    generation: int


@dataclass(frozen=True)
class ProfileRoleSnapshot:
    role: ProfileRole
    profile_id: int
    name: str
    configuration: ConfigRevision
    credential_service: CredentialOwner
    credential_version: int


@dataclass(frozen=True)
class ProcessingProfileSnapshot:
    primary: ProfileRoleSnapshot
    fallback: ProfileRoleSnapshot | None


@dataclass(frozen=True)
class ProfileCatalogEntry:
    profile_id: int
    name: str
    generation: int
    current_revision: int | None
    role: ProfileRole | None
    role_revision: int | None
    verification_state: Literal["verified", "not_verified", "retest"]
    last_test_success: bool | None
    last_tested_at: str | None
    role_verification_state: Literal["verified", "not_verified", "retest"] | None
    role_last_test_success: bool | None
    role_last_tested_at: str | None


class ProfileService:
    """Persist immutable profile revisions and atomically assigned roles."""

    def __init__(self, repo: V2Repository):
        self._repo = repo

    def create(self, name: str, values: ConfigInput) -> ProfileRevision:
        name = _validate_name(name)
        _validate_config(values)
        try:
            with self._repo.transaction() as connection:
                _ensure_name_available(connection, name)
                _require_credential(connection, values.service, values.credential_version)
                cursor = connection.execute(
                    "INSERT INTO profiles(name, generation) VALUES (?, 1)", (name,)
                )
                profile_id = int(cursor.lastrowid)
                revision = _next_revision(connection)
                connection.execute(
                    "INSERT INTO profile_revisions(profile_id, revision, service, mode, "
                    "audio_model, text_model, credential_version, system_prompt, "
                    "refine_template, limits_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _revision_values(profile_id, revision, values),
                )
                connection.execute(
                    "INSERT INTO profile_drafts(profile_id, revision, generation) "
                    "VALUES (?, ?, 1)",
                    (profile_id, revision),
                )
                _advance_revision(connection, revision)
        except sqlite3.IntegrityError as exc:
            if "profiles.name" in str(exc).lower() or "unique constraint failed: profiles.name" in str(exc).lower():
                raise ConfigurationInvalid("A profile with this name already exists") from exc
            raise
        return self.get_revision(profile_id, revision)

    def save_revision(
        self,
        profile_id: int,
        expected_revision: int | None,
        *,
        expected_generation: int,
        name: str,
        values: ConfigInput,
    ) -> ProfileRevision:
        name = _validate_name(name)
        _validate_config(values)
        try:
            with self._repo.transaction() as connection:
                profile = connection.execute(
                    "SELECT generation FROM profiles WHERE profile_id=?", (profile_id,)
                ).fetchone()
                draft = connection.execute(
                    "SELECT revision, generation FROM profile_drafts WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()
                if profile is None or draft is None:
                    raise ConfigurationInvalid("Profile not found")
                current_revision = int(draft["revision"]) if draft["revision"] is not None else None
                if current_revision != expected_revision or int(profile["generation"]) != expected_generation:
                    raise RevisionConflict("Profile changed; reload its latest revision")
                _ensure_name_available(connection, name, exclude_profile_id=profile_id)
                _require_credential(connection, values.service, values.credential_version)
                connection.execute(
                    "UPDATE profiles SET name=?, generation=generation+1, "
                    "updated_at=datetime('now') WHERE profile_id=?",
                    (name, profile_id),
                )
                revision = _next_revision(connection)
                connection.execute(
                    "INSERT INTO profile_revisions(profile_id, revision, service, mode, "
                    "audio_model, text_model, credential_version, system_prompt, "
                    "refine_template, limits_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _revision_values(profile_id, revision, values),
                )
                connection.execute(
                    "UPDATE profile_drafts SET revision=?, generation=generation+1 "
                    "WHERE profile_id=?",
                    (revision, profile_id),
                )
                _advance_revision(connection, revision)
        except sqlite3.IntegrityError as exc:
            if "unique constraint failed: profiles.name" in str(exc).lower():
                raise ConfigurationInvalid("A profile with this name already exists") from exc
            raise
        return self.get_revision(profile_id, revision)

    def get_revision(self, profile_id: int, revision: int) -> ProfileRevision:
        with self._repo.read() as connection:
            row = connection.execute(
                "SELECT p.name, r.* FROM profile_revisions r "
                "JOIN profiles p ON p.profile_id=r.profile_id "
                "WHERE r.profile_id=? AND r.revision=?",
                (profile_id, revision),
            ).fetchone()
        if row is None:
            raise ConfigurationInvalid("Profile revision not found")
        return _profile_revision(row)

    def get_draft(self, profile_id: int) -> ProfileDraft:
        with self._repo.read() as connection:
            row = connection.execute(
                "SELECT profile_id, revision, generation FROM profile_drafts "
                "WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise ConfigurationInvalid("Profile not found")
        return ProfileDraft(
            int(row["profile_id"]),
            int(row["revision"]) if row["revision"] is not None else None,
            int(row["generation"]),
        )

    def list_profiles(self) -> tuple[ProfileCatalogEntry, ...]:
        with self._repo.read() as connection:
            profiles = connection.execute(
                "SELECT p.profile_id, p.name, p.generation, d.revision AS current_revision, "
                "pr.role, pr.revision AS role_revision "
                "FROM profiles p LEFT JOIN profile_drafts d ON d.profile_id=p.profile_id "
                "LEFT JOIN profile_roles pr ON pr.profile_id=p.profile_id "
                "ORDER BY p.name COLLATE NOCASE, p.profile_id"
            ).fetchall()
            entries = []
            for profile in profiles:
                revision = profile["current_revision"]
                tested_revision = revision if revision is not None else profile["role_revision"]
                state, latest_test = _revision_verification(
                    connection, int(profile["profile_id"]), tested_revision
                )
                role_state, role_test = _revision_verification(
                    connection, int(profile["profile_id"]), profile["role_revision"]
                )
                entries.append(
                    ProfileCatalogEntry(
                        profile_id=int(profile["profile_id"]),
                        name=profile["name"],
                        generation=int(profile["generation"]),
                        current_revision=int(revision) if revision is not None else None,
                        role=profile["role"],
                        role_revision=(int(profile["role_revision"])
                                       if profile["role_revision"] is not None else None),
                        verification_state=state,
                        last_test_success=(bool(latest_test["success"])
                                           if latest_test is not None else None),
                        last_tested_at=latest_test["finished_at"] if latest_test else None,
                        role_verification_state=role_state,
                        role_last_test_success=(bool(role_test["success"])
                                                if role_test is not None else None),
                        role_last_tested_at=role_test["finished_at"] if role_test else None,
                    )
                )
        return tuple(entries)

    def roles_generation(self) -> int:
        with self._repo.read() as connection:
            row = connection.execute(
                "SELECT generation FROM profile_roles_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise ConfigurationInvalid("Profile role generation is unavailable")
        return int(row[0])

    def duplicate(
        self, profile_id: int, new_name: str, *, expected_generation: int
    ) -> ProfileRevision:
        new_name = _validate_name(new_name)
        try:
            with self._repo.transaction() as connection:
                _ensure_name_available(connection, new_name)
                generation = connection.execute(
                    "SELECT generation FROM profiles WHERE profile_id=?", (profile_id,)
                ).fetchone()
                if generation is None:
                    raise ConfigurationInvalid("Profile not found")
                if int(generation[0]) != expected_generation:
                    raise RevisionConflict("Profile changed; reload its latest revision")
                source = connection.execute(
                    "SELECT p.name, r.* FROM profiles p JOIN profile_drafts d "
                    "ON d.profile_id=p.profile_id JOIN profile_revisions r "
                    "ON r.profile_id=p.profile_id AND r.revision=d.revision "
                    "WHERE p.profile_id=?",
                    (profile_id,),
                ).fetchone()
                if source is None:
                    source = connection.execute(
                        "SELECT p.name, r.* FROM profiles p JOIN profile_revisions r "
                        "ON r.profile_id=p.profile_id WHERE p.profile_id=? "
                        "ORDER BY r.revision DESC LIMIT 1",
                        (profile_id,),
                    ).fetchone()
                if source is None:
                    raise ConfigurationInvalid("Profile has no saved configuration to duplicate")
                values = _profile_revision(source).values
                cursor = connection.execute(
                    "INSERT INTO profiles(name, generation) VALUES (?, 1)", (new_name,)
                )
                duplicate_id = int(cursor.lastrowid)
                revision = _next_revision(connection)
                connection.execute(
                    "INSERT INTO profile_revisions(profile_id, revision, service, mode, "
                    "audio_model, text_model, credential_version, system_prompt, "
                    "refine_template, limits_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _revision_values(duplicate_id, revision, values),
                )
                connection.execute(
                    "INSERT INTO profile_drafts(profile_id, revision, generation) VALUES (?, ?, 1)",
                    (duplicate_id, revision),
                )
                _advance_revision(connection, revision)
        except sqlite3.IntegrityError as exc:
            if "unique constraint failed: profiles.name" in str(exc).lower():
                raise ConfigurationInvalid("A profile with this name already exists") from exc
            raise
        return self.get_revision(duplicate_id, revision)

    def current_primary(self) -> ProfileRoleSnapshot | None:
        """Read the active primary role from profile storage, never legacy config refs."""
        with self._repo.lease_processing_profile_rows(VALIDATOR_VERSION) as rows:
            if rows is None:
                return None
            primary = next((row for row in rows if row["role"] == "primary"), None)
            return _role_snapshot(primary, "primary") if primary is not None else None

    def record_test(self, profile_id: int, receipt: TestReceipt) -> None:
        with self._repo.transaction() as connection:
            row = connection.execute(
                "SELECT service, credential_version FROM profile_revisions "
                "WHERE profile_id=? AND revision=?",
                (profile_id, receipt.revision),
            ).fetchone()
            if row is None:
                return
            if int(row["credential_version"]) != receipt.credential_version:
                raise ConfigurationInvalid("Test receipt does not match this profile revision")
            connection.execute(
                "INSERT INTO profile_test_receipts(receipt_id, profile_id, revision, "
                "credential_service, credential_version, validator_version, success, "
                "models_json, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.id,
                    profile_id,
                    receipt.revision,
                    row["service"],
                    receipt.credential_version,
                    receipt.validator_version,
                    int(receipt.success),
                    json.dumps(receipt.models),
                    receipt.finished_at.isoformat(),
                ),
            )

    def has_valid_receipt(
        self,
        profile_id: int,
        revision: int,
        credential_version: int,
        validator_version: str = VALIDATOR_VERSION,
    ) -> bool:
        with self._repo.read() as connection:
            return _has_valid_receipt(
                connection, profile_id, revision, credential_version, validator_version
            )

    def assign_role(
        self,
        role: ProfileRole,
        profile_id: int | None,
        revision: int | None,
        *,
        expected_generation: int,
        require_valid_receipt: bool = False,
    ) -> None:
        if role not in ("primary", "fallback"):
            raise ConfigurationInvalid("Profile role is invalid")
        if (profile_id is None) != (revision is None):
            raise ConfigurationInvalid("Profile and revision must be assigned together")
        with self._repo.transaction() as connection:
            generation_row = connection.execute(
                "SELECT generation FROM profile_roles_state WHERE singleton=1"
            ).fetchone()
            if generation_row is None or int(generation_row[0]) != expected_generation:
                raise RevisionConflict("Profile roles changed; reload the catalog")

            current = connection.execute(
                "SELECT profile_id, revision FROM profile_roles WHERE role=?", (role,)
            ).fetchone()
            if profile_id is not None:
                revision_row = connection.execute(
                    "SELECT service, credential_version FROM profile_revisions "
                    "WHERE profile_id=? AND revision=?",
                    (profile_id, revision),
                ).fetchone()
                if revision_row is None:
                    raise ConfigurationInvalid("Profile revision not found")
                draft = connection.execute(
                    "SELECT revision FROM profile_drafts WHERE profile_id=?", (profile_id,)
                ).fetchone()
                if draft is None or draft["revision"] is None or int(draft["revision"]) != revision:
                    raise RevisionConflict("Profile revision changed; reload the catalog")
                if require_valid_receipt and not _has_valid_receipt(
                    connection, profile_id, revision,
                    int(revision_row["credential_version"]), VALIDATOR_VERSION,
                ):
                    raise TestRequired("A successful current profile test is required")
                other_role = "fallback" if role == "primary" else "primary"
                assigned = connection.execute(
                    "SELECT 1 FROM profile_roles WHERE role=? AND profile_id=?",
                    (other_role, profile_id),
                ).fetchone()
                if assigned is not None:
                    raise ConfigurationInvalid("A profile cannot hold both roles")
                if role == "fallback" and not _has_valid_receipt(
                    connection,
                    profile_id,
                    revision,
                    int(revision_row["credential_version"]),
                    VALIDATOR_VERSION,
                ):
                    raise ConfigurationInvalid("Fallback profile requires a successful current test")

            next_generation = expected_generation + 1
            if role == "primary" and current is not None and (
                profile_id is None
                or int(current["profile_id"]) != profile_id
                or int(current["revision"]) != revision
            ):
                connection.execute("DELETE FROM profile_role_history WHERE singleton=1")
                connection.execute(
                    "INSERT INTO profile_role_history(singleton, profile_id, revision) "
                    "VALUES (1, ?, ?)",
                    (current["profile_id"], current["revision"]),
                )

            connection.execute("DELETE FROM profile_roles WHERE role=?", (role,))
            if profile_id is not None:
                connection.execute(
                    "INSERT INTO profile_roles(role, profile_id, revision, generation) "
                    "VALUES (?, ?, ?, ?)",
                    (role, profile_id, revision, next_generation),
                )
            connection.execute(
                "UPDATE profile_role_versions SET generation=? WHERE role=?",
                (next_generation, role),
            )
            connection.execute(
                "UPDATE profile_roles_state SET generation=generation+1 WHERE singleton=1"
            )

    def restore_previous_primary(self, *, expected_generation: int) -> ProfileRoleSnapshot | None:
        with self._repo.transaction() as connection:
            generation_row = connection.execute(
                "SELECT generation FROM profile_roles_state WHERE singleton=1"
            ).fetchone()
            if generation_row is None or int(generation_row[0]) != expected_generation:
                raise RevisionConflict("Profile roles changed; reload the catalog")
            previous = connection.execute(
                "SELECT h.profile_id, h.revision, p.name, r.service, r.mode, r.audio_model, "
                "r.text_model, r.credential_version, r.system_prompt, r.refine_template, "
                "r.limits_json, r.created_at FROM profile_role_history h "
                "JOIN profiles p ON p.profile_id=h.profile_id "
                "JOIN profile_revisions r ON r.profile_id=h.profile_id AND r.revision=h.revision "
                "WHERE h.singleton=1"
            ).fetchone()
            if previous is None:
                return None
            conflict = connection.execute(
                "SELECT 1 FROM profile_roles WHERE role='fallback' AND profile_id=?",
                (previous["profile_id"],),
            ).fetchone()
            if conflict:
                raise ConfigurationInvalid("Previous primary currently has the fallback role")
            current = connection.execute(
                "SELECT profile_id, revision FROM profile_roles WHERE role='primary'"
            ).fetchone()
            connection.execute("DELETE FROM profile_roles WHERE role='primary'")
            connection.execute("DELETE FROM profile_role_history WHERE singleton=1")
            next_generation = expected_generation + 1
            connection.execute(
                "INSERT INTO profile_roles(role, profile_id, revision, generation) "
                "VALUES ('primary', ?, ?, ?)",
                (previous["profile_id"], previous["revision"], next_generation),
            )
            connection.execute(
                "UPDATE profile_role_versions SET generation=? WHERE role='primary'",
                (next_generation,),
            )
            connection.execute(
                "UPDATE profile_roles_state SET generation=generation+1 WHERE singleton=1"
            )
            if current is not None:
                connection.execute(
                    "INSERT INTO profile_role_history(singleton, profile_id, revision) VALUES (1, ?, ?)",
                    (current["profile_id"], current["revision"]),
                )
        return _role_snapshot(previous, "primary")

    @contextmanager
    def lease_processing_snapshot(self) -> Iterator[ProcessingProfileSnapshot | None]:
        with self._repo.lease_processing_profile_rows(VALIDATOR_VERSION) as rows:
            if rows is None:
                yield None
                return
            roles = {row["role"]: _role_snapshot(row, row["role"]) for row in rows}
            yield ProcessingProfileSnapshot(roles["primary"], roles.get("fallback"))

    def delete(self, profile_id: int, *, expected_generation: int) -> None:
        with self._repo.transaction() as connection:
            profile = connection.execute(
                "SELECT generation FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if profile is None:
                raise ConfigurationInvalid("Profile not found")
            if int(profile[0]) != expected_generation:
                raise RevisionConflict("Profile changed; reload its latest revision")
            if connection.execute(
                "SELECT 1 FROM profile_roles WHERE profile_id=?", (profile_id,)
            ).fetchone():
                raise ConfigurationInvalid("Remove the profile role before deleting it")
            if any(key[0] == profile_id for key in self._repo._profile_leases):
                raise ConfigurationInvalid("Profile is in use by a request")
            connection.execute(
                "DELETE FROM profile_role_history WHERE singleton=1 AND profile_id=?",
                (profile_id,),
            )
            connection.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))


def _revision_verification(connection, profile_id: int, revision: int | None):
    if revision is None:
        return None, None
    receipt = connection.execute(
        "SELECT success, credential_service, credential_version, validator_version, finished_at "
        "FROM profile_test_receipts WHERE profile_id=? AND revision=? "
        "ORDER BY recorded_order DESC LIMIT 1",
        (profile_id, revision),
    ).fetchone()
    values = connection.execute(
        "SELECT service, credential_version FROM profile_revisions "
        "WHERE profile_id=? AND revision=?", (profile_id, revision),
    ).fetchone()
    has_success = connection.execute(
        "SELECT 1 FROM profile_test_receipts WHERE profile_id=? AND revision=? AND success=1 LIMIT 1",
        (profile_id, revision),
    ).fetchone() is not None
    verified = bool(
        receipt and receipt["success"]
        and receipt["validator_version"] == VALIDATOR_VERSION
        and values is not None
        and int(receipt["credential_version"]) == int(values["credential_version"])
        and receipt["credential_service"] == values["service"]
    )
    state = "verified" if verified else "retest" if has_success else "not_verified"
    return state, receipt


def _profile_revision(row: sqlite3.Row) -> ProfileRevision:
    values = ConfigInput(
        service=row["service"],
        mode=row["mode"],
        audio_model=row["audio_model"],
        text_model=row["text_model"],
        credential_version=int(row["credential_version"]),
        system_prompt=row["system_prompt"],
        refine_template=row["refine_template"],
        limits=RuntimeLimits(**json.loads(row["limits_json"])),
    )
    return ProfileRevision(
        profile_id=int(row["profile_id"]),
        revision=int(row["revision"]),
        name=row["name"],
        values=values,
        created_at=row["created_at"],
    )


def _role_snapshot(row: sqlite3.Row, role: ProfileRole) -> ProfileRoleSnapshot:
    profile_revision = _profile_revision(row)
    return ProfileRoleSnapshot(
        role=role,
        profile_id=profile_revision.profile_id,
        name=profile_revision.name,
        configuration=ConfigRevision(profile_revision.revision, profile_revision.values),
        credential_service=profile_revision.values.service,
        credential_version=profile_revision.values.credential_version,
    )


def _revision_values(profile_id: int, revision: int, values: ConfigInput) -> tuple:
    return (
        profile_id,
        revision,
        values.service,
        values.mode,
        values.audio_model,
        values.text_model,
        values.credential_version,
        values.system_prompt,
        values.refine_template,
        json.dumps(values.limits.__dict__, sort_keys=True),
    )


def _next_revision(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT next_revision FROM revision_sequence WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise ConfigurationInvalid("Profile revision sequence is unavailable")
    return int(row[0])


def _advance_revision(connection: sqlite3.Connection, revision: int) -> None:
    connection.execute(
        "UPDATE revision_sequence SET next_revision=? WHERE singleton=1",
        (revision + 1,),
    )


def _require_credential(connection: sqlite3.Connection, service: str, version: int) -> None:
    if connection.execute(
        "SELECT 1 FROM credentials WHERE service=? AND version=?", (service, version)
    ).fetchone() is None:
        raise ConfigurationInvalid("Selected credential version is unavailable")


def _has_valid_receipt(
    connection: sqlite3.Connection,
    profile_id: int,
    revision: int,
    credential_version: int,
    validator_version: str,
) -> bool:
    row = connection.execute(
        "SELECT t.success, t.credential_service, t.credential_version, t.validator_version, "
        "r.service, r.credential_version AS revision_credential_version "
        "FROM profile_test_receipts t JOIN profile_revisions r "
        "ON r.profile_id=t.profile_id AND r.revision=t.revision "
        "WHERE t.profile_id=? AND t.revision=? "
        "ORDER BY recorded_order DESC LIMIT 1",
        (profile_id, revision),
    ).fetchone()
    return bool(
        row
        and row["success"]
        and int(row["credential_version"]) == credential_version
        and int(row["revision_credential_version"]) == credential_version
        and row["credential_service"] == row["service"]
        and row["validator_version"] == validator_version
    )


def _validate_name(name: str) -> str:
    if not isinstance(name, str):
        raise ConfigurationInvalid("Profile name is required")
    normalized = name.strip()
    if not normalized or len(normalized) > 80:
        raise ConfigurationInvalid("Profile name must contain 1 to 80 characters")
    return normalized


def _ensure_name_available(
    connection: sqlite3.Connection,
    name: str,
    *,
    exclude_profile_id: int | None = None,
) -> None:
    candidate = _name_key(name)
    for row in connection.execute("SELECT profile_id, name FROM profiles"):
        if exclude_profile_id is not None and int(row["profile_id"]) == exclude_profile_id:
            continue
        if _name_key(row["name"]) == candidate:
            raise ConfigurationInvalid("A profile with this name already exists")


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()

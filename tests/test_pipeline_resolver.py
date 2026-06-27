"""
Tests for the automatic pipeline resolver (P4).

Covers:
- :class:`PipelineResolver` — single provider, separate providers,
  resolution failures, mode toggles, capability overrides.
- :class:`ExecutionPlan` — immutability, fields.
- :class:`PipelineRequest` / :class:`RequestMode` — construction.
"""

from __future__ import annotations

import pytest

from bot.database import DatabaseManager
from bot.exceptions import PipelineResolutionError
from bot.pipeline_resolver import (
    ExecutionPlan,
    PipelineRequest,
    PipelineResolver,
    RequestMode,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path, with_secret_store: bool = False) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_db_with_secret(tmp_path) -> tuple[DatabaseManager, "SecretStore"]:
    """Create a DatabaseManager with a SecretStore so credentials are
    persisted."""
    from bot.database.secret_store import SecretStore

    key_path = str(tmp_path / ".master_key")
    store = SecretStore(key_path)
    store.initialize()
    db = DatabaseManager(str(tmp_path / "app.sqlite3"), secret_store=store)
    db.initialize()
    return db, store


def _add_provider(
    db: DatabaseManager,
    name: str = "Test Provider",
    adapter: str = "openai-native",
    credentials: str = "sk-test",
    capabilities: dict | None = None,
    enabled: bool = True,
) -> int:
    """Add a provider connection and return its ID."""
    return db.add_provider(
        name=name,
        adapter_type=adapter,
        credentials=credentials,
        capabilities=capabilities,
        enabled=enabled,
    )


# ------------------------------------------------------------------
# RequestMode
# ------------------------------------------------------------------


class TestRequestMode:
    def test_full_value(self):
        assert RequestMode.FULL.value == "full"

    def test_transcription_only_value(self):
        assert RequestMode.TRANSCRIPTION_ONLY.value == "transcription_only"


# ------------------------------------------------------------------
# PipelineRequest
# ------------------------------------------------------------------


class TestPipelineRequest:
    def test_default_mode_is_full(self):
        req = PipelineRequest()
        assert req.mode == RequestMode.FULL
        assert req.user_id is None
        assert req.chat_id is None

    def test_with_user_and_chat(self):
        req = PipelineRequest(
            mode=RequestMode.TRANSCRIPTION_ONLY,
            user_id=12345,
            chat_id=67890,
        )
        assert req.mode == RequestMode.TRANSCRIPTION_ONLY
        assert req.user_id == 12345
        assert req.chat_id == 67890

    def test_is_frozen(self):
        req = PipelineRequest()
        with pytest.raises(AttributeError):
            req.mode = RequestMode.TRANSCRIPTION_ONLY  # type: ignore[misc]


# ------------------------------------------------------------------
# ExecutionPlan
# ------------------------------------------------------------------


class TestExecutionPlan:
    def test_minimal_construction(self):
        plan = ExecutionPlan(
            transcriber=object(),  # stub
            text_processor=None,
            provider_name="Test",
            model_name="test-model",
        )
        assert plan.provider_name == "Test"
        assert plan.model_name == "test-model"
        assert plan.text_processor is None
        assert plan.resolution_log == []

    def test_with_log(self):
        plan = ExecutionPlan(
            transcriber=object(),
            text_processor=object(),
            provider_name="X",
            model_name="m1",
            resolution_log=["Step 1", "Step 2"],
        )
        assert len(plan.resolution_log) == 2
        assert plan.resolution_log[0] == "Step 1"

    def test_is_frozen(self):
        plan = ExecutionPlan(
            transcriber=object(),
            text_processor=None,
            provider_name="X",
            model_name="m1",
        )
        with pytest.raises(AttributeError):
            plan.provider_name = "Y"  # type: ignore[misc]


# ------------------------------------------------------------------
# PipelineResolver — error cases
# ------------------------------------------------------------------


class TestResolverErrors:
    def test_no_providers_raises(self, tmp_path):
        db = _make_db(tmp_path)
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()

        assert "Nessun provider" in str(exc.value.user_message)

    def test_all_disabled_providers_raises(self, tmp_path):
        db = _make_db(tmp_path)
        _add_provider(db, enabled=False)
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()

        assert "Nessun provider" in str(exc.value.user_message)

    def test_no_transcription_capability_raises(self, tmp_path):
        """Provider exists but only supports refinement (no transcription)."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Refine Only",
            adapter="openai-native",
            capabilities={"refinement": True, "transcription": False},
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()

        assert "trascrizione" in str(exc.value.user_message).lower()

    def test_no_refinement_when_needed_raises(self, tmp_path):
        """Provider supports transcription but not refinement, and
        refinement is needed."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Transcribe Only",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()  # mode=FULL by default → needs refinement

        assert "refinement" in str(exc.value.user_message).lower()

    def test_invalid_adapter_type_raises(self, tmp_path):
        """Provider with an unknown adapter type should raise."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Unknown",
            adapter="nonexistent-adapter",
            capabilities={"transcription": True},
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError):
            resolver.resolve()


# ------------------------------------------------------------------
# PipelineResolver — successful resolution
# ------------------------------------------------------------------


class TestResolverSuccess:
    def test_single_provider_with_all_capabilities(self, tmp_path):
        """A single provider with transcription + refinement is used for
        both stages."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Super AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert plan.provider_name == "Super AI"
        assert plan.text_processor is not None
        # The transcriber should be a real instance
        assert hasattr(plan.transcriber, "transcribe")

    def test_transcription_only_mode_skips_refinement(self, tmp_path):
        """When mode is TRANSCRIPTION_ONLY, no text processor is created
        even if the provider supports refinement."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Super AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve(
            PipelineRequest(mode=RequestMode.TRANSCRIPTION_ONLY)
        )

        assert plan.provider_name == "Super AI"
        assert plan.text_processor is None

    def test_refinement_globally_disabled(self, tmp_path):
        """When refinement is globally disabled, no text processor is
        created."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Super AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve(refinement_globally_disabled=True)

        assert plan.text_processor is None

    def test_separate_providers_when_single_cannot_do_both(self, tmp_path):
        """When one provider can only transcribe and another can only
        refine, the resolver uses both."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="TX Only",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        _add_provider(
            db,
            name="REF Only",
            adapter="openai-native",
            capabilities={"transcription": False, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        # Display name should combine both
        assert "TX Only" in plan.provider_name
        assert "REF Only" in plan.provider_name
        assert plan.text_processor is not None

    def test_prefers_single_over_separate(self, tmp_path):
        """When one provider can do everything AND separate providers
        exist, the single provider is preferred."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="All-in-One",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        _add_provider(
            db,
            name="TX Backup",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert plan.provider_name == "All-in-One"
        assert plan.text_processor is not None

    def test_resolution_log_populated(self, tmp_path):
        """The resolution log should contain human-readable steps."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Test AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert len(plan.resolution_log) >= 2
        assert any("enabled provider" in msg for msg in plan.resolution_log)
        assert any("Selected" in msg for msg in plan.resolution_log)

    def test_gemini_provider_resolves(self, tmp_path):
        """Gemini provider should resolve correctly."""
        db, _ = _make_db_with_secret(tmp_path)
        _add_provider(
            db,
            name="Gemini AI",
            adapter="gemini-native",
            credentials="test-api-key-12345",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert plan.provider_name == "Gemini AI"
        assert plan.text_processor is not None
        assert hasattr(plan.transcriber, "transcribe")


# ------------------------------------------------------------------
# PipelineResolver — edge cases
# ------------------------------------------------------------------


class TestResolverEdgeCases:
    def test_first_enabled_provider_selected(self, tmp_path):
        """When multiple providers can do everything, the first enabled
        one is selected."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="First AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        _add_provider(
            db,
            name="Second AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert plan.provider_name == "First AI"

    def test_disabled_provider_not_considered(self, tmp_path):
        """Disabled providers are skipped during resolution."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="Disabled AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
            enabled=False,
        )
        _add_provider(
            db,
            name="Enabled AI",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
            enabled=True,
        )
        resolver = PipelineResolver(db)

        plan = resolver.resolve()

        assert plan.provider_name == "Enabled AI"

    def test_multiple_tx_providers_finds_refinement(self, tmp_path):
        """Only transcription providers available, but refinement needed:
        should raise."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="TX1",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        _add_provider(
            db,
            name="TX2",
            adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()

        assert "refinement" in str(exc.value.user_message).lower()

    def test_capability_override_disables_transcription(self, tmp_path):
        """User overrides can disable transcription even for an adapter
        that normally supports it."""
        db = _make_db(tmp_path)
        _add_provider(
            db,
            name="OpenAI No TX",
            adapter="openai-native",
            capabilities={"transcription": False, "refinement": True},
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve()

        assert "trascrizione" in str(exc.value.user_message).lower()


# ------------------------------------------------------------------
# _adapters_support helper
# ------------------------------------------------------------------


class TestAdaptersSupport:
    def test_openai_supports_both(self):
        from bot.pipeline_resolver import _adapters_support

        assert _adapters_support("openai", True, True)
        assert _adapters_support("openai", True, False)
        assert _adapters_support("openai", False, True)
        assert not _adapters_support("openai", True, True) is False

    def test_unknown_adapter_supports_nothing(self):
        from bot.pipeline_resolver import _adapters_support

        assert not _adapters_support("nonexistent", True, False)
        assert not _adapters_support("nonexistent", False, True)


# ------------------------------------------------------------------
# P5 — resolve_from_profile
# ------------------------------------------------------------------


class TestResolveFromProfile:
    """Tests for profile-based pipeline resolution (P5)."""

    def _create_profile_with_provider(
        self, db: DatabaseManager, adapter: str = "openai-native",
        caps: dict | None = None, provider_name: str = "Test Provider",
        profile_name: str = "Test Profile",
    ) -> int:
        """Create a provider and a same-provider pipeline profile.
        Returns the profile ID."""
        if caps is None:
            caps = {"transcription": True, "refinement": True}
        pid = _add_provider(db, name=provider_name, adapter=adapter,
                            capabilities=caps)
        return db.add_pipeline_profile(
            name=profile_name,
            transcription_provider_id=pid,
            text_provider_id=pid,
        )

    def _create_profile_with_separate_providers(
        self, db: DatabaseManager,
        tx_adapter: str = "openai-native",
        ref_adapter: str = "openai-native",
    ) -> int:
        """Create two providers and a separate-provider pipeline profile.
        Returns the profile ID."""
        tx_id = _add_provider(
            db, name="TX Provider", adapter=tx_adapter,
            capabilities={"transcription": True, "refinement": False},
        )
        ref_id = _add_provider(
            db, name="REF Provider", adapter=ref_adapter,
            capabilities={"transcription": False, "refinement": True},
        )
        return db.add_pipeline_profile(
            name="Separate Profile",
            transcription_provider_id=tx_id,
            text_provider_id=ref_id,
        )

    # ---------- Success cases ----------

    def test_same_provider_default_resolves(self, tmp_path):
        """Same-provider profile: single provider used for both stages."""
        db = _make_db(tmp_path)
        profile_id = self._create_profile_with_provider(
            db, provider_name="Super AI",
            caps={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.provider_name == "Super AI"
        assert plan.text_processor is not None
        assert hasattr(plan.transcriber, "transcribe")
        assert any("same-provider" in msg.lower()
                   for msg in plan.resolution_log)

    def test_same_provider_transcription_only(self, tmp_path):
        """Same-provider profile with refinement globally disabled."""
        db = _make_db(tmp_path)
        profile_id = self._create_profile_with_provider(
            db, caps={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(
            profile_id, refinement_globally_disabled=True,
        )

        assert plan.text_processor is None
        assert any("Transcription only" in msg
                   for msg in plan.resolution_log)

    def test_same_provider_transcription_only_mode(self, tmp_path):
        """Same-provider profile with TRANSCRIPTION_ONLY request mode."""
        db = _make_db(tmp_path)
        profile_id = self._create_profile_with_provider(
            db, caps={"transcription": True, "refinement": True},
        )
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(
            profile_id,
            PipelineRequest(mode=RequestMode.TRANSCRIPTION_ONLY),
        )

        assert plan.text_processor is None

    def test_separate_providers_in_profile(self, tmp_path):
        """Profile with different providers for each stage."""
        db = _make_db(tmp_path)
        profile_id = self._create_profile_with_separate_providers(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert "TX Provider" in plan.provider_name
        assert "REF Provider" in plan.provider_name
        assert plan.text_processor is not None
        assert any("separate providers" in msg.lower()
                   for msg in plan.resolution_log)

    def test_resolution_log_populated(self, tmp_path):
        """Profile-based resolution should produce a log."""
        db = _make_db(tmp_path)
        profile_id = self._create_profile_with_provider(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert len(plan.resolution_log) >= 2
        assert any("Loaded pipeline profile" in msg
                   for msg in plan.resolution_log)

    # ---------- Error cases ----------

    def test_profile_not_found(self, tmp_path):
        """Non-existent profile ID should raise."""
        db = _make_db(tmp_path)
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(999)

        assert "non esiste" in str(exc.value.user_message)

    def test_profile_without_transcription_provider(self, tmp_path):
        """Profile without transcription_provider_id should raise."""
        db = _make_db(tmp_path)
        profile_id = db.add_pipeline_profile(
            name="Broken Profile",
            transcription_provider_id=None,
            text_provider_id=None,
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        assert "non ha un provider di trascrizione" in str(exc.value.user_message)

    def test_profile_with_deleted_provider(self, tmp_path):
        """Profile referencing a deleted provider should raise.
        We simulate this by deleting the profile first (to release the FK),
        then deleting the provider, then re-creating the profile reference
        by writing directly to the DB (bypassing FK).
        """
        import sqlite3
        db = _make_db_with_secret(tmp_path)
        db, _ = db if isinstance(db, tuple) else (db, None)

        # Create provider, then create profile referencing it.
        pid = _add_provider(db, adapter="openai-native",
                            capabilities={"transcription": True})
        profile_id = db.add_pipeline_profile(
            name="Orphan Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )

        # Delete the profile first, then the provider, then manually
        # insert a profile row that references the deleted provider.
        # Temporarily disable FK checks to create an orphan reference.
        db.connection.execute("PRAGMA foreign_keys = OFF")
        db.connection.execute("DELETE FROM pipeline_profiles WHERE id = ?",
                              (profile_id,))
        db.connection.commit()
        db.delete_provider(pid)

        # Manually insert an orphan profile row.
        db.connection.execute(
            "INSERT INTO pipeline_profiles "
            "(id, name, transcription_provider_id, text_provider_id) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, "Orphan Profile", pid, pid),
        )
        db.connection.commit()
        db.connection.execute("PRAGMA foreign_keys = ON")
        db.connection.commit()

        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        msg = str(exc.value.user_message).lower()
        assert "non esiste" in msg or "non trovato" in msg

    def test_profile_with_disabled_transcription_provider(self, tmp_path):
        """Profile with disabled transcription provider should raise."""
        db = _make_db_with_secret(tmp_path)
        db, _ = db if isinstance(db, tuple) else (db, None)
        pid = _add_provider(db, enabled=False)
        profile_id = db.add_pipeline_profile(
            name="Disabled Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        assert "disabilitato" in str(exc.value.user_message).lower()

    def test_profile_without_refinement_provider_when_needed(self, tmp_path):
        """Profile without text_provider_id should raise when
        refinement is needed."""
        db = _make_db_with_secret(tmp_path)
        db, _ = db if isinstance(db, tuple) else (db, None)
        pid = _add_provider(db, capabilities={"transcription": True})
        profile_id = db.add_pipeline_profile(
            name="No Ref Profile",
            transcription_provider_id=pid,
            text_provider_id=None,
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        msg = str(exc.value.user_message).lower()
        assert "refinement" in msg and "non ha un provider" in msg

    def test_profile_no_transcription_capability(self, tmp_path):
        """Profile whose provider lacks transcription capability."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, capabilities={"transcription": False, "refinement": True},
        )
        profile_id = db.add_pipeline_profile(
            name="No TX Cap Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        assert "non supporta" in str(exc.value.user_message).lower()

    def test_profile_refinement_provider_no_refinement_cap(self, tmp_path):
        """Profile whose text_provider lacks refinement capability."""
        db = _make_db(tmp_path)
        tx_id = _add_provider(
            db, name="TX",
            capabilities={"transcription": True, "refinement": False},
        )
        ref_id = _add_provider(
            db, name="REF",
            capabilities={"transcription": False, "refinement": False},
        )
        profile_id = db.add_pipeline_profile(
            name="No Ref Cap Profile",
            transcription_provider_id=tx_id,
            text_provider_id=ref_id,
        )
        resolver = PipelineResolver(db)

        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)

        assert "non supporta" in str(exc.value.user_message).lower()


# ------------------------------------------------------------------
# P5 — Wizard profile creation helpers
# ------------------------------------------------------------------


class TestCreatePipelineFromWizard:
    """Tests for :func:`setup_wizard.create_pipeline_from_wizard`."""

    def test_creates_provider_and_profile(self, tmp_path):
        """Complete wizard data should create a provider + profile."""
        db, store = _make_db_with_secret(tmp_path)
        from bot.web.setup_wizard import (
            create_pipeline_from_wizard,
            get_active_pipeline_profile_id,
            save_capabilities,
            save_provider_config,
            save_pipeline_mode,
            save_provider_model,
        )

        # Simulate wizard state
        save_provider_config(
            db, "openai", "sk-test-123", "https://api.openai.com/v1", store,
        )
        save_capabilities(db, {
            "transcription": True, "refinement": True,
            "text_generation": True, "streaming_refinement": False,
            "models": ["gpt-4o-mini", "whisper-1"],
        })
        save_provider_model(db, "gpt-4o-mini")
        save_pipeline_mode(db, "single")

        profile_id = create_pipeline_from_wizard(db, None)

        assert profile_id > 0
        assert get_active_pipeline_profile_id(db) == profile_id

        profile = db.get_pipeline_profile(profile_id)
        assert profile is not None
        assert profile["name"] == "Default (onboarding)"
        assert profile["transcription_provider_id"] is not None
        assert profile["transcription_provider_id"] == profile["text_provider_id"]

        # Verify the provider was created
        provider = db.get_provider(profile["transcription_provider_id"])
        assert provider is not None
        assert provider["name"] == "OpenAI (onboarding)"
        assert provider["adapter_type"] == "openai-native"

    def test_transcription_only_provider(self, tmp_path):
        """Provider with only transcription should still create profile."""
        db = _make_db(tmp_path)
        from bot.web.setup_wizard import (
            create_pipeline_from_wizard,
            get_active_pipeline_profile_id,
            save_capabilities,
            save_provider_config,
            save_pipeline_mode,
        )

        save_provider_config(
            db, "openai", "sk-test-123", "https://api.openai.com/v1", None,
        )
        save_capabilities(db, {
            "transcription": True, "refinement": False,
        })
        save_pipeline_mode(db, "single-no-refine")

        profile_id = create_pipeline_from_wizard(db, None)
        assert profile_id > 0
        assert get_active_pipeline_profile_id(db) == profile_id

        profile = db.get_pipeline_profile(profile_id)
        assert profile is not None
        assert profile["transcription_provider_id"] == profile["text_provider_id"]

    def test_incomplete_wizard_data_raises(self, tmp_path):
        """Missing provider data should raise ValueError."""
        db = _make_db(tmp_path)
        from bot.web.setup_wizard import create_pipeline_from_wizard

        with pytest.raises(ValueError, match="incompleti"):
            create_pipeline_from_wizard(db, None)

    def test_gemini_provider_maps_correctly(self, tmp_path):
        """Gemini wizard type maps to gemini-native adapter."""
        db = _make_db(tmp_path)
        from bot.web.setup_wizard import (
            create_pipeline_from_wizard,
            save_capabilities,
            save_provider_config,
            save_pipeline_mode,
        )

        save_provider_config(
            db, "gemini", "test-key", "", None,
        )
        save_capabilities(db, {
            "transcription": True, "refinement": True,
        })
        save_pipeline_mode(db, "single")

        profile_id = create_pipeline_from_wizard(db, None)
        provider = db.get_provider(
            db.get_pipeline_profile(profile_id)["transcription_provider_id"],
        )
        assert provider["adapter_type"] == "gemini-native"

    def test_openrouter_maps_to_openai_compat(self, tmp_path):
        """OpenRouter wizard type maps to openai-compat adapter."""
        db = _make_db(tmp_path)
        from bot.web.setup_wizard import (
            create_pipeline_from_wizard,
            save_capabilities,
            save_provider_config,
            save_pipeline_mode,
        )

        save_provider_config(
            db, "openrouter", "sk-test", "https://openrouter.ai/api/v1", None,
        )
        save_capabilities(db, {
            "transcription": True, "refinement": True,
        })
        save_pipeline_mode(db, "single")

        profile_id = create_pipeline_from_wizard(db, None)
        provider = db.get_provider(
            db.get_pipeline_profile(profile_id)["transcription_provider_id"],
        )
        assert provider["adapter_type"] == "openai-compat"
        assert provider["endpoint"] == "https://openrouter.ai/api/v1"

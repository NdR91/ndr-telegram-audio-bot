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
    FallbackTextProcessor,
    FallbackTranscriber,
    PipelineRequest,
    PipelineResolver,
    RequestMode,
)
from bot.providers import RefineError, TextProcessor, Transcriber, TranscribeError, TranscriptionResult


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
        # Verify log shows both transcription and refinement stages
        assert any("Stage 'transcription'" in msg
                   for msg in plan.resolution_log)
        assert any("Stage 'refinement'" in msg
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
        assert any("TX Provider" in msg and "REF Provider" in msg
                   or "transcription" in msg.lower()
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

        assert "modello per la trascrizione" in str(exc.value.user_message)

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
        assert any(kw in msg for kw in ["non esiste", "non trovato",
                                        "modello per la trascrizione"])

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
        assert "refinement" in msg and ("non ha un provider" in msg or
                                         "non è configurato" in msg)

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


# ------------------------------------------------------------------
# P5+ — Two-stage resolution with model entries
# ------------------------------------------------------------------


class TestTwoStageWithModelEntries:
    """Two-stage pipeline resolution with explicit model entries.

    Covers:
    - Profile with explicit transcription + refinement stages and model entries.
    - Fallback chains on stages.
    - Transcription-only modes (global disable + request mode).
    - Capability validation for both transcription and refinement.
    - Same-provider different-model display naming.
    - Different-provider with model entries.
    - Resolution log contents.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_two_stage(
        db: DatabaseManager,
        *,
        tx_model_id: str = "whisper-1",
        ref_model_id: str = "gpt-4o-mini",
        tx_caps: dict | None = None,
        ref_caps: dict | None = None,
        provider_name: str = "Multi Model",
        adapter: str = "openai-native",
    ) -> tuple[int, int, int, int, int, int]:
        """Create a two-stage profile with explicit model entries.

        Returns
        -------
        tuple[pid, tx_entry_id, ref_entry_id, profile_id, tx_stage_id, ref_stage_id]
        """
        if tx_caps is None:
            tx_caps = {"transcription": True, "refinement": False}
        if ref_caps is None:
            ref_caps = {"transcription": False, "refinement": True}

        pid = _add_provider(
            db, name=provider_name, adapter=adapter,
            capabilities={"transcription": True, "refinement": True},
        )
        tx_entry = db.add_provider_model(pid, tx_model_id, capabilities=tx_caps)
        ref_entry = db.add_provider_model(pid, ref_model_id, capabilities=ref_caps)

        profile_id = db.add_pipeline_profile(
            name="Two Stage Models",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        tx_stage_id = db.add_pipeline_stage(
            profile_id, "transcription", tx_entry,
        )
        ref_stage_id = db.add_pipeline_stage(
            profile_id, "refinement", ref_entry,
        )
        return pid, tx_entry, ref_entry, profile_id, tx_stage_id, ref_stage_id

    # ---------- Positive cases ----------

    def test_resolves_correct_model_refs(self, tmp_path):
        """✅ Two-stage with model entries: plan has correct ModelRef values."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, _, _ = \
            self._setup_two_stage(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        # Transcription model ref
        assert plan.transcript_model is not None
        assert plan.transcript_model.model_id == "whisper-1"
        assert plan.transcript_model.model_entry_id == tx_entry
        assert plan.transcript_model.provider_id == pid
        assert plan.transcript_model.capabilities.transcription is True

        # Refinement model ref
        assert plan.refine_model is not None
        assert plan.refine_model.model_id == "gpt-4o-mini"
        assert plan.refine_model.model_entry_id == ref_entry
        assert plan.refine_model.provider_id == pid
        assert plan.refine_model.capabilities.refinement is True

        # Display name combines both models
        assert "whisper-1" in plan.model_name
        assert "gpt-4o-mini" in plan.model_name
        assert plan.text_processor is not None

    def test_with_fallback_chain(self, tmp_path):
        """✅ Two-stage with fallback: fallback_model_ids and
        fallback_entry_ids populated."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, tx_stage_id, _ = \
            self._setup_two_stage(db)

        # Add a fallback model for the transcription stage.
        fb_entry = db.add_provider_model(pid, "whisper-1-alt", capabilities={
            "transcription": True,
            "refinement": False,
        })
        db.add_stage_fallback(tx_stage_id, fb_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert len(plan.transcript_model.fallback_model_ids) == 1
        assert plan.transcript_model.fallback_model_ids[0] == "whisper-1-alt"
        assert plan.transcript_model.fallback_entry_ids[0] == fb_entry

    def test_multiple_fallbacks(self, tmp_path):
        """✅ Multiple fallbacks on a stage are ordered correctly."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, tx_stage_id, _ = \
            self._setup_two_stage(db)

        fb1 = db.add_provider_model(pid, "whisper-1-alt1", capabilities={
            "transcription": True,
        })
        fb2 = db.add_provider_model(pid, "whisper-1-alt2", capabilities={
            "transcription": True,
        })
        db.add_stage_fallback(tx_stage_id, fb1, fallback_order=1)
        db.add_stage_fallback(tx_stage_id, fb2, fallback_order=2)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert len(plan.transcript_model.fallback_model_ids) == 2
        assert plan.transcript_model.fallback_model_ids == [
            "whisper-1-alt1", "whisper-1-alt2",
        ]
        assert plan.transcript_model.fallback_entry_ids == [fb1, fb2]

    def test_transcription_only_globally_disabled(self, tmp_path):
        """✅ Two-stage + refinement_globally_disabled=True → no
        text_processor, no refine_model."""
        db = _make_db(tmp_path)
        _, _, _, profile_id, _, _ = self._setup_two_stage(db)
        resolver = PipelineResolver(db)

        plan = resolver.resolve_from_profile(
            profile_id, refinement_globally_disabled=True,
        )

        assert plan.text_processor is None
        assert plan.refine_model is None
        assert any("Transcription only" in msg for msg in plan.resolution_log)

    def test_transcription_only_via_request_mode(self, tmp_path):
        """✅ Two-stage + TRANSCRIPTION_ONLY request mode → no
        text_processor, no refine_model."""
        db = _make_db(tmp_path)
        _, _, _, profile_id, _, _ = self._setup_two_stage(db)
        resolver = PipelineResolver(db)

        plan = resolver.resolve_from_profile(
            profile_id,
            PipelineRequest(mode=RequestMode.TRANSCRIPTION_ONLY),
        )

        assert plan.text_processor is None
        assert plan.refine_model is None

    def test_resolution_log_contains_stage_info(self, tmp_path):
        """✅ Resolution log includes profile, stage, and model details."""
        db = _make_db(tmp_path)
        _, _, _, profile_id, _, _ = self._setup_two_stage(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        log_text = " ".join(plan.resolution_log)
        assert "Loaded pipeline profile" in log_text
        assert "Stage 'transcription'" in log_text
        assert "Stage 'refinement'" in log_text
        assert "whisper-1" in log_text
        assert "gpt-4o-mini" in log_text

    def test_same_provider_different_models_display(self, tmp_path):
        """✅ Same provider, separate model entries: model_name shows
        'tx + ref', provider_name is the shared provider name."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, _, _ = \
            self._setup_two_stage(db, provider_name="Super AI")
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.provider_name == "Super AI"
        assert "whisper-1" in plan.model_name
        assert "gpt-4o-mini" in plan.model_name
        assert plan.transcript_model.model_entry_id != plan.refine_model.model_entry_id
        assert plan.transcript_model.provider_id == plan.refine_model.provider_id

    def test_different_providers_with_model_entries(self, tmp_path):
        """✅ Different providers for tx/ref, each with model entries:
        provider_name combines both."""
        db = _make_db(tmp_path)
        tx_pid = _add_provider(
            db, name="TX AI", adapter="openai-native",
            capabilities={"transcription": True, "refinement": False},
        )
        ref_pid = _add_provider(
            db, name="REF AI", adapter="openai-native",
            capabilities={"transcription": False, "refinement": True},
        )
        tx_entry = db.add_provider_model(tx_pid, "whisper-1", capabilities={
            "transcription": True,
        })
        ref_entry = db.add_provider_model(ref_pid, "gpt-4o-mini", capabilities={
            "refinement": True,
        })
        profile_id = db.add_pipeline_profile(
            name="Separate Model Profile",
            transcription_provider_id=tx_pid,
            text_provider_id=ref_pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", tx_entry)
        db.add_pipeline_stage(profile_id, "refinement", ref_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert "TX AI" in plan.provider_name
        assert "REF AI" in plan.provider_name
        assert plan.transcript_model.model_id == "whisper-1"
        assert plan.refine_model.model_id == "gpt-4o-mini"
        assert plan.text_processor is not None

    def test_stage_fallback_disabled_model_skipped(self, tmp_path):
        """✅ A disabled fallback model is skipped in the fallback chain."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, tx_stage_id, _ = \
            self._setup_two_stage(db)

        # Enabled fallback
        fb_enabled = db.add_provider_model(pid, "fallback-good", capabilities={
            "transcription": True,
        })
        db.add_stage_fallback(tx_stage_id, fb_enabled, fallback_order=1)

        # Disabled fallback — should be omitted
        fb_disabled = db.add_provider_model(pid, "fallback-bad", capabilities={
            "transcription": True,
        }, enabled=False)
        db.add_stage_fallback(tx_stage_id, fb_disabled, fallback_order=2)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.transcript_model.fallback_model_ids == ["fallback-good"]

    # ---------- Negative cases ----------

    def test_missing_transcription_capability_raises(self, tmp_path):
        """❌ Model without transcription capability as tx stage raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="No TX Model", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        tx_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": False,
            "refinement": True,
        })
        ref_entry = db.add_provider_model(pid, "gpt-4o-mini-ref", capabilities={
            "refinement": True,
        })
        profile_id = db.add_pipeline_profile(
            name="Bad TX",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", tx_entry)
        db.add_pipeline_stage(profile_id, "refinement", ref_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert "non supporta" in str(exc.value.user_message).lower()

    def test_missing_refinement_capability_raises(self, tmp_path):
        """❌ Model without refinement capability as ref stage raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="No Ref Model", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        tx_entry = db.add_provider_model(pid, "whisper-1", capabilities={
            "transcription": True,
        })
        ref_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": False,
            "refinement": False,
        })
        profile_id = db.add_pipeline_profile(
            name="Bad Ref",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", tx_entry)
        db.add_pipeline_stage(profile_id, "refinement", ref_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert "non supporta" in str(exc.value.user_message).lower()

    def test_disabled_stage_model_raises(self, tmp_path):
        """❌ A disabled primary model in a stage should raise."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="Disabled Model", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        tx_entry = db.add_provider_model(pid, "whisper-1", capabilities={
            "transcription": True,
        }, enabled=False)
        ref_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "refinement": True,
        })
        profile_id = db.add_pipeline_profile(
            name="Disabled Model Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", tx_entry)
        db.add_pipeline_stage(profile_id, "refinement", ref_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert any(kw in str(exc.value.user_message).lower()
                   for kw in ["modello", "trascrizione"])

    def test_tx_provider_fallback_when_no_tx_stage(self, tmp_path):
        """✅ Profile with only a refinement stage: transcription falls
        back to provider-level resolution."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="One Provider", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        # Only one model entry — used by explicit refinement stage.
        # Provider-level transcription fallback picks this same model,
        # so it must also carry transcription capability.
        ref_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
        })
        profile_id = db.add_pipeline_profile(
            name="Ref-Only Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        # Only a refinement stage exists (no transcription stage).
        # Transcription falls back to provider-level.
        # Refinement uses the explicit stage.
        db.add_pipeline_stage(profile_id, "refinement", ref_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        # Transcription resolved from provider-level (uses first registered model)
        assert plan.transcript_model is not None
        assert plan.transcript_model.model_entry_id == ref_entry  # first enabled model entry
        # Refinement from explicit stage
        assert plan.refine_model is not None
        assert plan.refine_model.model_id == "gpt-4o-mini"
        assert plan.text_processor is not None


# ------------------------------------------------------------------
# P5+ — Single-pass resolution
# ------------------------------------------------------------------


class TestSinglePassResolution:
    """Single-pass pipeline resolution with model entries.

    Covers:
    - Explicit single_pass stage resolves correctly.
    - Fallback chain on single_pass stage.
    - Fallback to two-stage when provider lacks single_pass capability.
    - Single_pass model without required capability raises.
    - Resolution log contents.
    - Disabled model in single_pass stage raises.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_single_pass(
        db: DatabaseManager,
        *,
        adapter: str = "openai-native",
        provider_name: str = "Single Pass AI",
        model_id: str = "gpt-4o-audio-preview",
        sp_caps: dict | None = None,
    ) -> tuple[int, int, int, int]:
        """Create a single-pass profile with explicit stage.

        Returns
        -------
        tuple[pid, sp_entry_id, profile_id, stage_id]
        """
        if sp_caps is None:
            sp_caps = {
                "transcription": True,
                "refinement": True,
                "single_pass_audio_to_text": True,
            }

        pid = _add_provider(
            db, name=provider_name, adapter=adapter,
            capabilities={
                "transcription": True,
                "refinement": True,
                "single_pass_audio_to_text": True,
            },
        )
        sp_entry = db.add_provider_model(pid, model_id, capabilities=sp_caps)

        profile_id = db.add_pipeline_profile(
            name="Single Pass Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        stage_id = db.add_pipeline_stage(
            profile_id, "single_pass", sp_entry,
        )
        return pid, sp_entry, profile_id, stage_id

    # ---------- Positive cases ----------

    def test_explicit_stage_resolves(self, tmp_path):
        """✅ Single-pass with explicit stage: plan has correct model_ref."""
        db = _make_db(tmp_path)
        pid, sp_entry, profile_id, _ = self._setup_single_pass(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.transcript_model is not None
        assert plan.transcript_model.model_id == "gpt-4o-audio-preview"
        assert plan.transcript_model.model_entry_id == sp_entry
        assert plan.transcript_model.provider_id == pid
        assert plan.transcript_model.capabilities.single_pass_audio_to_text is True

        # Single-pass uses the same model ref for both stages
        assert plan.refine_model is not None
        assert plan.refine_model.model_entry_id == sp_entry
        # Both transcriber and text_processor should exist
        assert plan.text_processor is not None
        assert hasattr(plan.transcriber, "transcribe")

    def test_with_fallback_models(self, tmp_path):
        """✅ Single-pass stage with fallback: chain populated."""
        db = _make_db(tmp_path)
        pid, sp_entry, profile_id, stage_id = self._setup_single_pass(db)
        fb_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": True,
        })
        db.add_stage_fallback(stage_id, fb_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert len(plan.transcript_model.fallback_model_ids) == 1
        assert plan.transcript_model.fallback_model_ids[0] == "gpt-4o-mini"
        assert plan.transcript_model.fallback_entry_ids[0] == fb_entry

    def test_fallback_to_two_stage_when_no_single_pass_cap(self, tmp_path):
        """✅ Provider without single_pass capability falls back to
        two-stage resolution."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="OpenAI No SP", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        # No explicit stages — resolver must use provider-level fallback.
        profile_id = db.add_pipeline_profile(
            name="SP Fallback",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        # Should have fallen back to two-stage
        assert plan.text_processor is not None
        log_text = " ".join(plan.resolution_log)
        assert "falling back to two-stage" in log_text.lower()

    def test_fallback_to_two_stage_logged(self, tmp_path):
        """✅ Single-pass fallback includes log messages about the
        fallback decision."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="NoSP", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        profile_id = db.add_pipeline_profile(
            name="Fallback Log",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        log_text = " ".join(plan.resolution_log)
        assert "Pipeline mode: single_pass" in log_text
        assert "falling back to two-stage" in log_text.lower()
        # Two-stage log entries
        assert "Stage" in log_text

    def test_single_pass_log_entries(self, tmp_path):
        """✅ Single-pass resolution log includes profile and model info."""
        db = _make_db(tmp_path)
        _, _, profile_id, _ = self._setup_single_pass(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        log_text = " ".join(plan.resolution_log)
        assert "Loaded pipeline profile" in log_text
        assert "Pipeline mode: single_pass" in log_text
        assert "Single-pass" in plan.resolution_log[-1] or "Single-pass" in log_text
        assert "gpt-4o-audio-preview" in log_text

    def test_openai_provider_single_pass_fallback_with_stages(self, tmp_path):
        """✅ Single-pass mode with explicit stage but provider model
        without single_pass cap falls through to provider-level fallback.

        When the single_pass stage's model lacks the capability, it raises.
        This verifies that capability check is enforced."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="OpenAI SP", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        # Model entry that has transcription+refinement but NOT
        # single_pass_audio_to_text.
        sp_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": False,
        })
        profile_id = db.add_pipeline_profile(
            name="OpenAI SP Stage",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        db.add_pipeline_stage(profile_id, "single_pass", sp_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert "non supporta" in str(exc.value.user_message).lower()

    # ---------- Negative cases ----------

    def test_missing_single_pass_capability_raises(self, tmp_path):
        """❌ Stage model without single_pass_audio_to_text raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="Bad SP", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        sp_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": False,
        })
        profile_id = db.add_pipeline_profile(
            name="Bad SP",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        db.add_pipeline_stage(profile_id, "single_pass", sp_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert "non supporta" in str(exc.value.user_message).lower()

    def test_disabled_single_pass_model_raises(self, tmp_path):
        """❌ Disabled primary model in single_pass stage raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="Disabled SP", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        sp_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": True,
        }, enabled=False)
        profile_id = db.add_pipeline_profile(
            name="Disabled SP",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        db.add_pipeline_stage(profile_id, "single_pass", sp_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert any(kw in str(exc.value.user_message).lower()
                   for kw in ["non esiste", "disabilitato"])

    def test_single_pass_disabled_provider_raises(self, tmp_path):
        """❌ Disabled provider for single_pass stage raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="Disabled Prov", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
            enabled=False,
        )
        sp_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": True,
        })
        profile_id = db.add_pipeline_profile(
            name="Disabled Prov SP",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        db.add_pipeline_stage(profile_id, "single_pass", sp_entry)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert any(kw in str(exc.value.user_message).lower()
                   for kw in ["non esiste", "disabilitato"])


# ------------------------------------------------------------------
# Runtime fallback execution (P4+)
# ------------------------------------------------------------------


class TestFallbackTranscriber:
    """Tests for FallbackTranscriber runtime fallback execution."""

    @pytest.mark.asyncio
    async def test_primary_succeeds_returns_result(self):
        """Primary transcriber succeeds → result returned immediately."""
        primary = _make_stub_transcriber("primary result")
        fallbacks = [_make_stub_transcriber("fb result")]
        ft = FallbackTranscriber(primary, fallbacks)
        result = await ft.transcribe("/tmp/test.mp3")
        assert result.text == "primary result"

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self):
        """Primary fails → fallback is tried and its result returned."""
        calls = []

        class FailingPrimary(Transcriber):
            async def transcribe(self, file_path):
                calls.append("primary")
                raise TranscribeError("Primary failed", "msg")
            def get_capabilities(self): ...

        class WorkingFallback(Transcriber):
            async def transcribe(self, file_path):
                calls.append("fallback")
                return TranscriptionResult(text="fallback result")
            def get_capabilities(self): ...

        ft = FallbackTranscriber(FailingPrimary(), [WorkingFallback()])
        result = await ft.transcribe("/tmp/test.mp3")
        assert result.text == "fallback result"
        assert calls == ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_all_fail_raises_transcribe_error(self):
        """All models fail → raises TranscribeError."""

        class AlwaysFails(Transcriber):
            async def transcribe(self, file_path):
                raise TranscribeError("fail", "msg")
            def get_capabilities(self): ...

        ft = FallbackTranscriber(AlwaysFails(), [AlwaysFails()])
        with pytest.raises(TranscribeError):
            await ft.transcribe("/tmp/test.mp3")

    @pytest.mark.asyncio
    async def test_no_fallbacks_delegates_to_primary(self):
        """No fallbacks → behaves as direct primary."""
        primary = _make_stub_transcriber("direct")
        ft = FallbackTranscriber(primary, [])
        result = await ft.transcribe("/tmp/test.mp3")
        assert result.text == "direct"


class TestFallbackTextProcessor:
    """Tests for FallbackTextProcessor runtime fallback execution."""

    @pytest.mark.asyncio
    async def test_primary_succeeds_returns_result(self):
        """Primary text processor succeeds → result returned immediately."""
        primary = _make_stub_processor("primary result")
        fb = [_make_stub_processor("fb result")]
        fp = FallbackTextProcessor(primary, fb)
        result = await fp.process("hello")
        assert result == "primary result"

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self):
        """Primary fails → fallback is tried and its result returned."""
        calls = []

        class FailingPrimary(TextProcessor):
            async def process(self, raw_text):
                calls.append("primary")
                raise RefineError("Primary failed", "msg")
            def get_capabilities(self): ...

        class WorkingFallback(TextProcessor):
            async def process(self, raw_text):
                calls.append("fallback")
                return "fallback result"
            def get_capabilities(self): ...

        fp = FallbackTextProcessor(FailingPrimary(), [WorkingFallback()])
        result = await fp.process("hello")
        assert result == "fallback result"
        assert calls == ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_all_fail_raises_refine_error(self):
        """All text processors fail → raises RefineError."""

        class AlwaysFails(TextProcessor):
            async def process(self, raw_text):
                raise RefineError("fail", "msg")
            def get_capabilities(self): ...

        fp = FallbackTextProcessor(AlwaysFails(), [AlwaysFails()])
        with pytest.raises(RefineError):
            await fp.process("hello")

    @pytest.mark.asyncio
    async def test_no_fallbacks_delegates_to_primary(self):
        """No fallbacks → behaves as direct processor."""
        primary = _make_stub_processor("direct")
        fp = FallbackTextProcessor(primary, [])
        result = await fp.process("hello")
        assert result == "direct"


class TestRuntimeFallbackInResolver:
    """Tests that the resolver wires fallback transcribers/processors
    into the ExecutionPlan when stages have fallbacks."""

    def test_two_stage_with_fallback_chain_has_fallback_wrapper(self, tmp_path):
        """Two-stage resolution with fallbacks produces a FallbackTranscriber
        in the plan."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, tx_stage_id, ref_stage_id = \
            _setup_two_stage(db)

        # Add a fallback model for the transcription stage.
        fb_entry = db.add_provider_model(pid, "whisper-1-alt", capabilities={
            "transcription": True, "refinement": False,
        })
        db.add_stage_fallback(tx_stage_id, fb_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        # The transcriber should be wrapped in FallbackTranscriber
        assert isinstance(plan.transcriber, FallbackTranscriber)

    def test_two_stage_without_fallback_no_wrapper(self, tmp_path):
        """Two-stage resolution without fallbacks produces a plain
        transcriber (not FallbackTranscriber)."""
        db = _make_db(tmp_path)
        _, _, _, profile_id, _, _ = _setup_two_stage(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert not isinstance(plan.transcriber, FallbackTranscriber)

    def test_refinement_with_fallback_has_fallback_wrapper(self, tmp_path):
        """Refinement stage with fallbacks produces a FallbackTextProcessor."""
        db = _make_db(tmp_path)
        pid, tx_entry, ref_entry, profile_id, tx_stage_id, ref_stage_id = \
            _setup_two_stage(db)

        fb_entry = db.add_provider_model(pid, "gpt-4o", capabilities={
            "refinement": True, "transcription": False,
        })
        db.add_stage_fallback(ref_stage_id, fb_entry)

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.text_processor is not None
        assert isinstance(plan.text_processor, FallbackTextProcessor)

    def test_refinement_without_fallback_no_wrapper(self, tmp_path):
        """Refinement without fallbacks → plain processor (not wrapper)."""
        db = _make_db(tmp_path)
        _, _, _, profile_id, _, _ = _setup_two_stage(db)
        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.text_processor is not None
        assert not isinstance(plan.text_processor, FallbackTextProcessor)


# ------------------------------------------------------------------
# Helpers for fallback tests
# ------------------------------------------------------------------


class _StubTranscriber(Transcriber):
    def __init__(self, text: str):
        self._text = text

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        return TranscriptionResult(text=self._text)

    def get_capabilities(self):
        from bot.capabilities import CapabilityModel
        return CapabilityModel(transcription=True)


class _StubTextProcessor(TextProcessor):
    def __init__(self, text: str):
        self._text = text

    async def process(self, raw_text: str) -> str:
        return self._text

    def get_capabilities(self):
        from bot.capabilities import CapabilityModel
        return CapabilityModel(refinement=True, text_generation=True)


def _make_stub_transcriber(text: str) -> _StubTranscriber:
    return _StubTranscriber(text)


def _make_stub_processor(text: str) -> _StubTextProcessor:
    return _StubTextProcessor(text)


def _setup_two_stage(
    db: DatabaseManager,
    *,
    tx_model_id: str = "whisper-1",
    ref_model_id: str = "gpt-4o-mini",
    tx_caps: dict | None = None,
    ref_caps: dict | None = None,
    provider_name: str = "Multi Model",
    adapter: str = "openai-native",
) -> tuple[int, int, int, int, int, int]:
    """Create a two-stage profile with explicit model entries.

    Returns
    -------
    tuple[pid, tx_entry_id, ref_entry_id, profile_id, tx_stage_id, ref_stage_id]
    """
    if tx_caps is None:
        tx_caps = {"transcription": True, "refinement": False}
    if ref_caps is None:
        ref_caps = {"transcription": False, "refinement": True}

    pid = _add_provider(
        db, name=provider_name, adapter=adapter,
        capabilities={"transcription": True, "refinement": True},
    )
    tx_entry = db.add_provider_model(pid, tx_model_id, capabilities=tx_caps)
    ref_entry = db.add_provider_model(pid, ref_model_id, capabilities=ref_caps)

    profile_id = db.add_pipeline_profile(
        name="Two Stage Models",
        transcription_provider_id=pid,
        text_provider_id=pid,
    )
    tx_stage_id = db.add_pipeline_stage(profile_id, "transcription", tx_entry)
    ref_stage_id = db.add_pipeline_stage(profile_id, "refinement", ref_entry)
    return pid, tx_entry, ref_entry, profile_id, tx_stage_id, ref_stage_id


# ------------------------------------------------------------------
# P5+ — Fallback chain edge cases
# ------------------------------------------------------------------


class TestFallbackChainEdgeCases:
    """Edge cases for fallback chain resolution."""

    def test_empty_fallback_chain(self, tmp_path):
        """✅ Stage with no fallbacks: empty fallback lists."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="No Fallback", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        tx_entry = db.add_provider_model(pid, "whisper-1", capabilities={
            "transcription": True,
            "refinement": False,
        })
        ref_entry = db.add_provider_model(pid, "gpt-4o-mini", capabilities={
            "transcription": False,
            "refinement": True,
        })
        profile_id = db.add_pipeline_profile(
            name="No FB",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", tx_entry)
        # Refinement falls back to provider-level — gpt-4o-mini has
        # refinement capability.

        resolver = PipelineResolver(db)
        plan = resolver.resolve_from_profile(profile_id)

        assert plan.transcript_model.fallback_model_ids == []
        assert plan.transcript_model.fallback_entry_ids == []

    def test_stage_with_none_primary_model(self, tmp_path):
        """❌ Stage with None primary_model_id raises — the resolver
        does not fall back to provider-level when a stage entry exists."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="None Primary", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        profile_id = db.add_pipeline_profile(
            name="None Primary",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.add_pipeline_stage(profile_id, "transcription", primary_model_id=None)

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert any(kw in str(exc.value.user_message).lower()
                   for kw in ["modello", "trascrizione"])

    def test_stage_non_existent_primary_model(self, tmp_path):
        """❌ Stage referencing a non-existent primary model raises."""
        db = _make_db(tmp_path)
        pid = _add_provider(
            db, name="Bad Primary", adapter="openai-native",
            capabilities={"transcription": True, "refinement": True},
        )
        profile_id = db.add_pipeline_profile(
            name="Bad Primary",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        # Bypass FK check to create a stage that references a model
        # entry that does not exist.
        db.connection.execute("PRAGMA foreign_keys = OFF")
        db.connection.execute(
            "INSERT INTO pipeline_stages (profile_id, stage_type, primary_model_id) "
            "VALUES (?, 'transcription', 99999)",
            (profile_id,),
        )
        db.connection.commit()
        db.connection.execute("PRAGMA foreign_keys = ON")
        db.connection.commit()

        resolver = PipelineResolver(db)
        with pytest.raises(PipelineResolutionError) as exc:
            resolver.resolve_from_profile(profile_id)
        assert any(kw in str(exc.value.user_message).lower()
                   for kw in ["modello", "trascrizione"])


# ------------------------------------------------------------------
# P5+ — ModelRef unit construction
# ------------------------------------------------------------------


class TestModelRef:
    """Unit tests for the ModelRef frozen dataclass."""

    def test_construct_with_all_fields(self):
        from bot.pipeline_resolver import ModelRef
        from bot.capabilities import CapabilityModel

        ref = ModelRef(
            provider_id=1,
            adapter_type="openai-native",
            model_entry_id=42,
            model_id="whisper-1",
            capabilities=CapabilityModel(transcription=True),
            fallback_model_ids=["whisper-1-alt"],
            fallback_entry_ids=[43],
        )
        assert ref.provider_id == 1
        assert ref.adapter_type == "openai-native"
        assert ref.model_entry_id == 42
        assert ref.model_id == "whisper-1"
        assert ref.capabilities.transcription is True
        assert ref.fallback_model_ids == ["whisper-1-alt"]
        assert ref.fallback_entry_ids == [43]

    def test_default_fallback_lists(self):
        from bot.pipeline_resolver import ModelRef
        from bot.capabilities import CapabilityModel

        ref = ModelRef(
            provider_id=1,
            adapter_type="openai-native",
            model_entry_id=None,
            model_id="gpt-4o-mini",
            capabilities=CapabilityModel(),
        )
        assert ref.fallback_model_ids == []
        assert ref.fallback_entry_ids == []

    def test_is_frozen(self):
        from bot.pipeline_resolver import ModelRef
        from bot.capabilities import CapabilityModel

        ref = ModelRef(
            provider_id=1,
            adapter_type="openai-native",
            model_entry_id=None,
            model_id="m1",
            capabilities=CapabilityModel(),
        )
        with pytest.raises(AttributeError):
            ref.model_id = "other"  # type: ignore[misc]

    def test_capabilities_default_to_false(self):
        from bot.pipeline_resolver import ModelRef
        from bot.capabilities import CapabilityModel

        ref = ModelRef(
            provider_id=1,
            adapter_type="unknown",
            model_entry_id=None,
            model_id="m1",
            capabilities=CapabilityModel(),
        )
        assert ref.capabilities.transcription is False
        assert ref.capabilities.refinement is False
        assert ref.capabilities.single_pass_audio_to_text is False

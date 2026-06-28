"""
Shared pipeline profile builders for web setup and admin forms.

The express setup flow and the advanced pipeline page must create equivalent
database state.  Keeping profile/stage creation here avoids subtle drift between
the two paths.
"""

from __future__ import annotations

from bot.database import DatabaseManager

ACTIVE_PROFILE_KEY = "active_pipeline_profile"


def set_active_pipeline_profile_id(db: DatabaseManager, profile_id: int) -> None:
    """Persist the active pipeline profile ID."""
    db.set_setup_state(ACTIVE_PROFILE_KEY, str(profile_id))


def create_two_stage_profile(
    db: DatabaseManager,
    *,
    tx_model_id: int,
    ref_model_id: int | None = None,
    name: str = "Pipeline due fasi",
    activate: bool = True,
) -> int:
    """Create a two-stage profile with explicit model stages."""
    tx_model = db.get_provider_model(tx_model_id)
    if tx_model is None:
        raise ValueError("Modello di trascrizione non trovato.")

    ref_model = db.get_provider_model(ref_model_id) if ref_model_id else None
    tx_provider_id = tx_model["provider_id"]
    ref_provider_id = ref_model["provider_id"] if ref_model else tx_provider_id

    profile_id = db.add_pipeline_profile(
        name=name,
        transcription_provider_id=tx_provider_id,
        text_provider_id=ref_provider_id,
        mode="two_stage",
    )
    db.add_pipeline_stage(profile_id, "transcription", tx_model_id)
    if ref_model_id is not None:
        db.add_pipeline_stage(profile_id, "refinement", ref_model_id)
    if activate:
        set_active_pipeline_profile_id(db, profile_id)
    return profile_id


def create_single_pass_profile(
    db: DatabaseManager,
    *,
    model_id: int,
    name: str = "Pipeline singolo passaggio",
    activate: bool = True,
) -> int:
    """Create a single-pass profile with one explicit model stage."""
    model = db.get_provider_model(model_id)
    if model is None:
        raise ValueError("Modello single-pass non trovato.")

    provider_id = model["provider_id"]
    profile_id = db.add_pipeline_profile(
        name=name,
        transcription_provider_id=provider_id,
        text_provider_id=provider_id,
        mode="single_pass",
    )
    db.add_pipeline_stage(profile_id, "single_pass", model_id)
    if activate:
        set_active_pipeline_profile_id(db, profile_id)
    return profile_id


def create_same_provider_profile(
    db: DatabaseManager,
    *,
    provider_id: int,
    name: str = "Pipeline predefinita",
    activate: bool = True,
) -> int:
    """Create a legacy same-provider two-stage profile."""
    profile_id = db.add_pipeline_profile(
        name=name,
        transcription_provider_id=provider_id,
        text_provider_id=provider_id,
        mode="two_stage",
    )
    if activate:
        set_active_pipeline_profile_id(db, profile_id)
    return profile_id


def create_advanced_provider_profile(
    db: DatabaseManager,
    *,
    transcription_provider_id: int,
    text_provider_id: int | None,
    name: str = "Pipeline avanzata",
    activate: bool = True,
) -> int:
    """Create an advanced provider-level profile without explicit stages."""
    profile_id = db.add_pipeline_profile(
        name=name,
        transcription_provider_id=transcription_provider_id,
        text_provider_id=text_provider_id,
        mode="two_stage",
    )
    if activate:
        set_active_pipeline_profile_id(db, profile_id)
    return profile_id

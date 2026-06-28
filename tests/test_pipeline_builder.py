"""
Tests for shared web pipeline profile builders.
"""

from bot.database import DatabaseManager
from bot.web.pipeline_builder import (
    create_advanced_provider_profile,
    create_same_provider_profile,
    create_single_pass_profile,
    create_two_stage_profile,
)


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _provider(db: DatabaseManager, name: str = "Provider") -> int:
    return db.add_provider(
        name=name,
        adapter_type="openai-compat",
        credentials="sk-test",
        enabled=True,
    )


def test_create_two_stage_profile_adds_stages_and_activates(tmp_path):
    db = _make_db(tmp_path)
    provider_id = _provider(db)
    tx_model = db.add_provider_model(
        provider_id,
        "whisper-1",
        capabilities={"transcription": True},
    )
    ref_model = db.add_provider_model(
        provider_id,
        "openai/gpt-4o-mini",
        capabilities={"refinement": True, "text_generation": True},
    )

    profile_id = create_two_stage_profile(
        db,
        tx_model_id=tx_model,
        ref_model_id=ref_model,
    )

    assert db.get_setup_state("active_pipeline_profile") == str(profile_id)
    profile = db.get_pipeline_profile(profile_id)
    assert profile["mode"] == "two_stage"
    assert profile["transcription_provider_id"] == provider_id
    assert profile["text_provider_id"] == provider_id
    stages = db.list_pipeline_stages(profile_id)
    assert [(s["stage_type"], s["primary_model_id"]) for s in stages] == [
        ("transcription", tx_model),
        ("refinement", ref_model),
    ]


def test_create_two_stage_profile_allows_optional_refinement(tmp_path):
    db = _make_db(tmp_path)
    provider_id = _provider(db)
    tx_model = db.add_provider_model(
        provider_id,
        "whisper-1",
        capabilities={"transcription": True},
    )

    profile_id = create_two_stage_profile(db, tx_model_id=tx_model)

    profile = db.get_pipeline_profile(profile_id)
    assert profile["text_provider_id"] == provider_id
    stages = db.list_pipeline_stages(profile_id)
    assert [(s["stage_type"], s["primary_model_id"]) for s in stages] == [
        ("transcription", tx_model),
    ]


def test_create_single_pass_profile_adds_single_stage(tmp_path):
    db = _make_db(tmp_path)
    provider_id = _provider(db, name="Gemini")
    model = db.add_provider_model(
        provider_id,
        "gemini-2.5-flash",
        capabilities={
            "transcription": True,
            "refinement": True,
            "single_pass_audio_to_text": True,
        },
    )

    profile_id = create_single_pass_profile(db, model_id=model)

    profile = db.get_pipeline_profile(profile_id)
    assert profile["mode"] == "single_pass"
    assert profile["transcription_provider_id"] == provider_id
    assert db.list_pipeline_stages(profile_id)[0]["stage_type"] == "single_pass"
    assert db.list_pipeline_stages(profile_id)[0]["primary_model_id"] == model


def test_create_same_provider_profile_keeps_legacy_shape(tmp_path):
    db = _make_db(tmp_path)
    provider_id = _provider(db)

    profile_id = create_same_provider_profile(db, provider_id=provider_id)

    profile = db.get_pipeline_profile(profile_id)
    assert profile["mode"] == "two_stage"
    assert profile["transcription_provider_id"] == provider_id
    assert profile["text_provider_id"] == provider_id
    assert db.list_pipeline_stages(profile_id) == []


def test_create_advanced_provider_profile_keeps_provider_level_shape(tmp_path):
    db = _make_db(tmp_path)
    tx_provider = _provider(db, name="TX")
    ref_provider = _provider(db, name="REF")

    profile_id = create_advanced_provider_profile(
        db,
        transcription_provider_id=tx_provider,
        text_provider_id=ref_provider,
    )

    profile = db.get_pipeline_profile(profile_id)
    assert profile["mode"] == "two_stage"
    assert profile["transcription_provider_id"] == tx_provider
    assert profile["text_provider_id"] == ref_provider
    assert db.get_setup_state("active_pipeline_profile") == str(profile_id)

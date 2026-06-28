"""
Tests for W10 smart model picker helpers.
"""

from bot.model_picker import (
    build_openrouter_picker_cards,
    manual_model_card,
    openrouter_counts,
    select_openrouter_models,
    transcription_locked_card,
)


def _catalog():
    return [
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o mini",
            "description": "Small fast text model.",
            "pricing": {
                "prompt": "0.00000015",
                "completion": "0.0000006",
            },
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "modality": "text->text",
            },
            "supported_parameters": ["stream"],
        },
        {
            "id": "anthropic/claude-3.5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "pricing": {
                "prompt": "0.000003",
                "completion": "0.000015",
            },
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "modality": "text->text",
            },
            "supported_parameters": ["stream"],
        },
        {
            "id": "openai/whisper-1",
            "name": "Whisper",
            "pricing": {
                "prompt": "0",
                "completion": "0",
                "request": "0.0001",
            },
            "architecture": {
                "input_modalities": ["audio"],
                "output_modalities": ["text"],
                "modality": "audio->text",
            },
            "supported_parameters": [],
        },
        {
            "id": "google/gemini-2.0-flash",
            "name": "Gemini 2.0 Flash",
            "pricing": {
                "prompt": "0.0000001",
                "completion": "0.0000004",
            },
            "architecture": {
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
                "modality": "multimodal->text",
            },
            "supported_parameters": ["stream"],
        },
        {
            "id": "image/only",
            "name": "Image only",
            "architecture": {
                "input_modalities": ["image"],
                "output_modalities": ["image"],
                "modality": "image->image",
            },
            "supported_parameters": [],
        },
    ]


def test_transcription_locked_card_is_whisper():
    card = transcription_locked_card()

    assert card["model_id"] == "whisper-1"
    assert card["locked"] is True
    assert card["recommended"] is True
    assert "standard industriale" in card["description"]
    assert card["capabilities"]["transcription"] is True


def test_select_openrouter_models_filters_by_refinement():
    selected = select_openrouter_models(
        _catalog(),
        purpose="refinement",
        limit=5,
    )

    assert [m["id"] for m in selected] == [
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-sonnet",
    ]


def test_build_openrouter_picker_cards_formats_tradeoffs():
    cards = build_openrouter_picker_cards(
        _catalog(),
        purpose="refinement",
        limit=5,
    )

    assert len(cards) == 2
    first = cards[0]
    assert first["model_id"] == "openai/gpt-4o-mini"
    assert first["provider"] == "OpenAI"
    assert first["pricing"]["input_per_million"] == 0.15
    assert first["pricing"]["output_per_million"] == 0.6
    assert first["speed"] == "fast"
    assert first["quality"] == "high"
    assert first["recommended"] is True
    assert sum(1 for card in cards if card["recommended"]) == 1


def test_build_openrouter_picker_cards_supports_single_pass():
    cards = build_openrouter_picker_cards(
        _catalog(),
        purpose="single_pass",
        limit=5,
    )

    assert [card["model_id"] for card in cards] == ["google/gemini-2.0-flash"]
    assert cards[0]["category"] == "single_pass"
    assert cards[0]["capabilities"]["single_pass_audio_to_text"] is True
    assert cards[0]["recommended"] is True


def test_openrouter_counts_separates_categories():
    counts = openrouter_counts(_catalog())

    assert counts["refinement"] == 2
    assert counts["transcription"] == 1
    assert counts["single_pass"] == 1
    assert counts["not_recommended"] == 1


def test_manual_model_card_is_conservative():
    card = manual_model_card("custom/model-id", "OpenRouter")

    assert card["kind"] == "manual"
    assert card["model_id"] == "custom/model-id"
    assert card["pricing"]["input_per_million"] is None
    assert card["speed"] == "unknown"
    assert card["quality"] == "unknown"
    assert card["recommended"] is False

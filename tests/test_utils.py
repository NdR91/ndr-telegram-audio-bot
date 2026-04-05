from types import SimpleNamespace

from bot import utils


def test_create_provider_uses_openai_default_model(monkeypatch):
    captured = {}

    class DummyProvider:
        def __init__(self, api_key, model_name, prompts):
            captured["api_key"] = api_key
            captured["model_name"] = model_name
            captured["prompts"] = prompts

    monkeypatch.setattr(utils, "OpenAIProvider", DummyProvider)
    config = SimpleNamespace(
        provider_name="openai",
        model_name=None,
        prompts={"system": "s", "refine_template": "{raw_text}"},
        get_api_key=lambda provider: "openai-key",
    )

    provider = utils.create_provider(config)

    assert isinstance(provider, DummyProvider)
    assert captured["api_key"] == "openai-key"
    assert captured["model_name"] == "gpt-4o-mini"


def test_cleanup_audio_directory_removes_only_allowed_audio_files(monkeypatch, tmp_path):
    audio_dir = tmp_path / "audio_files"
    audio_dir.mkdir()
    keep_file = audio_dir / "note.txt"
    delete_file = audio_dir / "clip.mp3"
    keep_file.write_text("note", encoding="utf-8")
    delete_file.write_text("audio", encoding="utf-8")
    monkeypatch.setenv("AUDIO_CLEANUP_ON_STARTUP", "1")

    utils.cleanup_audio_directory(str(audio_dir))

    assert keep_file.exists()
    assert not delete_file.exists()

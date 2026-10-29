import asyncio
from datetime import datetime, timezone

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.transcription.models import ConfigInput, ProcessingResult, RuntimeLimits


def _input():
    return ConfigInput(
        service="openai", mode="multimodal", audio_model="synthetic-audio",
        text_model=None, credential_version=1, system_prompt="Faithful",
        refine_template="{raw_text}", limits=RuntimeLimits(),
    )


async def test_profile_test_saves_receipt_only_for_complete_unchanged_revision(tmp_path):
    services = build_services(DeploymentSettings(
        tmp_path, tmp_path / "db.sqlite3", tmp_path / "key"
    ))
    try:
        services.repo.put_credential("openai", "synthetic-provider-key")
        profile = services.profiles.create("Test profile", _input())
        audio = tmp_path / "synthetic.wav"
        audio.write_bytes(b"synthetic audio")
        calls = []

        class Runner:
            async def run(self, snapshot, path):
                calls.append((snapshot, path))
                return ProcessingResult("synthetic transcript", False, ("synthetic-audio",), 1)

        services.transcription_tests._runner = Runner()
        result = await services.transcription_tests.run_profile(
            profile.profile_id, profile.revision, audio
        )

        assert result.status == "succeeded"
        assert result.profile_id == profile.profile_id
        assert calls[0][0].number == profile.revision
        assert calls[0][1] == audio
        assert services.profiles.has_valid_receipt(
            profile.profile_id, profile.revision, 1
        )
    finally:
        services.repo.close()


async def test_partial_result_and_revision_race_never_create_receipt(tmp_path):
    services = build_services(DeploymentSettings(
        tmp_path, tmp_path / "db.sqlite3", tmp_path / "key"
    ))
    try:
        services.repo.put_credential("openai", "synthetic-provider-key")
        profile = services.profiles.create("Test profile", _input())
        audio = tmp_path / "synthetic.wav"
        audio.write_bytes(b"synthetic audio")

        class PartialRunner:
            async def run(self, snapshot, path):
                return ProcessingResult("partial", True, ("synthetic-audio",), 1)

        services.transcription_tests._runner = PartialRunner()
        partial = await services.transcription_tests.run_profile(
            profile.profile_id, profile.revision, audio
        )
        assert partial.status == "failed"
        assert not services.profiles.has_valid_receipt(profile.profile_id, profile.revision, 1)

        class RacingRunner:
            async def run(self, snapshot, path):
                services.profiles.save_revision(
                    profile.profile_id, profile.revision,
                    expected_generation=services.profiles.get_draft(profile.profile_id).generation,
                    name="Test profile", values=_input(),
                )
                return ProcessingResult("complete", False, ("synthetic-audio",), 1)

        services.transcription_tests._runner = RacingRunner()
        raced = await services.transcription_tests.run_profile(
            profile.profile_id, profile.revision, audio
        )
        assert raced.status == "failed"
        assert not services.profiles.has_valid_receipt(profile.profile_id, profile.revision, 1)
    finally:
        services.repo.close()


def test_sample_metadata_excludes_local_asset_path(tmp_path):
    services = build_services(DeploymentSettings(
        tmp_path, tmp_path / "db.sqlite3", tmp_path / "key"
    ))
    try:
        sample = services.transcription_tests.sample_info()
        assert sample.sha256
        assert sample.duration_seconds > 0
        assert sample.expected_text
    finally:
        services.repo.close()

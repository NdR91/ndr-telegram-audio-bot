"""Explicit, short-lived configuration tests using the production runner."""

import asyncio
import hashlib
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.transcription.configuration import VALIDATOR_VERSION
from bot.transcription.errors import ConfigurationInvalid, ServiceFailure
from bot.transcription.models import ConfigRevision, ProcessingResult, TestReceipt
from bot.transcription.profiles import ProfileService

_SAMPLE_PATH = Path(__file__).parent / "assets" / "transcription-sample.wav"
_SAMPLE_TEXT = "The blue bird sings at sunrise."
_SAMPLE_SHA256 = "04ab9192ded8a373a60ecc8f3c3da956694e0360630ce66487d7596dfe63d922"
_SAMPLE_DURATION_SECONDS = 1.858875


@dataclass(frozen=True)
class BundledAudioSample:
    path: Path
    expected_text: str
    duration_seconds: float
    sha256: str


@dataclass(frozen=True)
class TestView:
    __test__ = False

    id: str
    revision: int
    status: str
    result: ProcessingResult | None = None
    safe_error: str | None = None
    diagnostics: tuple[str, ...] = ()
    profile_id: int | None = None


class ConfigurationTestService:
    """Keep transcript text in process memory and persist only proof metadata."""

    def __init__(self, configuration, runner, clock=time.monotonic,
                 profiles: ProfileService | None = None):
        self._configuration = configuration
        self._profiles = profiles
        self._runner = runner
        self._clock = clock
        self._views: dict[str, tuple[float, TestView]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def sample_info() -> BundledAudioSample:
        """Return safe, non-secret metadata for the included test recording."""
        return BundledAudioSample(
            _SAMPLE_PATH, _SAMPLE_TEXT, _SAMPLE_DURATION_SECONDS, _SAMPLE_SHA256
        )

    async def run_bundled_sample(
        self, revision: int, temporary_dir: Path | None = None
    ) -> TestView:
        """Run the included audio through the regular runner using a temporary copy."""
        sample = self.sample_info()
        sample_digest = hashlib.sha256(sample.path.read_bytes()).hexdigest() if sample.path.is_file() else None
        if sample_digest != sample.sha256:
            raise ConfigurationInvalid("Bundled audio sample is unavailable")

        base_dir = (
            Path(temporary_dir) if temporary_dir is not None else Path(tempfile.gettempdir())
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-test-sample-", dir=base_dir) as temp_dir:
            audio_path = Path(temp_dir) / sample.path.name
            shutil.copyfile(sample.path, audio_path)
            return await self.run(revision, audio_path)

    async def run_profile_bundled_sample(
        self, profile_id: int, revision: int, temporary_dir: Path | None = None
    ) -> TestView:
        """Test one immutable profile revision against a temporary sample copy."""
        sample = self.sample_info()
        sample_digest = hashlib.sha256(sample.path.read_bytes()).hexdigest() if sample.path.is_file() else None
        if sample_digest != sample.sha256:
            raise ConfigurationInvalid("Bundled audio sample is unavailable")
        base_dir = Path(temporary_dir) if temporary_dir is not None else Path(tempfile.gettempdir())
        base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v2-test-sample-", dir=base_dir) as temp_dir:
            audio_path = Path(temp_dir) / sample.path.name
            shutil.copyfile(sample.path, audio_path)
            return await self.run_profile(profile_id, revision, audio_path)

    async def run_profile(self, profile_id: int, revision: int, audio: Path) -> TestView:
        if self._profiles is None:
            raise ConfigurationInvalid("Profile testing is unavailable")
        if self._lock.locked():
            raise RuntimeError("A configuration test is already running")
        async with self._lock:
            snapshot = self._profiles.get_revision(profile_id, revision)
            draft = self._profiles.get_draft(profile_id)
            if draft.revision != revision:
                from bot.transcription.errors import RevisionConflict
                raise RevisionConflict("Profile changed; reload before testing")
            test_id = secrets.token_urlsafe(18)
            pending = TestView(test_id, revision, "running", profile_id=profile_id)
            self._remember(pending)
            try:
                with self._profiles._repo.profile_lease(profile_id, revision):
                    result = await self._runner.run(
                        ConfigRevision(revision, snapshot.values), audio
                    )
                    current_draft = self._profiles.get_draft(profile_id)
                    unchanged = current_draft.revision == revision
                    succeeded = not result.partial and unchanged
                    view = TestView(
                        test_id, revision, "succeeded" if succeeded else "failed",
                        result=result,
                        safe_error=None if succeeded else "Il profilo è cambiato o il percorso è parziale; ripeti la prova.",
                        profile_id=profile_id,
                    )
                    if succeeded:
                        self._profiles.record_test(profile_id, TestReceipt(
                            id=test_id, revision=revision,
                            credential_version=snapshot.values.credential_version,
                            validator_version=VALIDATOR_VERSION, success=True,
                            models=result.models, finished_at=datetime.now(timezone.utc),
                        ))
                self._remember(view)
                return view
            except asyncio.CancelledError:
                self._views.pop(test_id, None)
                raise
            except ServiceFailure as exc:
                diagnostics = (f"Fase non completata: {exc.stage}",)
                view = TestView(test_id, revision, "failed", safe_error=exc.safe_message,
                                diagnostics=diagnostics, profile_id=profile_id)
                self._remember(view)
                return view
            except Exception:
                view = TestView(
                    test_id, revision, "failed",
                    safe_error="La prova non è riuscita. Controlla servizio e configurazione.",
                    diagnostics=("Elaborazione interrotta prima di ricevere un risultato",),
                    profile_id=profile_id,
                )
                self._remember(view)
                return view

    async def run(self, revision: int, audio: Path) -> TestView:
        if self._lock.locked():
            raise RuntimeError("A configuration test is already running")
        async with self._lock:
            snapshot = self._configuration.draft()
            if snapshot is None or snapshot.number != revision:
                from bot.transcription.errors import RevisionConflict
                raise RevisionConflict("Draft changed; reload before testing")
            test_id = secrets.token_urlsafe(18)
            pending = TestView(test_id, revision, "running")
            self._remember(pending)
            try:
                result = await self._runner.run(snapshot, audio)
                current_draft = self._configuration.draft()
                with self._configuration._repo.read() as connection:
                    current_credential = connection.execute(
                        "SELECT MAX(version) FROM credentials WHERE service = ?",
                        (snapshot.values.service,),
                    ).fetchone()[0]
                    saved_key = connection.execute(
                        'SELECT 1 FROM setup_state WHERE setup_key = ?',
                        (f'saved_key:{snapshot.values.service}:{snapshot.values.credential_version}',),
                    ).fetchone()
                unchanged = (current_draft is not None
                             and current_draft.number == snapshot.number
                             and (saved_key is not None or
                                  int(current_credential or 0) == snapshot.values.credential_version))
                succeeded = not result.partial and unchanged
                view = TestView(test_id, revision, "succeeded" if succeeded else "failed",
                                result=result,
                                safe_error=None if succeeded else "La bozza o la credenziale è cambiata, oppure il percorso è parziale; ripeti la prova.")
                if succeeded:
                    self._configuration.record_test(TestReceipt(
                        id=test_id, revision=revision,
                        credential_version=snapshot.values.credential_version,
                        validator_version=VALIDATOR_VERSION, success=True,
                        models=result.models, finished_at=datetime.now(timezone.utc),
                    ))
                self._remember(view)
                return view
            except asyncio.CancelledError:
                self._views.pop(test_id, None)
                raise
            except ServiceFailure as exc:
                if exc.stage == "conversion":
                    source_format = Path(audio).suffix.lower().lstrip(".") or "sconosciuto"
                    diagnostics = (
                        f"Conversione audio: {source_format} → mp3 con FFmpeg",
                        "Conversione non riuscita; chiamata al modello non eseguita",
                    )
                else:
                    diagnostics = (f"Fase non completata: {exc.stage}",)
                view = TestView(test_id, revision, "failed", safe_error=exc.safe_message,
                                diagnostics=diagnostics)
                self._remember(view)
                return view
            except Exception:
                view = TestView(test_id, revision, "failed", safe_error="La prova non è riuscita. Controlla servizio e configurazione.",
                                diagnostics=("Elaborazione interrotta prima di ricevere un risultato",))
                self._remember(view)
                return view

    def result(self, test_id: str) -> TestView | None:
        item = self._views.get(test_id)
        if item is None:
            return None
        created, view = item
        if self._clock() - created >= 600:
            self._views.pop(test_id, None)
            return None
        return view

    def _remember(self, view: TestView) -> None:
        now = self._clock()
        self._views = {key: value for key, value in self._views.items()
                       if now - value[0] < 600}
        self._views[view.id] = (now, view)

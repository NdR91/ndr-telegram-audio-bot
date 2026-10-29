"""Explicit, short-lived configuration tests using the production runner."""

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.transcription.configuration import VALIDATOR_VERSION
from bot.transcription.models import ConfigRevision, ProcessingResult, TestReceipt


@dataclass(frozen=True)
class TestView:
    id: str
    revision: int
    status: str
    result: ProcessingResult | None = None
    safe_error: str | None = None


class ConfigurationTestService:
    """Keep transcript text in process memory and persist only proof metadata."""

    def __init__(self, configuration, runner, clock=time.monotonic):
        self._configuration = configuration
        self._runner = runner
        self._clock = clock
        self._views: dict[str, tuple[float, TestView]] = {}
        self._lock = asyncio.Lock()

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
                unchanged = (current_draft is not None
                             and current_draft.number == snapshot.number
                             and int(current_credential or 0) == snapshot.values.credential_version)
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
            except Exception:
                view = TestView(test_id, revision, "failed", safe_error="La prova non è riuscita. Controlla servizio e configurazione.")
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

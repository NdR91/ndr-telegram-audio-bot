"""Cross-process single-instance lock for a live V2 database runtime."""

import fcntl
import os
import stat
import threading
from pathlib import Path


class RuntimeDatabaseLocked(RuntimeError):
    """Another live application process already owns this database."""


class RuntimeDatabaseLock:
    """Hold an advisory OS lock without affecting migrations or repository fixtures."""

    _registry_lock = threading.RLock()
    _registry: dict[str, list[int]] = {}

    def __init__(self, database_path: Path):
        self.path = Path(f"{Path(database_path).resolve()}.runtime.lock")
        self._fd: int | None = None
        self._held = False

    def acquire(self) -> None:
        if self._held:
            return
        key = str(self.path)
        with self._registry_lock:
            existing = self._registry.get(key)
            if existing is not None:
                existing[1] += 1
                self._fd = existing[0]
                self._held = True
                return
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except OSError as exc:
                raise RuntimeDatabaseLocked(
                    "The V2 database runtime lock could not be opened"
                ) from exc
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeDatabaseLocked(
                        "The V2 database runtime lock is not a regular file"
                    )
                os.fchmod(descriptor, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeDatabaseLocked(
                        "Another application process already uses this V2 database"
                    ) from exc
            except BaseException:
                os.close(descriptor)
                raise
            self._registry[key] = [descriptor, 1]
            self._fd = descriptor
            self._held = True

    def release(self) -> None:
        if not self._held:
            return
        key = str(self.path)
        with self._registry_lock:
            self._held = False
            self._fd = None
            entry = self._registry.get(key)
            if entry is None:
                self._fd = None
                return
            entry[1] -= 1
            if entry[1] == 0:
                descriptor = entry[0]
                del self._registry[key]
                self._fd = None
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

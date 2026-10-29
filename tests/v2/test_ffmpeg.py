"""FFmpeg subprocess completion and cleanup stay reliable under asyncio."""

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import bot.transcription.ffmpeg as ffmpeg
from bot.transcription.ffmpeg import convert_to_mp3


def test_conversion_of_temporary_sample_completes_and_writes_mp3(tmp_path):
    packaged = Path(__file__).parents[2] / "bot/transcription/assets/transcription-sample.wav"
    sample_dir = tmp_path / "v2-test-sample"
    sample_dir.mkdir()
    source = sample_dir / packaged.name
    shutil.copyfile(packaged, source)
    target = tmp_path / "runner-work" / "sample.mp3"
    target.parent.mkdir()

    asyncio.run(asyncio.wait_for(convert_to_mp3(str(source), str(target)), timeout=5))

    assert target.is_file()
    assert target.read_bytes().startswith(b"ID3")


def test_cancellation_kills_and_reaps_ffmpeg_without_blocking_loop(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "ffmpeg-started"
    fake_ffmpeg = bin_dir / "ffmpeg"
    fake_ffmpeg.write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        f"open({str(started)!r}, 'w').close()\n"
        "time.sleep(30)\n"
    )
    fake_ffmpeg.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic audio")

    async def cancel_conversion():
        task = asyncio.create_task(convert_to_mp3(str(source), str(tmp_path / "out.mp3")))
        for _ in range(100):
            if started.exists():
                break
            await asyncio.sleep(0.01)
        assert started.exists(), "FFmpeg process started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(cancel_conversion())


def test_cancellation_bounds_cleanup_and_closes_stalled_pipes(tmp_path, monkeypatch):
    started = threading.Event()
    released = threading.Event()

    class Stream:
        closed = False
        def close(self):
            self.closed = True
            released.set()

    class Process:
        def __init__(self):
            self.stdout = Stream()
            self.stderr = Stream()
            self.killed = False
            self.returncode = 0
        def communicate(self):
            started.set()
            released.wait()
            return b"", b""
        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(ffmpeg, "_CLEANUP_TIMEOUT_SECONDS", 0.1)
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic audio")

    async def cancel_conversion():
        task = asyncio.create_task(convert_to_mp3(str(source), str(tmp_path / "out.mp3")))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set(), "communication worker started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(cancel_conversion())
    assert process.killed
    assert process.stdout.closed and process.stderr.closed


def test_cancellation_never_reads_result_from_noncooperative_communication(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class Process:
        stdout = stderr = None
        returncode = -9
        killed = False
        def communicate(self):
            started.set()
            release.wait()
            return b"", b""
        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: process)
    async def wait(communication, timeout):
        if timeout == float("inf"):
            while not started.is_set():
                await asyncio.sleep(0.005)
            await asyncio.Future()
        return False

    monkeypatch.setattr(ffmpeg, "_wait_for_communication", wait)
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic audio")

    async def cancel_conversion():
        task = asyncio.create_task(convert_to_mp3(str(source), str(tmp_path / "out.mp3")))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(cancel_conversion())
    assert process.killed


def test_noncooperative_daemon_worker_does_not_keep_python_process_alive(tmp_path):
    code = """
import asyncio, threading
import bot.transcription.ffmpeg as ffmpeg

started = threading.Event()
release = threading.Event()
class Process:
    stdout = stderr = None
    returncode = -9
    def communicate(self):
        started.set()
        release.wait()
        return b'', b''
    def kill(self):
        pass
ffmpeg.subprocess.Popen = lambda *args, **kwargs: Process()
async def wait(future, timeout):
    if timeout == float('inf'):
        while not started.is_set():
            await asyncio.sleep(.005)
        await asyncio.Future()
    return False
ffmpeg._wait_for_communication = wait
async def main():
    task = asyncio.create_task(ffmpeg.convert_to_mp3('source', 'target'))
    while not started.is_set():
        await asyncio.sleep(.005)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
asyncio.run(main())
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=2)

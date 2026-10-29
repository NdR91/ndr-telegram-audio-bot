"""FFmpeg conversion primitive used by the V2 runner."""

import asyncio
from concurrent.futures import Future
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)
_CLEANUP_TIMEOUT_SECONDS = 2.0


async def _wait_for_communication(communication, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not communication.done() and loop.time() < deadline:
        await asyncio.sleep(0.025)
    return communication.done()


async def convert_to_mp3(src_path: str, dst_path: str) -> None:
    process = subprocess.Popen(
        ["ffmpeg", "-y", "-i", src_path, "-vn", "-ar", "44100", "-ac", "2",
         "-b:a", "192k", dst_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    communication = Future()

    def communicate():
        try:
            communication.set_result(process.communicate())
        except BaseException as error:
            communication.set_exception(error)

    worker = threading.Thread(target=communicate, name="ffmpeg-communicate", daemon=True)
    worker.start()
    try:
        await _wait_for_communication(communication, float("inf"))
        _stdout, stderr = communication.result()
    except asyncio.CancelledError:
        try:
            process.kill()
        except OSError:
            pass
        finished = await _wait_for_communication(
            communication, _CLEANUP_TIMEOUT_SECONDS / 2
        )
        if not finished:
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            finished = await _wait_for_communication(
                communication, _CLEANUP_TIMEOUT_SECONDS / 2
            )
        if not finished:
            logger.error("FFmpeg cleanup exceeded its bound; a communication worker may remain active")
        if communication.done():
            try:
                communication.result()
            except Exception:
                pass
        raise
    if process.returncode != 0:
        logger.error("FFmpeg conversion failed (details omitted)")
        raise RuntimeError("FFmpeg conversion failed")

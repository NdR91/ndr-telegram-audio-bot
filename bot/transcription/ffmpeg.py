"""FFmpeg conversion primitive used by the V2 runner."""

import asyncio
from asyncio.subprocess import PIPE
import logging

logger = logging.getLogger(__name__)


async def convert_to_mp3(src_path: str, dst_path: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", src_path, "-vn", "-ar", "44100", "-ac", "2",
        "-b:a", "192k", dst_path, stdout=PIPE, stderr=PIPE,
    )
    try:
        _stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await process.communicate()
        except Exception:
            pass
        raise
    if process.returncode != 0:
        logger.error("FFmpeg conversion failed (details omitted)")
        raise RuntimeError("FFmpeg conversion failed")

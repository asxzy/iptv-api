# utils/ffmpeg/limits.py
"""
Process-wide bounds for ffmpeg / ffprobe subprocesses.

Two safeguards against the intermittent multi-GB memory spikes that OOM-kill the
scan during warm-mode probing:

INPUT_BOUND_ARGS       - caps how much a single process buffers while opening a
                         stream (probesize / analyzeduration) and how long it
                         blocks on a stalled socket (rw_timeout). Without these,
                         ffprobe/ffmpeg on a high-bitrate or trickling live
                         stream can balloon for the full wall-clock timeout.

get_ffmpeg_semaphore() - one gate every async ffmpeg/ffprobe spawn acquires, so
                         the number of concurrent native processes (and thus
                         their combined memory) stays bounded regardless of how
                         many speed-test / deep-probe tasks are in flight.
"""
import asyncio

from utils.config import config

# probesize: bytes; analyzeduration & rw_timeout: microseconds.
# Placed as input options (before -i / the url) so they apply to the stream.
INPUT_BOUND_ARGS = [
    "-probesize", "2000000",
    "-analyzeduration", "2000000",
    "-rw_timeout", "5000000",
]

_ffmpeg_semaphore = None


def get_ffmpeg_semaphore() -> asyncio.Semaphore:
    """
    Lazily-created, process-wide gate on concurrent ffmpeg/ffprobe spawns.

    Created on first use inside the running event loop (the scan uses a single
    long-lived loop) and reused across scheduled runs. Independent of the
    per-phase task semaphores, so it caps total native subprocess memory even
    when several test tasks each want to probe at once.
    """
    global _ffmpeg_semaphore
    if _ffmpeg_semaphore is None:
        _ffmpeg_semaphore = asyncio.Semaphore(max(1, config.ffmpeg_concurrency))
    return _ffmpeg_semaphore

# utils/ffmpeg/deep_probe.py
"""
ffmpeg-based deep-probe detectors for fake quality.

measure_keep_ratio  -> fraction of non-duplicate frames (mpdecimate), exposes
                       frame-duplication fps fakery.
measure_upscale_ssim -> mean SSIM of a downscale-then-upscale round-trip, exposes
                        resolution upscaling.

Both fail open: on timeout / decode error / non-zero exit / missing ffmpeg they
return None, and the caller leaves the corresponding authenticity factor at 1.0.
"""
import asyncio
import re

_MPDECIMATE_DECISION = re.compile(r"\]\s+(keep|drop)\b")


def _parse_mpdecimate_keep_ratio(stderr: str):
    """kept / (kept + dropped) from mpdecimate debug output, or None if no decisions."""
    keep = 0
    drop = 0
    for m in _MPDECIMATE_DECISION.finditer(stderr or ""):
        if m.group(1) == "keep":
            keep += 1
        else:
            drop += 1
    total = keep + drop
    if total == 0:
        return None
    return keep / total


def _header_args(headers: dict) -> list:
    if not headers:
        return []
    header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return ["-headers", header_str]


async def _run(args: list, timeout: int):
    """Run ffmpeg, return combined stderr text, or None on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (FileNotFoundError, Exception):
        return None
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (err or b"").decode("utf-8", "replace")
    except (asyncio.TimeoutError, Exception):
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None


async def measure_keep_ratio(url, headers=None, sample_seconds=4, timeout=15):
    """Fraction of non-duplicate frames in the first `sample_seconds`. None on failure."""
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "debug",
        *_header_args(headers),
        "-t", str(sample_seconds), "-i", url,
        "-an", "-vf", "mpdecimate", "-f", "null", "-",
    ]
    out = await _run(args, timeout)
    if out is None:
        return None
    return _parse_mpdecimate_keep_ratio(out)

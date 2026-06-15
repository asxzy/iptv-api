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


from utils.tools import get_resolution_value

_SSIM_ALL = re.compile(r"All:([0-9]*\.?[0-9]+)")


def _parse_ssim_all(stderr: str):
    """Last SSIM All:<value> from ffmpeg ssim filter output, or None."""
    matches = _SSIM_ALL.findall(stderr or "")
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


async def measure_upscale_ssim(url, declared_resolution, lower_resolution,
                               headers=None, sample_seconds=4, timeout=15):
    """
    Mean SSIM between native frames and their downscale-to-`lower_resolution`-then-
    upscale-back round-trip. High SSIM => no real detail beyond the lower tier =>
    upscaled. Returns None on failure or when resolutions are unparseable.
    """
    px = get_resolution_value(declared_resolution)
    low_px = get_resolution_value(lower_resolution)
    if px <= 0 or low_px <= 0:
        return None
    m = re.search(r"(\d+)[xX*](\d+)", declared_resolution or "")
    lm = re.search(r"(\d+)[xX*](\d+)", lower_resolution or "")
    if not m or not lm:
        return None
    w, h = m.group(1), m.group(2)
    lw, lh = lm.group(1), lm.group(2)
    filtergraph = (
        f"[0:v]split=2[a][b];"
        f"[b]scale={lw}:{lh},scale={w}:{h}[c];"
        f"[a][c]ssim"
    )
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        *_header_args(headers),
        "-t", str(sample_seconds), "-i", url,
        "-an", "-lavfi", filtergraph, "-f", "null", "-",
    ]
    out = await _run(args, timeout)
    if out is None:
        return None
    return _parse_ssim_all(out)

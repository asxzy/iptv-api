# utils/authenticity.py
"""
Pure anti-fake authenticity helpers.

Turn raw detector signals (mpdecimate keep-ratio, upscale round-trip SSIM) into
0-1 authenticity factors that scale the resolution / fps quality credit. No I/O.
"""
from utils.scoring import resolution_score, fps_score
from utils.tools import get_resolution_value

# Representative resolution string per quality tier, highest -> lowest.
_TIER_REPRESENTATIVES = (
    "3840x2160",
    "2560x1440",
    "1920x1080",
    "1280x720",
    "854x480",
    "640x360",
)


def lower_resolution_tier(resolution):
    """Representative resolution string one tier below `resolution`.

    Returns None if `resolution` is unknown/unparseable or already the lowest tier.
    """
    px = get_resolution_value(resolution)
    if px <= 0:
        return None
    for i, rep in enumerate(_TIER_REPRESENTATIVES):
        if px >= get_resolution_value(rep):
            return _TIER_REPRESENTATIVES[i + 1] if i + 1 < len(_TIER_REPRESENTATIVES) else None
    return None


def fps_authenticity(declared_fps, keep_ratio):
    """
    0-1 frame-rate authenticity from mpdecimate keep-ratio.

    effective_fps = declared_fps * keep_ratio; the factor is the ratio of the fps
    credit at the effective vs declared rate. Unknown fps or missing measurement
    -> 1.0 (no penalty).
    """
    if declared_fps is None or keep_ratio is None:
        return 1.0
    try:
        declared = float(declared_fps)
    except (TypeError, ValueError):
        return 1.0
    if declared <= 0:
        return 1.0
    denom = fps_score(declared)
    if denom <= 0:
        return 1.0
    effective = declared * max(0.0, min(1.0, keep_ratio))
    return max(0.0, min(1.0, fps_score(effective) / denom))

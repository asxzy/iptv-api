# utils/scoring.py
"""
Pure media-ranking scoring functions.

A stream's final score blends a Quality component (Q) and a Loadability
component (L). Every sub-score has a neutral fallback so missing signals
neither help nor hurt ranking. No config or I/O here: all tunables arrive
through a weights dict.
"""
from utils.tools import get_resolution_value

NEUTRAL = 0.5

_RESOLUTION_TIERS = (
    (3840 * 2160, 1.0),
    (2560 * 1440, 0.85),
    (1920 * 1080, 0.7),
    (1280 * 720, 0.5),
    (854 * 480, 0.3),
)


def resolution_score(resolution):
    """Map a resolution string to a 0-1 tier score; unknown -> NEUTRAL."""
    px = get_resolution_value(resolution)
    if px <= 0:
        return NEUTRAL
    for threshold, score in _RESOLUTION_TIERS:
        if px >= threshold:
            return score
    return 0.15


_FPS_NEUTRAL = 0.6


def fps_score(fps):
    """Map fps to a 0-1 score; unknown/invalid -> _FPS_NEUTRAL."""
    if fps is None:
        return _FPS_NEUTRAL
    try:
        f = float(fps)
    except (TypeError, ValueError):
        return _FPS_NEUTRAL
    if f <= 0:
        return _FPS_NEUTRAL
    if f >= 50:
        return 1.0
    if f >= 29:
        return 0.7
    if f >= 24:
        return 0.55
    return 0.4

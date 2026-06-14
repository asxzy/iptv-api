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


# h264-equivalent bitrate multipliers: how much bitrate a codec needs for the
# same visual quality. <1 means more efficient than h264.
CODEC_EFFICIENCY = {
    "h264": 1.0, "avc": 1.0, "avc1": 1.0,
    "hevc": 0.5, "h265": 0.5, "hev1": 0.5, "hvc1": 0.5,
    "av1": 0.4, "av01": 0.4,
    "vp9": 0.6,
    "mpeg2video": 2.0, "mpeg2": 2.0,
}

DEFAULT_WEIGHTS = {
    # top-level blend
    "w_quality": 0.5, "w_loadability": 0.5,
    # quality sub-weights (sum to 1.0)
    "w_res": 0.5, "w_enc": 0.35, "w_fps": 0.15,
    # loadability sub-weights (sum to 1.0)
    "w_start": 0.3, "w_margin": 0.7,
    # thresholds
    "delay_max": 3000.0,        # ms; delay >= this -> startup 0
    "margin_target": 2.0,       # throughput/bitrate at which margin saturates to 1
    "target_bpp": 0.1,          # h264-equivalent bits-per-pixel-per-frame "good enough"
    "ref_throughput_mbps": 10.0,  # bitrate-unknown fallback saturation point
}


def encoding_adequacy(resolution, bitrate_bps, fps, video_codec, weights=DEFAULT_WEIGHTS):
    """
    Saturating 0-1 measure of how richly the source is encoded for its
    resolution, normalized for codec efficiency. Missing bitrate/resolution
    -> NEUTRAL (not punished).
    """
    px = get_resolution_value(resolution)
    if not bitrate_bps or bitrate_bps <= 0 or px <= 0:
        return NEUTRAL
    try:
        f = float(fps) if fps else 25.0
    except (TypeError, ValueError):
        f = 25.0
    if f <= 0:
        f = 25.0
    bpp = bitrate_bps / (px * f)
    codec_factor = CODEC_EFFICIENCY.get((video_codec or "").lower(), 1.0)
    adjusted_bpp = bpp / codec_factor
    return min(1.0, adjusted_bpp / weights["target_bpp"])


def quality_score(result, weights=DEFAULT_WEIGHTS):
    """Blend resolution, encoding adequacy, and fps into a 0-1 quality score."""
    resolution = result.get("resolution")
    bitrate = result.get("bitrate")
    fps = result.get("fps")
    codec = result.get("video_codec")
    rs = resolution_score(resolution)
    es = encoding_adequacy(resolution, bitrate, fps, codec, weights)
    fs = fps_score(fps)
    return weights["w_res"] * rs + weights["w_enc"] * es + weights["w_fps"] * fs

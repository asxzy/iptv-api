"""
Tests for utils.scoring.

Run via:
    python -m pytest tests/test_scoring.py
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.scoring import resolution_score, NEUTRAL


def test_resolution_score_tiers():
    assert resolution_score("3840x2160") == 1.0
    assert resolution_score("1920x1080") == 0.7
    assert resolution_score("1280x720") == 0.5
    assert resolution_score("640x360") == 0.15


def test_resolution_score_unknown_is_neutral():
    assert resolution_score(None) == NEUTRAL
    assert resolution_score("") == NEUTRAL
    assert resolution_score("garbage") == NEUTRAL


def test_fps_score_tiers():
    from utils.scoring import fps_score
    assert fps_score(60) == 1.0
    assert fps_score(30) == 0.7
    assert fps_score(25) == 0.55
    assert fps_score(15) == 0.4


def test_fps_score_unknown_is_neutral_default():
    from utils.scoring import fps_score
    assert fps_score(None) == 0.6
    assert fps_score("not-a-number") == 0.6
    assert fps_score(0) == 0.6


def test_encoding_adequacy_saturates_at_good_bitrate():
    from utils.scoring import encoding_adequacy, DEFAULT_WEIGHTS
    # 1080p (2.07M px) @ 25fps, h264. target_bpp default 0.1.
    # bitrate giving bpp == target: 0.1 * 2073600 * 25 = 5,184,000 bps
    good = encoding_adequacy("1920x1080", 5_184_000, 25, "h264", DEFAULT_WEIGHTS)
    assert good == 1.0
    # half that bitrate -> half adequacy
    half = encoding_adequacy("1920x1080", 2_592_000, 25, "h264", DEFAULT_WEIGHTS)
    assert abs(half - 0.5) < 1e-6


def test_encoding_adequacy_hevc_worth_more_per_bit():
    from utils.scoring import encoding_adequacy, DEFAULT_WEIGHTS
    # hevc factor 0.5 -> half the bitrate scores like full-bitrate h264
    hevc = encoding_adequacy("1920x1080", 2_592_000, 25, "hevc", DEFAULT_WEIGHTS)
    assert hevc == 1.0


def test_encoding_adequacy_missing_signal_is_neutral():
    from utils.scoring import encoding_adequacy, DEFAULT_WEIGHTS, NEUTRAL
    assert encoding_adequacy("1920x1080", None, 25, "h264", DEFAULT_WEIGHTS) == NEUTRAL
    assert encoding_adequacy(None, 5_000_000, 25, "h264", DEFAULT_WEIGHTS) == NEUTRAL
    assert encoding_adequacy("1920x1080", 0, 25, "h264", DEFAULT_WEIGHTS) == NEUTRAL

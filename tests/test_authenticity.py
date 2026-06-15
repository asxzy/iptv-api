"""
Tests for anti-fake authenticity detection.

Run via:
    python -m pytest tests/test_authenticity.py
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.authenticity import lower_resolution_tier


def test_lower_resolution_tier_steps_down_one_tier():
    assert lower_resolution_tier("1920x1080") == "1280x720"
    assert lower_resolution_tier("1280x720") == "854x480"
    assert lower_resolution_tier("3840x2160") == "2560x1440"
    assert lower_resolution_tier("854x480") == "640x360"


def test_lower_resolution_tier_lowest_and_unknown_return_none():
    assert lower_resolution_tier("640x360") is None
    assert lower_resolution_tier(None) is None
    assert lower_resolution_tier("garbage") is None


def test_bpp_prior_bounds():
    from utils.scoring import bpp_prior, DEFAULT_WEIGHTS
    assert bpp_prior(0.5, DEFAULT_WEIGHTS) == 1.0
    assert bpp_prior(0.3, DEFAULT_WEIGHTS) == 1.0
    assert abs(bpp_prior(0.0, DEFAULT_WEIGHTS) - 0.7) < 1e-9
    assert abs(bpp_prior(0.15, DEFAULT_WEIGHTS) - 0.85) < 1e-9


def test_bpp_prior_neutral_adequacy_is_one():
    from utils.scoring import bpp_prior, DEFAULT_WEIGHTS, NEUTRAL
    assert bpp_prior(NEUTRAL, DEFAULT_WEIGHTS) == 1.0


def test_quality_score_uses_explicit_authenticity_factors():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    base = {"resolution": "1920x1080", "bitrate": 5_000_000, "fps": 50,
            "video_codec": "h264"}
    honest = quality_score(base, DEFAULT_WEIGHTS)
    faked = quality_score({**base, "a_res": 0.71, "a_fps": 0.55}, DEFAULT_WEIGHTS)
    assert faked < honest


def test_quality_score_fake_1080p_ranks_below_honest_720p():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    fake = quality_score({"resolution": "1920x1080", "bitrate": 2_500_000,
                          "fps": 25, "video_codec": "h264", "a_res": 0.714,
                          "a_fps": 1.0}, DEFAULT_WEIGHTS)
    honest_720 = quality_score({"resolution": "1280x720", "bitrate": 3_000_000,
                                "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert honest_720 > fake


def test_quality_score_cheap_prior_applies_when_no_deep_signal():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    starved = quality_score({"resolution": "1920x1080", "bitrate": 200_000,
                             "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    rich = quality_score({"resolution": "1920x1080", "bitrate": 5_000_000,
                          "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert starved < rich


def test_quality_score_no_signals_unchanged_factors_are_one():
    from utils.scoring import quality_score, resolution_score, fps_score, encoding_adequacy, DEFAULT_WEIGHTS
    r = {"resolution": "1280x720", "fps": 25, "video_codec": "h264"}
    w = DEFAULT_WEIGHTS
    expected = (w["w_res"] * resolution_score("1280x720")
                + w["w_enc"] * encoding_adequacy("1280x720", None, 25, "h264", w)
                + w["w_fps"] * fps_score(25))
    assert abs(quality_score(r, w) - expected) < 1e-9


def test_fps_authenticity_frame_dup_collapses_credit():
    from utils.authenticity import fps_authenticity
    from utils.scoring import fps_score
    a = fps_authenticity(50, 0.5)
    assert abs(a - fps_score(25) / fps_score(50)) < 1e-9


def test_fps_authenticity_genuine_and_unknown():
    from utils.authenticity import fps_authenticity
    assert fps_authenticity(50, 1.0) == 1.0
    assert fps_authenticity(None, 0.5) == 1.0
    assert fps_authenticity(50, None) == 1.0
    assert 0.0 <= fps_authenticity(50, 0.1) <= 1.0

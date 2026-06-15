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


def test_resolution_authenticity_upscale_high_ssim_collapses_credit():
    from utils.authenticity import resolution_authenticity, lower_resolution_tier
    from utils.scoring import resolution_score
    a = resolution_authenticity("1920x1080", 0.99, 0.96, 0.985)
    ratio = resolution_score(lower_resolution_tier("1920x1080")) / resolution_score("1920x1080")
    assert abs(a - ratio) < 1e-9


def test_resolution_authenticity_genuine_low_ssim_is_one():
    from utils.authenticity import resolution_authenticity
    assert resolution_authenticity("1920x1080", 0.95, 0.96, 0.985) == 1.0


def test_resolution_authenticity_unknown_or_lowest_tier_is_one():
    from utils.authenticity import resolution_authenticity
    assert resolution_authenticity(None, 0.99, 0.96, 0.985) == 1.0
    assert resolution_authenticity("640x360", 0.99, 0.96, 0.985) == 1.0
    assert resolution_authenticity("1920x1080", None, 0.96, 0.985) == 1.0


def test_config_has_authenticity_settings():
    from utils.config import config
    w = config.ranking_weights
    assert "bpp_prior_floor" in w and "bpp_prior_knee" in w
    a = config.authenticity_config
    assert set(a.keys()) == {"ssim_low", "ssim_high"}
    assert isinstance(config.open_deep_probe, bool)
    assert isinstance(config.deep_probe_top_n, int)
    assert isinstance(config.deep_probe_sample_seconds, int)
    assert isinstance(config.deep_probe_timeout, int)


def test_parse_mpdecimate_keep_ratio():
    from utils.ffmpeg.deep_probe import _parse_mpdecimate_keep_ratio
    stderr = (
        "[Parsed_mpdecimate_0 @ 0x55] keep pts:1 pts_time:0.04 drop_count:-1\n"
        "[Parsed_mpdecimate_0 @ 0x55] drop pts:2 pts_time:0.08 drop_count:1\n"
        "[Parsed_mpdecimate_0 @ 0x55] keep pts:3 pts_time:0.12 drop_count:-1\n"
        "[Parsed_mpdecimate_0 @ 0x55] drop pts:4 pts_time:0.16 drop_count:1\n"
        "frame=    2 fps=0.0 q=-0.0 Lsize=N/A time=00:00:00.16 bitrate=N/A\n"
    )
    assert abs(_parse_mpdecimate_keep_ratio(stderr) - 0.5) < 1e-9


def test_parse_mpdecimate_keep_ratio_no_frames_returns_none():
    from utils.ffmpeg.deep_probe import _parse_mpdecimate_keep_ratio
    assert _parse_mpdecimate_keep_ratio("no decision lines here") is None


def test_parse_ssim_all():
    from utils.ffmpeg.deep_probe import _parse_ssim_all
    stderr = (
        "[Parsed_ssim_2 @ 0x55] SSIM Y:0.990 U:0.992 V:0.991 All:0.990 (20.1)\n"
        "[Parsed_ssim_2 @ 0x55] SSIM All:0.987 (18.9)\n"
    )
    assert abs(_parse_ssim_all(stderr) - 0.987) < 1e-9


def test_parse_ssim_all_missing_returns_none():
    from utils.ffmpeg.deep_probe import _parse_ssim_all
    assert _parse_ssim_all("no ssim here") is None


def test_deep_probe_pass_mutates_top_n(monkeypatch):
    import asyncio
    import utils.channel as channel

    async def fake_keep(url, headers=None, sample_seconds=4, timeout=15):
        return 0.5 if "dup" in url else 1.0

    async def fake_ssim(url, declared_resolution, lower_resolution, headers=None,
                        sample_seconds=4, timeout=15):
        return 0.99 if "upscale" in url else 0.95

    # measure_* are module-level names in channel (imported), so patchable here.
    # config.open_deep_probe / deep_probe_top_n are properties — do NOT setattr them;
    # rely on the defaults written to config.ini (open_deep_probe=True, top_n=5).
    monkeypatch.setattr(channel, "measure_keep_ratio", fake_keep)
    monkeypatch.setattr(channel, "measure_upscale_ssim", fake_ssim)

    grouped = {"cat": {"ch": [
        {"url": "http://upscale/1", "delay": 100, "speed": 5.0,
         "resolution": "1920x1080", "fps": 25, "bitrate": 5_000_000},
        {"url": "http://dup/2", "delay": 100, "speed": 5.0,
         "resolution": "1920x1080", "fps": 50, "bitrate": 5_000_000},
        {"url": "http://honest/3", "delay": 100, "speed": 5.0,
         "resolution": "1280x720", "fps": 25, "bitrate": 3_000_000},
    ]}}
    asyncio.run(channel.deep_probe_pass(grouped))

    from utils.scoring import fps_score
    up = grouped["cat"]["ch"][0]
    dup = grouped["cat"]["ch"][1]
    honest = grouped["cat"]["ch"][2]
    assert up["a_res"] < 1.0
    assert abs(dup["a_fps"] - fps_score(25) / fps_score(50)) < 1e-9
    assert honest.get("a_res", 1.0) == 1.0

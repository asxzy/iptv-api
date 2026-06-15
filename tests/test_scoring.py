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


def test_quality_score_in_unit_range_and_resolution_dominates():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    hi = quality_score({"resolution": "3840x2160", "bitrate": 20_000_000,
                        "fps": 60, "video_codec": "hevc"}, DEFAULT_WEIGHTS)
    lo = quality_score({"resolution": "640x360", "bitrate": 500_000,
                        "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert 0.0 <= lo < hi <= 1.0


def test_quality_score_fake_1080p_loses_to_honest_720p():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    # under-encoded 1080p: bitrate far below "good enough" for its pixels
    fake_1080 = quality_score({"resolution": "1920x1080", "bitrate": 600_000,
                              "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    # honest 720p: well-encoded
    honest_720 = quality_score({"resolution": "1280x720", "bitrate": 4_000_000,
                               "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert honest_720 > fake_1080


def test_margin_score_saturates_and_floors():
    from utils.scoring import margin_score, DEFAULT_WEIGHTS
    # bitrate 8 Mbps -> 1 MB/s. margin_target 2.0.
    # speed 2 MB/s == 16 Mbps -> margin 2.0 -> saturates to 1.0
    assert margin_score({"speed": 2.0, "bitrate": 8_000_000}, DEFAULT_WEIGHTS) == 1.0
    # speed 1 MB/s == 8 Mbps -> margin 1.0 -> floor 0.0
    assert margin_score({"speed": 1.0, "bitrate": 8_000_000}, DEFAULT_WEIGHTS) == 0.0
    # midpoint margin 1.5 -> 0.5
    assert abs(margin_score({"speed": 1.5, "bitrate": 8_000_000}, DEFAULT_WEIGHTS) - 0.5) < 1e-6


def test_margin_score_bitrate_unknown_uses_throughput():
    from utils.scoring import margin_score, DEFAULT_WEIGHTS
    # no bitrate: rank by raw throughput, ref 10 Mbps. 0.625 MB/s == 5 Mbps -> 0.5
    assert abs(margin_score({"speed": 0.625}, DEFAULT_WEIGHTS) - 0.5) < 1e-6
    # infinite speed (ipv6 default) -> 1.0
    assert margin_score({"speed": float("inf")}, DEFAULT_WEIGHTS) == 1.0


def test_loadability_startup_and_neutral():
    from utils.scoring import loadability_score, DEFAULT_WEIGHTS, NEUTRAL
    # delay missing/-1 -> startup NEUTRAL; bitrate unknown, speed 0 -> margin 0
    val = loadability_score({"delay": -1, "speed": 0}, DEFAULT_WEIGHTS)
    assert abs(val - (0.3 * NEUTRAL + 0.7 * 0.0)) < 1e-6
    # lower delay scores higher (all else equal)
    fast = loadability_score({"delay": 300, "speed": 1.25}, DEFAULT_WEIGHTS)
    slow = loadability_score({"delay": 2700, "speed": 1.25}, DEFAULT_WEIGHTS)
    assert fast > slow


def test_is_sustainable_gate():
    from utils.scoring import is_sustainable, DEFAULT_WEIGHTS
    assert is_sustainable({"speed": 1.0, "bitrate": 4_000_000}, DEFAULT_WEIGHTS) is True   # 8>=4 Mbps
    assert is_sustainable({"speed": 0.25, "bitrate": 8_000_000}, DEFAULT_WEIGHTS) is False  # 2<8 Mbps
    assert is_sustainable({"speed": 0.1}, DEFAULT_WEIGHTS) is True  # bitrate unknown -> don't gate
    assert is_sustainable({"speed": float("inf"), "bitrate": 8_000_000}, DEFAULT_WEIGHTS) is True


def test_compute_score_higher_bitrate_wins_at_equal_resolution():
    from utils.scoring import compute_score, DEFAULT_WEIGHTS
    base = {"resolution": "1920x1080", "fps": 25, "video_codec": "h264",
            "delay": 500, "speed": 5.0}
    rich = compute_score({**base, "bitrate": 6_000_000}, DEFAULT_WEIGHTS)
    poor = compute_score({**base, "bitrate": 1_500_000}, DEFAULT_WEIGHTS)
    assert rich > poor


def test_compute_score_all_missing_signals_ranks_by_speed():
    from utils.scoring import compute_score, DEFAULT_WEIGHTS
    # only speed differs; resolution/bitrate/fps/codec absent, delay equal
    fast = compute_score({"delay": 500, "speed": 3.0}, DEFAULT_WEIGHTS)
    slow = compute_score({"delay": 500, "speed": 1.0}, DEFAULT_WEIGHTS)
    assert fast > slow


def test_compute_score_returns_unit_range():
    from utils.scoring import compute_score, DEFAULT_WEIGHTS
    s = compute_score({"resolution": "1920x1080", "bitrate": 5_000_000,
                      "fps": 30, "video_codec": "h264", "delay": 400,
                      "speed": 4.0}, DEFAULT_WEIGHTS)
    assert 0.0 <= s <= 1.0


def test_config_ranking_weights_has_all_keys():
    from utils.config import config
    from utils.scoring import DEFAULT_WEIGHTS
    w = config.ranking_weights
    assert set(w.keys()) == set(DEFAULT_WEIGHTS.keys())
    for k in DEFAULT_WEIGHTS:
        assert isinstance(w[k], float)


def test_get_sort_result_orders_by_blended_score():
    from utils.speed import get_sort_result
    rich = {"url": "rich", "ipv_type": "ipv4", "delay": 500, "speed": 8.0,
            "resolution": "1920x1080", "bitrate": 6_000_000, "fps": 25,
            "video_codec": "h264"}
    poor = {"url": "poor", "ipv_type": "ipv4", "delay": 500, "speed": 8.0,
            "resolution": "1920x1080", "bitrate": 1_200_000, "fps": 25,
            "video_codec": "h264"}
    out = get_sort_result([poor, rich], filter_speed=False, filter_resolution=False)
    assert [r["url"] for r in out] == ["rich", "poor"]


def test_get_sort_result_drops_dead_streams():
    from utils.speed import get_sort_result
    dead = {"url": "dead", "ipv_type": "ipv4", "delay": -1, "speed": 0}
    live = {"url": "live", "ipv_type": "ipv4", "delay": 500, "speed": 5.0}
    out = get_sort_result([dead, live], filter_speed=False, filter_resolution=False)
    assert [r["url"] for r in out] == ["live"]


def test_get_sort_result_gates_unsustainable_when_not_supply():
    from utils.speed import get_sort_result
    starves = {"url": "starves", "ipv_type": "ipv4", "delay": 500, "speed": 0.25,
               "bitrate": 8_000_000, "resolution": "1920x1080"}
    ok = {"url": "ok", "ipv_type": "ipv4", "delay": 500, "speed": 3.0,
          "bitrate": 8_000_000, "resolution": "1920x1080"}
    out = get_sort_result([starves, ok], supply=False, filter_speed=False,
                          filter_resolution=False)
    assert [r["url"] for r in out] == ["ok"]
    out2 = get_sort_result([starves, ok], supply=True, filter_speed=False,
                           filter_resolution=False)
    assert {r["url"] for r in out2} == {"ok", "starves"}


def test_get_sort_result_speed_breaks_ties():
    from utils.speed import get_sort_result
    a = {"url": "a", "ipv_type": "ipv4", "delay": 500, "speed": 2.0}
    b = {"url": "b", "ipv_type": "ipv4", "delay": 500, "speed": 9.0}
    out = get_sort_result([a, b], filter_speed=False, filter_resolution=False)
    assert [r["url"] for r in out] == ["b", "a"]


def test_get_avg_result_aggregates_bitrate_and_fps():
    from utils.speed import get_avg_result
    avg = get_avg_result([
        {"speed": 4.0, "delay": 100, "resolution": "1280x720",
         "bitrate": 2_000_000, "fps": 25, "video_codec": "h264", "audio_codec": "aac"},
        {"speed": 6.0, "delay": 200, "resolution": "1920x1080",
         "bitrate": 4_000_000, "fps": 50, "video_codec": None, "audio_codec": None},
    ])
    assert avg["resolution"] == "1920x1080"     # max resolution
    assert abs(avg["bitrate"] - 3_000_000) < 1  # mean of present bitrates
    assert avg["fps"] == 50                      # max fps
    assert avg["video_codec"] == "h264"          # first present
    assert avg["audio_codec"] == "aac"


def test_parse_probe_data_extracts_bitrate():
    from utils.ffmpeg.probe import _parse_probe_data
    meta = _parse_probe_data({
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920,
             "height": 1080, "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"bit_rate": "5000000"},
    })
    assert meta["resolution"] == "1920x1080"
    assert meta["bitrate"] == 5000000.0


def test_get_avg_result_coerces_string_fps():
    from utils.speed import get_avg_result
    avg = get_avg_result([
        {"speed": 4.0, "delay": 100, "resolution": "1280x720", "fps": "25"},
        {"speed": 6.0, "delay": 200, "resolution": "1280x720", "fps": "50.0"},
        {"speed": 5.0, "delay": 150, "resolution": "1280x720", "fps": "bad"},
    ])
    assert avg["fps"] == 50.0

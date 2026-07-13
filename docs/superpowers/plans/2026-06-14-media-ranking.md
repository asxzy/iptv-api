# Media Ranking Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pure-speed sort with a tunable quality + loadability blended score, plus a two-mode (fast-scan / full-probe) signal-collection strategy.

**Architecture:** A new pure module `utils/scoring.py` holds the scoring formula (fully unit-testable on plain dicts). `utils/speed.py` feeds it real signals — adding encoded-bitrate computation during the segment download and mode-gated ffprobe — and `get_sort_result` sorts by the blended score with a sustainability gate. New `config.ini` keys expose all weights/thresholds.

**Tech Stack:** Python 3.13, pytest, aiohttp, m3u8, ffprobe.

---

## File Structure

- **Create** `utils/scoring.py` — pure scoring functions + `DEFAULT_WEIGHTS`. No I/O, no config import. Depends only on `utils.tools.get_resolution_value`.
- **Create** `tests/test_scoring.py` — unit tests for every scoring function and the integration scenarios from the spec.
- **Modify** `utils/types.py` — add `bitrate` to `TestResult`.
- **Modify** `utils/config.py` — add ranking weight/threshold properties + a `ranking_weights` bundle and `open_full_probe` override.
- **Modify** `config/config.ini` — add `[Settings]` keys with defaults.
- **Modify** `utils/speed.py` — compute encoded bitrate in `get_result`; aggregate bitrate/fps/codecs in `get_avg_result`; mode-gated ffprobe; rewrite `get_sort_result` sort key + sustainability gate.
- **Modify** `utils/ffmpeg/probe.py` — extract `format.bit_rate` in `_parse_probe_data`.

All tests run with: `python -m pytest tests/test_scoring.py -v` (repo root on `sys.path` via the test header, matching existing tests).

---

## Task 1: Scoring module skeleton + resolution_score

**Files:**
- Create: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.scoring'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add resolution_score tier mapping"
```

---

## Task 2: fps_score

**Files:**
- Modify: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_fps_score_tiers -v`
Expected: FAIL with `ImportError: cannot import name 'fps_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `utils/scoring.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add fps_score tier mapping"
```

---

## Task 3: encoding_adequacy (bitrate + codec)

**Files:**
- Modify: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_encoding_adequacy_saturates_at_good_bitrate -v`
Expected: FAIL with `ImportError: cannot import name 'encoding_adequacy'`

- [ ] **Step 3: Write minimal implementation**

Append to `utils/scoring.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add encoding_adequacy with codec normalization"
```

---

## Task 4: quality_score (Q)

**Files:**
- Modify: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_quality_score_in_unit_range_and_resolution_dominates -v`
Expected: FAIL with `ImportError: cannot import name 'quality_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `utils/scoring.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add quality_score blend"
```

---

## Task 5: margin_score, loadability_score (L), is_sustainable

**Files:**
- Modify: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_margin_score_saturates_and_floors -v`
Expected: FAIL with `ImportError: cannot import name 'margin_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `utils/scoring.py`:

```python
def _speed_mbps(result):
    speed = result.get("speed") or 0
    if speed == float("inf"):
        return float("inf")
    return speed * 8.0  # MB/s -> Mbps


def margin_score(result, weights=DEFAULT_WEIGHTS):
    """
    Throughput-over-bitrate headroom, 0-1. Saturates at margin_target,
    floors at margin 1 (cannot sustain). Bitrate unknown -> bounded raw
    throughput so faster still wins.
    """
    speed_mbps = _speed_mbps(result)
    if speed_mbps == float("inf"):
        return 1.0
    bitrate_mbps = (result.get("bitrate") or 0) / 1_000_000.0
    if bitrate_mbps <= 0:
        return min(1.0, speed_mbps / weights["ref_throughput_mbps"])
    margin = speed_mbps / bitrate_mbps
    target = weights["margin_target"]
    if target <= 1:
        return 1.0 if margin >= 1 else 0.0
    return max(0.0, min(1.0, (margin - 1.0) / (target - 1.0)))


def loadability_score(result, weights=DEFAULT_WEIGHTS):
    """Blend startup (delay) and sustain (margin) into a 0-1 loadability score."""
    delay = result.get("delay")
    if delay is None or delay < 0:
        startup = NEUTRAL
    else:
        startup = max(0.0, 1.0 - delay / weights["delay_max"])
    margin = margin_score(result, weights)
    return weights["w_start"] * startup + weights["w_margin"] * margin


def is_sustainable(result, weights=DEFAULT_WEIGHTS):
    """True if throughput can keep up with bitrate (or bitrate unknown)."""
    speed_mbps = _speed_mbps(result)
    if speed_mbps == float("inf"):
        return True
    bitrate_mbps = (result.get("bitrate") or 0) / 1_000_000.0
    if bitrate_mbps <= 0:
        return True
    return speed_mbps >= bitrate_mbps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add loadability, margin, and sustainability gate"
```

---

## Task 6: compute_score (final blend)

**Files:**
- Modify: `utils/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_compute_score_returns_unit_range -v`
Expected: FAIL with `ImportError: cannot import name 'compute_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `utils/scoring.py`:

```python
def compute_score(result, weights=DEFAULT_WEIGHTS):
    """Final 0-1 ranking score: weighted blend of quality and loadability."""
    q = quality_score(result, weights)
    l = loadability_score(result, weights)
    return weights["w_quality"] * q + weights["w_loadability"] * l
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add compute_score final blend"
```

---

## Task 7: Config keys, weights bundle, and mode override

**Files:**
- Modify: `config/config.ini`
- Modify: `utils/config.py:373` (near `resolution_speed_map`)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
def test_config_ranking_weights_has_all_keys():
    from utils.config import config
    from utils.scoring import DEFAULT_WEIGHTS
    w = config.ranking_weights
    assert set(w.keys()) == set(DEFAULT_WEIGHTS.keys())
    for k in DEFAULT_WEIGHTS:
        assert isinstance(w[k], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_config_ranking_weights_has_all_keys -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'ranking_weights'`

- [ ] **Step 3: Add config.ini keys**

Add these lines under the `[Settings]` section of `config/config.ini` (after the `resolution_speed_map` line at config.ini:116):

```ini
# 排序权重：画质与可加载性的总占比 | Ranking blend weights: quality vs loadability share
ranking_w_quality = 0.5
ranking_w_loadability = 0.5
# 画质子权重（分辨率/编码充分度/帧率） | Quality sub-weights (resolution/encoding/fps)
ranking_w_res = 0.5
ranking_w_enc = 0.35
ranking_w_fps = 0.15
# 可加载性子权重（启动延迟/带宽余量） | Loadability sub-weights (startup delay/bandwidth margin)
ranking_w_start = 0.3
ranking_w_margin = 0.7
# 阈值：最大延迟(ms)、余量饱和倍数、目标每像素每帧比特、未知码率回退吞吐(Mbps)
ranking_delay_max = 3000
ranking_margin_target = 2.0
ranking_target_bpp = 0.1
ranking_ref_throughput_mbps = 10.0
# 强制全量探测（默认随历史缓存是否存在自动判定）| Force full ffprobe (default: auto by history cache)
open_full_probe = False
```

- [ ] **Step 4: Add config properties**

In `utils/config.py`, immediately after the `resolution_speed_map` property (ends at line 384), add:

```python
    @property
    def ranking_weights(self):
        getf = self.config.getfloat
        return {
            "w_quality": getf("Settings", "ranking_w_quality", fallback=0.5),
            "w_loadability": getf("Settings", "ranking_w_loadability", fallback=0.5),
            "w_res": getf("Settings", "ranking_w_res", fallback=0.5),
            "w_enc": getf("Settings", "ranking_w_enc", fallback=0.35),
            "w_fps": getf("Settings", "ranking_w_fps", fallback=0.15),
            "w_start": getf("Settings", "ranking_w_start", fallback=0.3),
            "w_margin": getf("Settings", "ranking_w_margin", fallback=0.7),
            "delay_max": getf("Settings", "ranking_delay_max", fallback=3000.0),
            "margin_target": getf("Settings", "ranking_margin_target", fallback=2.0),
            "target_bpp": getf("Settings", "ranking_target_bpp", fallback=0.1),
            "ref_throughput_mbps": getf("Settings", "ranking_ref_throughput_mbps", fallback=10.0),
        }

    @property
    def open_full_probe(self):
        return self.config.getboolean("Settings", "open_full_probe", fallback=False)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scoring.py::test_config_ranking_weights_has_all_keys -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/config.ini utils/config.py tests/test_scoring.py
git commit -m "feat(config): add ranking weights, thresholds, and full-probe override"
```

---

## Task 8: Wire compute_score into get_sort_result with sustainability gate

**Files:**
- Modify: `utils/types.py:31-40` (add `bitrate`)
- Modify: `utils/speed.py:504-537` (`get_sort_result`) and imports near `utils/speed.py:15`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Add `bitrate` to TestResult**

In `utils/types.py`, inside `class TestResult`, add after the `fps` line (line 40):

```python
    bitrate: NotRequired[int | float | None]
```

- [ ] **Step 2: Write the failing test**

```python
def test_get_sort_result_orders_by_blended_score():
    from utils.speed import get_sort_result
    # identical content, identical delay/speed; richer bitrate must rank first
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
    # 8 Mbps bitrate, only 2 Mbps (0.25 MB/s) throughput -> cannot sustain
    starves = {"url": "starves", "ipv_type": "ipv4", "delay": 500, "speed": 0.25,
               "bitrate": 8_000_000, "resolution": "1920x1080"}
    ok = {"url": "ok", "ipv_type": "ipv4", "delay": 500, "speed": 3.0,
          "bitrate": 8_000_000, "resolution": "1920x1080"}
    out = get_sort_result([starves, ok], supply=False, filter_speed=False,
                          filter_resolution=False)
    assert [r["url"] for r in out] == ["ok"]
    # supply mode keeps it
    out2 = get_sort_result([starves, ok], supply=True, filter_speed=False,
                           filter_resolution=False)
    assert {r["url"] for r in out2} == {"ok", "starves"}


def test_get_sort_result_speed_breaks_ties():
    from utils.speed import get_sort_result
    a = {"url": "a", "ipv_type": "ipv4", "delay": 500, "speed": 2.0}
    b = {"url": "b", "ipv_type": "ipv4", "delay": 500, "speed": 9.0}
    out = get_sort_result([a, b], filter_speed=False, filter_resolution=False)
    assert [r["url"] for r in out] == ["b", "a"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_get_sort_result_orders_by_blended_score -v`
Expected: FAIL — currently sorts by raw speed, so equal-speed `rich`/`poor` order is input order `["poor","rich"]`.

- [ ] **Step 4: Implement**

In `utils/speed.py`, add to the imports near line 15:

```python
from utils.scoring import compute_score, is_sustainable
```

Add a module-level weights handle after line 29 (`speed_test_limit = config.speed_test_limit`):

```python
ranking_weights = config.ranking_weights
```

Replace the body of `get_sort_result` (lines 517-537, from `total_result = []` through `return total_result`) with:

```python
    total_result = []
    for result in results:
        if not ipv6_support and result["ipv_type"] == "ipv6":
            result.update(default_ipv6_result)
        result_speed, result_delay, resolution = (
            result.get("speed") or 0,
            result.get("delay"),
            result.get("resolution")
        )
        if result_delay == -1:
            continue
        if not supply:
            if filter_speed and result_speed < resolution_speed_map.get(resolution, min_speed):
                continue
            if filter_resolution and resolution:
                resolution_value = get_resolution_value(resolution)
                if resolution_value < min_resolution or resolution_value > max_resolution:
                    continue
            if not is_sustainable(result, ranking_weights):
                continue
        total_result.append(result)
    total_result.sort(
        key=lambda item: (compute_score(item, ranking_weights), item.get("speed") or 0),
        reverse=True,
    )
    return total_result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (all scoring + sort tests)

- [ ] **Step 6: Commit**

```bash
git add utils/types.py utils/speed.py tests/test_scoring.py
git commit -m "feat(speed): rank by blended score with sustainability gate"
```

---

## Task 9: Compute encoded bitrate during segment download

**Files:**
- Modify: `utils/speed.py:181-266` (`get_result`) and `get_avg_result` at `utils/speed.py:436-442`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_get_avg_result_aggregates_bitrate_and_fps -v`
Expected: FAIL with `KeyError: 'bitrate'`

- [ ] **Step 3: Implement bitrate aggregation in get_avg_result**

Replace `get_avg_result` (lines 436-442) with:

```python
def get_avg_result(result) -> TestResult:
    bitrates = [item.get("bitrate") for item in result if item.get("bitrate")]
    fps_values = [item.get("fps") for item in result if item.get("fps")]
    video_codec = next((item.get("video_codec") for item in result if item.get("video_codec")), None)
    audio_codec = next((item.get("audio_codec") for item in result if item.get("audio_codec")), None)
    return {
        'speed': sum(item['speed'] or 0 for item in result) / len(result),
        'delay': max(
            int(sum(item['delay'] or -1 for item in result) / len(result)), -1),
        'resolution': max((item['resolution'] for item in result), key=get_resolution_value),
        'bitrate': (sum(bitrates) / len(bitrates)) if bitrates else None,
        'fps': max(fps_values) if fps_values else None,
        'video_codec': video_codec,
        'audio_codec': audio_codec,
    }
```

- [ ] **Step 4: Implement bitrate computation in get_result**

In `utils/speed.py`, the segment-handling block in `get_result` currently builds `segment_urls` (lines 204-214) then downloads samples (lines 218-225). Replace the segment-building branch and the download/aggregation block so playback durations are carried alongside URLs.

Replace lines 204-214 (the `if playlists: ... raise Exception("Segment urls not found")` block) with:

```python
                    segment_pairs = []
                    if playlists:
                        best_playlist = max(m3u8_obj.playlists, key=lambda p: p.stream_info.bandwidth)
                        playlist_url = urljoin(url, best_playlist.uri)
                        playlist_content = await get_url_content(playlist_url, headers, session, timeout)
                        if playlist_content:
                            media_playlist = m3u8.loads(playlist_content)
                            segment_pairs = [
                                (urljoin(playlist_url, segment.uri), segment.duration)
                                for segment in media_playlist.segments
                            ]
                    else:
                        segment_pairs = [
                            (urljoin(url, segment.uri), segment.duration) for segment in segments
                        ]
                    if not segment_pairs:
                        raise Exception("Segment urls not found")
```

Then replace the download/aggregation block (lines 218-225, from `start_time = time()` through the `info['delay'] = ...` line) with:

```python
                start_time = time()
                sampled_pairs = sample_segment_urls(segment_pairs, speed_test_limit)
                tasks = [get_speed_with_download(ts_url, headers, session, timeout)
                         for ts_url, _ in sampled_pairs]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                total_size = sum(result['size'] for result in results if isinstance(result, dict))
                total_time = sum(result['time'] for result in results if isinstance(result, dict))
                info['speed'] = total_size / total_time / 1024 / 1024 if total_time > 0 else 0
                info['delay'] = int(round((time() - start_time) * 1000))
                seg_bytes = 0
                seg_duration = 0.0
                for (_, seg_dur), seg_res in zip(sampled_pairs, results):
                    if isinstance(seg_res, dict) and seg_res.get('size'):
                        seg_bytes += seg_res['size']
                        if seg_dur:
                            seg_duration += seg_dur
                if seg_bytes > 0 and seg_duration > 0:
                    info['bitrate'] = seg_bytes * 8 / seg_duration
```

Note: `sample_segment_urls` indexes its input generically, so passing a list of `(url, duration)` tuples works unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (all). Then sanity-check imports:
Run: `python -c "import utils.speed"`
Expected: no error.

- [ ] **Step 6: Commit**

```bash
git add utils/speed.py tests/test_scoring.py
git commit -m "feat(speed): compute encoded bitrate from segment size/duration"
```

---

## Task 10: Full-probe mode gating

**Files:**
- Modify: `utils/ffmpeg/probe.py:51-58` (extract bitrate)
- Modify: `utils/speed.py` — module-level `full_probe` flag + `get_result` finally block (lines 255-265)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring.py::test_parse_probe_data_extracts_bitrate -v`
Expected: FAIL with `KeyError: 'bitrate'`

- [ ] **Step 3: Extract bitrate in _parse_probe_data**

In `utils/ffmpeg/probe.py`, replace the `meta = {...}` dict (lines 51-56) with:

```python
    fmt = data.get('format', {}) or {}
    bitrate = None
    try:
        br = fmt.get('bit_rate')
        if br is not None:
            bitrate = float(br)
    except (TypeError, ValueError):
        bitrate = None

    meta = {
        'video_codec': _safe_get(video, 'codec_name'),
        'audio_codec': _safe_get(audio, 'codec_name'),
        'resolution': res,
        'fps': frame_rate_val,
        'bitrate': bitrate,
    }
```

- [ ] **Step 4: Add full_probe flag and gate the probe in get_result**

In `utils/speed.py`, add after the `ranking_weights = config.ranking_weights` line (added in Task 8):

```python
full_probe = config.open_full_probe or os.path.exists(constants.cache_path)
```

Add `import os` at the top of `utils/speed.py` if not already present (it is not — add it after `import asyncio` at line 1).

Replace the `finally` block of `get_result` (lines 255-266) with:

```python
    finally:
        need_probe = (
            not location and info.get('delay') != -1 and (
                (filter_resolution and not info.get('resolution'))
                or (full_probe and (not info.get('fps') or not info.get('video_codec')
                                    or not info.get('bitrate')))
            )
        )
        if need_probe:
            try:
                probed = await probe_url(url, headers, timeout=timeout)
                if probed:
                    if not info.get('resolution'):
                        info['resolution'] = probed.get('resolution')
                    if not info.get('fps'):
                        info['fps'] = probed.get('fps')
                    if not info.get('video_codec'):
                        info['video_codec'] = probed.get('video_codec')
                    if not info.get('audio_codec'):
                        info['audio_codec'] = probed.get('audio_codec')
                    if not info.get('bitrate') and probed.get('bitrate'):
                        info['bitrate'] = probed.get('bitrate')
            except Exception:
                pass
        return info
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS (all). Then:
Run: `python -c "import utils.speed"`
Expected: no error.

- [ ] **Step 6: Commit**

```bash
git add utils/ffmpeg/probe.py utils/speed.py tests/test_scoring.py
git commit -m "feat(speed): full-probe mode enriches signals when history cache exists"
```

---

## Final verification

- [ ] Run the full test file: `python -m pytest tests/test_scoring.py -v` — all green.
- [ ] Run the existing suite to confirm no regressions: `python -m pytest tests/ -v`.
- [ ] Sanity import: `python -c "import utils.speed, utils.scoring, utils.config"`.

---

## Spec Coverage Map

| Spec requirement | Task |
|---|---|
| Loadability L (startup + margin) | 5 |
| Quality Q (resolution + encoding adequacy + fps) | 1–4 |
| Codec-aware bitrate normalization | 3 |
| Final blended score | 6 |
| Tunable weights in config | 7 |
| Fast-scan mode (cheap signals, no ffprobe) | 9 (bitrate from segments) + 10 (probe gated off when cold) |
| Full-probe mode (ffprobe when warm) | 10 |
| Per-run mode selection via cache.gz | 10 (`full_probe` flag) |
| Graceful missing-signal fallbacks | 1–6 (NEUTRAL fallbacks) |
| delay==-1 dropped; existing filters kept | 8 |
| margin<1 gate unless supply | 8 |
| Backward compat: speed-only reproduces today | 6 (test) + 8 (speed tiebreak) |
| IPv6 default floats up | 5 (inf-speed -> margin 1) + 8 |
| Test scenarios a–e | 4, 6, 8 |

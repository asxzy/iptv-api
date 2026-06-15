# Anti-Fake Quality Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Penalize upscaled resolution and duplicated frame-rate fakes via a cheap bpp prior (all streams) plus an ffmpeg deep-probe (warm, top-N finalists), folded into the quality score through per-dimension authenticity factors.

**Architecture:** A pure `utils/authenticity.py` turns raw detector signals (mpdecimate keep-ratio, ssim) into authenticity factors `A_res`/`A_fps`. `utils/ffmpeg/deep_probe.py` runs the ffmpeg detectors (fail-open). `utils/scoring.py`'s `quality_score` multiplies the resolution/fps credit by these factors, using a cheap bpp prior when no deep signal is present. `utils/channel.py` runs the deep-probe pass on top-N per station after speed tests, mutating result dicts in place; the aggregator's final force-flush re-sorts with the adjusted data.

**Tech Stack:** Python 3.13, pytest, asyncio, ffmpeg (mpdecimate + scale + ssim filters).

---

## File Structure

- **Create** `utils/authenticity.py` — pure: `lower_resolution_tier`, `fps_authenticity`, `resolution_authenticity`. Imports `resolution_score`, `fps_score` from `utils.scoring`.
- **Create** `utils/ffmpeg/deep_probe.py` — async ffmpeg runners `measure_keep_ratio`, `measure_upscale_ssim`, and their pure parsers `_parse_mpdecimate_keep_ratio`, `_parse_ssim_all`. Fail-open.
- **Create** `tests/test_authenticity.py` — unit tests for authenticity + scoring authenticity behavior + deep_probe parsers + deep_probe_pass orchestration.
- **Modify** `utils/scoring.py` — add `bpp_prior`; make `quality_score` apply `A_res`/`A_fps`; add `bpp_prior_floor`/`bpp_prior_knee` to `DEFAULT_WEIGHTS`.
- **Modify** `utils/config.py` — add `bpp_prior_floor`/`bpp_prior_knee` to `ranking_weights`; add `authenticity_config` bundle + deep-probe flags.
- **Modify** `config/config.ini` — new `[Settings]` keys.
- **Modify** `utils/channel.py` — `deep_probe_pass` + `_deep_probe_one`; call it at the end of `test_speed`.
- **Modify** `main.py` — force-flush the aggregator before saving cache so cache.gz and result.txt both reflect the authenticity re-sort.

No circular imports: `scoring` imports nothing new; `authenticity` imports from `scoring`; `channel` imports both + `deep_probe`.

Tests run with: `python3 -m pytest tests/test_authenticity.py -v` (repo root on path via test header).

---

## Task 1: lower_resolution_tier

**Files:**
- Create: `utils/authenticity.py`
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_lower_resolution_tier_steps_down_one_tier -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.authenticity'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/authenticity.py tests/test_authenticity.py
git commit -m "feat(authenticity): add lower_resolution_tier"
```

---

## Task 2: scoring bpp_prior + new weight keys

**Files:**
- Modify: `utils/scoring.py` (`DEFAULT_WEIGHTS` at line 66; add `bpp_prior` function)
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bpp_prior_bounds():
    from utils.scoring import bpp_prior, DEFAULT_WEIGHTS
    # adequacy >= knee (0.3) -> 1.0
    assert bpp_prior(0.5, DEFAULT_WEIGHTS) == 1.0
    assert bpp_prior(0.3, DEFAULT_WEIGHTS) == 1.0
    # adequacy 0.0 -> floor 0.7
    assert abs(bpp_prior(0.0, DEFAULT_WEIGHTS) - 0.7) < 1e-9
    # midpoint 0.15 -> halfway between floor and 1.0 -> 0.85
    assert abs(bpp_prior(0.15, DEFAULT_WEIGHTS) - 0.85) < 1e-9


def test_bpp_prior_neutral_adequacy_is_one():
    from utils.scoring import bpp_prior, DEFAULT_WEIGHTS, NEUTRAL
    # NEUTRAL (0.5) adequacy means "unknown" -> no penalty
    assert bpp_prior(NEUTRAL, DEFAULT_WEIGHTS) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_bpp_prior_bounds -v`
Expected: FAIL with `ImportError: cannot import name 'bpp_prior'`

- [ ] **Step 3: Implement**

In `utils/scoring.py`, add two keys to `DEFAULT_WEIGHTS` (inside the dict, after `"ref_throughput_mbps": 10.0,`):

```python
    # anti-fake cheap prior
    "bpp_prior_floor": 0.7,     # lowest value of the bpp authenticity prior
    "bpp_prior_knee": 0.3,      # adequacy at/above which the prior is 1.0
```

Then add this function immediately after `encoding_adequacy` (after its `return` at ~line 100):

```python
def bpp_prior(adequacy, weights=DEFAULT_WEIGHTS):
    """
    Cheap metadata-only authenticity prior in [floor, 1.0], derived from encoding
    adequacy. Low adequacy (bitrate cannot back the claimed pixels/frames) -> mild
    suspicion. NEUTRAL adequacy (unknown, no bitrate) -> 1.0 (no penalty).
    """
    floor = weights["bpp_prior_floor"]
    knee = weights["bpp_prior_knee"]
    if adequacy == NEUTRAL:
        return 1.0
    if knee <= 0:
        return 1.0
    frac = min(1.0, max(0.0, adequacy / knee))
    return floor + (1.0 - floor) * frac
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_authenticity.py
git commit -m "feat(scoring): add bpp authenticity prior and weight keys"
```

---

## Task 3: quality_score applies A_res / A_fps

**Files:**
- Modify: `utils/scoring.py` (`quality_score` at lines 102-111)
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_quality_score_uses_explicit_authenticity_factors():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    base = {"resolution": "1920x1080", "bitrate": 5_000_000, "fps": 50,
            "video_codec": "h264"}
    honest = quality_score(base, DEFAULT_WEIGHTS)
    faked = quality_score({**base, "a_res": 0.71, "a_fps": 0.55}, DEFAULT_WEIGHTS)
    assert faked < honest


def test_quality_score_fake_1080p_ranks_below_honest_720p():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    # upscaled 1080p flagged by deep probe (a_res collapses to ~720p ratio)
    fake = quality_score({"resolution": "1920x1080", "bitrate": 2_500_000,
                          "fps": 25, "video_codec": "h264", "a_res": 0.714,
                          "a_fps": 1.0}, DEFAULT_WEIGHTS)
    honest_720 = quality_score({"resolution": "1280x720", "bitrate": 3_000_000,
                                "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert honest_720 > fake


def test_quality_score_cheap_prior_applies_when_no_deep_signal():
    from utils.scoring import quality_score, DEFAULT_WEIGHTS
    # very low adequacy (under-encoded for claim) -> cheap prior < 1 discounts credit
    starved = quality_score({"resolution": "1920x1080", "bitrate": 200_000,
                             "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    rich = quality_score({"resolution": "1920x1080", "bitrate": 5_000_000,
                          "fps": 25, "video_codec": "h264"}, DEFAULT_WEIGHTS)
    assert starved < rich


def test_quality_score_no_signals_unchanged_factors_are_one():
    from utils.scoring import quality_score, resolution_score, fps_score, encoding_adequacy, DEFAULT_WEIGHTS
    # bitrate absent -> adequacy NEUTRAL -> prior 1.0 -> equals plain weighted blend
    r = {"resolution": "1280x720", "fps": 25, "video_codec": "h264"}
    w = DEFAULT_WEIGHTS
    expected = (w["w_res"] * resolution_score("1280x720")
                + w["w_enc"] * encoding_adequacy("1280x720", None, 25, "h264", w)
                + w["w_fps"] * fps_score(25))
    assert abs(quality_score(r, w) - expected) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_quality_score_uses_explicit_authenticity_factors -v`
Expected: FAIL (current `quality_score` ignores `a_res`/`a_fps`, so faked == honest)

- [ ] **Step 3: Implement**

Replace `quality_score` (lines 102-111) with:

```python
def quality_score(result, weights=DEFAULT_WEIGHTS):
    """
    Blend resolution, encoding adequacy, and fps into a 0-1 quality score.

    The resolution and fps credits are scaled by per-dimension authenticity
    factors A_res / A_fps: explicit `a_res`/`a_fps` from the deep-probe pass when
    present, else a cheap bpp prior derived from encoding adequacy.
    """
    resolution = result.get("resolution")
    bitrate = result.get("bitrate")
    fps = result.get("fps")
    codec = result.get("video_codec")
    rs = resolution_score(resolution)
    es = encoding_adequacy(resolution, bitrate, fps, codec, weights)
    fs = fps_score(fps)
    prior = bpp_prior(es, weights)
    a_res = result.get("a_res")
    a_fps = result.get("a_fps")
    a_res = prior if a_res is None else a_res
    a_fps = prior if a_fps is None else a_fps
    return weights["w_res"] * (rs * a_res) + weights["w_enc"] * es + weights["w_fps"] * (fs * a_fps)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_authenticity.py tests/test_scoring.py -v`
Expected: PASS (test_authenticity new tests + all existing test_scoring still green)

Note: `test_scoring.py`'s existing `quality_score` tests pass because absent bitrate → adequacy NEUTRAL → prior 1.0, and present-bitrate cases already expected the adequacy effect; verify none regress.

- [ ] **Step 5: Commit**

```bash
git add utils/scoring.py tests/test_authenticity.py
git commit -m "feat(scoring): scale resolution/fps credit by authenticity factors"
```

---

## Task 4: fps_authenticity

**Files:**
- Modify: `utils/authenticity.py`
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fps_authenticity_frame_dup_collapses_credit():
    from utils.authenticity import fps_authenticity
    from utils.scoring import fps_score
    # 50fps claimed, half the frames are duplicates -> effective 25fps
    a = fps_authenticity(50, 0.5)
    assert abs(a - fps_score(25) / fps_score(50)) < 1e-9


def test_fps_authenticity_genuine_and_unknown():
    from utils.authenticity import fps_authenticity
    assert fps_authenticity(50, 1.0) == 1.0      # no dups
    assert fps_authenticity(None, 0.5) == 1.0     # unknown fps -> no penalty
    assert fps_authenticity(50, None) == 1.0      # no measurement -> no penalty
    assert 0.0 <= fps_authenticity(50, 0.1) <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_fps_authenticity_frame_dup_collapses_credit -v`
Expected: FAIL with `ImportError: cannot import name 'fps_authenticity'`

- [ ] **Step 3: Implement**

Append to `utils/authenticity.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/authenticity.py tests/test_authenticity.py
git commit -m "feat(authenticity): add fps_authenticity from keep-ratio"
```

---

## Task 5: resolution_authenticity

**Files:**
- Modify: `utils/authenticity.py`
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolution_authenticity_upscale_high_ssim_collapses_credit():
    from utils.authenticity import resolution_authenticity, lower_resolution_tier
    from utils.scoring import resolution_score
    # high round-trip SSIM => upscaled => credit collapses toward 720p/1080p ratio
    a = resolution_authenticity("1920x1080", 0.99, 0.96, 0.985)
    ratio = resolution_score(lower_resolution_tier("1920x1080")) / resolution_score("1920x1080")
    assert abs(a - ratio) < 1e-9


def test_resolution_authenticity_genuine_low_ssim_is_one():
    from utils.authenticity import resolution_authenticity
    assert resolution_authenticity("1920x1080", 0.95, 0.96, 0.985) == 1.0


def test_resolution_authenticity_unknown_or_lowest_tier_is_one():
    from utils.authenticity import resolution_authenticity
    assert resolution_authenticity(None, 0.99, 0.96, 0.985) == 1.0
    assert resolution_authenticity("640x360", 0.99, 0.96, 0.985) == 1.0  # lowest tier
    assert resolution_authenticity("1920x1080", None, 0.96, 0.985) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_resolution_authenticity_upscale_high_ssim_collapses_credit -v`
Expected: FAIL with `ImportError: cannot import name 'resolution_authenticity'`

- [ ] **Step 3: Implement**

Append to `utils/authenticity.py`:

```python
def resolution_authenticity(declared_resolution, ssim, ssim_low, ssim_high):
    """
    0-1 resolution authenticity from the upscale round-trip SSIM.

    A round-trip through the next-lower tier that barely changes the frame
    (high SSIM) means there was no real detail beyond that tier -> upscaled.
    c_fake grades from 0 at ssim_low to 1 at ssim_high; the credit collapses
    toward the lower tier's score by c_fake. Unknown resolution, lowest tier,
    or missing SSIM -> 1.0 (no penalty).
    """
    if ssim is None:
        return 1.0
    lower = lower_resolution_tier(declared_resolution)
    if lower is None:
        return 1.0
    declared_rs = resolution_score(declared_resolution)
    if declared_rs <= 0:
        return 1.0
    span = ssim_high - ssim_low
    if span <= 0:
        c_fake = 1.0 if ssim >= ssim_high else 0.0
    else:
        c_fake = max(0.0, min(1.0, (ssim - ssim_low) / span))
    ratio = resolution_score(lower) / declared_rs
    return 1.0 - c_fake * (1.0 - ratio)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/authenticity.py tests/test_authenticity.py
git commit -m "feat(authenticity): add resolution_authenticity from ssim"
```

---

## Task 6: config — authenticity bundle + deep-probe flags

**Files:**
- Modify: `config/config.ini`
- Modify: `utils/config.py` (`ranking_weights` property; add `authenticity_config` + flags after it)
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_config_has_authenticity_settings -v`
Expected: FAIL (`bpp_prior_floor` missing from ranking_weights / no `authenticity_config`)

- [ ] **Step 3: Add config.ini keys**

Add to the `[Settings]` section of `config/config.ini`, right after the `open_full_probe = False` line:

```ini
# 反作弊：开启深度探测（仅在存在历史缓存的全量模式下，对每个频道前N个候选进行内容分析）| Anti-fake: enable deep probe (warm mode only, top-N candidates per channel)
open_deep_probe = True
deep_probe_top_n = 5
deep_probe_sample_seconds = 4
deep_probe_timeout = 15
# 升尺度检测的SSIM阈值；廉价bpp先验的下限/拐点 | Upscale-detect SSIM thresholds; cheap bpp prior floor/knee
ssim_low = 0.96
ssim_high = 0.985
bpp_prior_floor = 0.7
bpp_prior_knee = 0.3
```

- [ ] **Step 4: Add config properties**

In `utils/config.py`, add the two new keys inside the `ranking_weights` dict (after the `"ref_throughput_mbps": ...` line):

```python
            "bpp_prior_floor": getf("Settings", "bpp_prior_floor", fallback=0.7),
            "bpp_prior_knee": getf("Settings", "bpp_prior_knee", fallback=0.3),
```

Then add these properties immediately after the `open_full_probe` property:

```python
    @property
    def authenticity_config(self):
        getf = self.config.getfloat
        return {
            "ssim_low": getf("Settings", "ssim_low", fallback=0.96),
            "ssim_high": getf("Settings", "ssim_high", fallback=0.985),
        }

    @property
    def open_deep_probe(self):
        return self.config.getboolean("Settings", "open_deep_probe", fallback=True)

    @property
    def deep_probe_top_n(self):
        return self.config.getint("Settings", "deep_probe_top_n", fallback=5)

    @property
    def deep_probe_sample_seconds(self):
        return self.config.getint("Settings", "deep_probe_sample_seconds", fallback=4)

    @property
    def deep_probe_timeout(self):
        return self.config.getint("Settings", "deep_probe_timeout", fallback=15)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py::test_config_has_authenticity_settings -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/config.ini utils/config.py tests/test_authenticity.py
git commit -m "feat(config): add anti-fake deep-probe settings and prior weights"
```

---

## Task 7: deep_probe — mpdecimate keep-ratio

**Files:**
- Create: `utils/ffmpeg/deep_probe.py`
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_parse_mpdecimate_keep_ratio -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.ffmpeg.deep_probe'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/ffmpeg/deep_probe.py tests/test_authenticity.py
git commit -m "feat(deep_probe): add mpdecimate keep-ratio measurement"
```

---

## Task 8: deep_probe — upscale SSIM

**Files:**
- Modify: `utils/ffmpeg/deep_probe.py`
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_parse_ssim_all -v`
Expected: FAIL with `ImportError: cannot import name '_parse_ssim_all'`

- [ ] **Step 3: Implement**

Append to `utils/ffmpeg/deep_probe.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/ffmpeg/deep_probe.py tests/test_authenticity.py
git commit -m "feat(deep_probe): add upscale round-trip ssim measurement"
```

---

## Task 9: deep_probe_pass orchestration + test_speed hook

**Files:**
- Modify: `utils/channel.py` (imports near line 28-57; add functions; call in `test_speed` after the gather at line 877)
- Test: `tests/test_authenticity.py`

- [ ] **Step 1: Write the failing test**

```python
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
    # rely on the defaults written to config.ini in Task 6 (open_deep_probe=True, top_n=5).
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
    assert up["a_res"] < 1.0                                   # upscale detected
    assert abs(dup["a_fps"] - fps_score(25) / fps_score(50)) < 1e-9  # frame-dup detected
    assert honest.get("a_res", 1.0) == 1.0                    # low ssim -> authentic
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_authenticity.py::test_deep_probe_pass_mutates_top_n -v`
Expected: FAIL with `AttributeError: module 'utils.channel' has no attribute 'deep_probe_pass'`

- [ ] **Step 3: Implement**

In `utils/channel.py`, add imports near the other `utils` imports (top of file):

```python
from utils.scoring import compute_score
from utils.authenticity import fps_authenticity, resolution_authenticity, lower_resolution_tier
from utils.ffmpeg.deep_probe import measure_keep_ratio, measure_upscale_ssim
```

Add these functions above `test_speed`:

```python
async def _deep_probe_one(item, weights, auth_cfg, sem, logger=None):
    """Run deep-probe detectors on one result dict and attach authenticity fields."""
    async with sem:
        url = item.get("url")
        if not url:
            return
        headers = (config.open_headers and item.get("headers")) or None
        sample = config.deep_probe_sample_seconds
        timeout = config.deep_probe_timeout
        declared_res = item.get("resolution")
        declared_fps = item.get("fps")

        keep_ratio = await measure_keep_ratio(url, headers, sample, timeout)
        if keep_ratio is not None and declared_fps:
            item["effective_fps"] = float(declared_fps) * keep_ratio
            item["a_fps"] = fps_authenticity(declared_fps, keep_ratio)

        lower = lower_resolution_tier(declared_res)
        if lower is not None:
            ssim = await measure_upscale_ssim(url, declared_res, lower, headers, sample, timeout)
            if ssim is not None:
                item["a_res"] = resolution_authenticity(
                    declared_res, ssim, auth_cfg["ssim_low"], auth_cfg["ssim_high"]
                )
                item["effective_resolution"] = lower if item["a_res"] < 1.0 else declared_res

        if logger and ("a_res" in item or "a_fps" in item):
            logger.info(
                "Deep-probe: %s | res %s -> a_res=%.2f | fps %s -> a_fps=%.2f",
                url, declared_res, item.get("a_res", 1.0),
                declared_fps, item.get("a_fps", 1.0),
            )


async def deep_probe_pass(grouped_results, logger=None):
    """
    Deep-probe the top-N finalists per channel (by preliminary score) and attach
    a_res/a_fps/effective_* in place. No-op when disabled.
    """
    if not config.open_deep_probe:
        return
    weights = config.ranking_weights
    auth_cfg = config.authenticity_config
    top_n = config.deep_probe_top_n
    sem = asyncio.Semaphore(config.speed_test_limit)
    tasks = []
    for cate, names in grouped_results.items():
        for name, items in names.items():
            valid = [it for it in items if is_valid_speed_result(it)]
            valid.sort(key=lambda it: (compute_score(it, weights), it.get("speed") or 0), reverse=True)
            for it in valid[:top_n]:
                tasks.append(_deep_probe_one(it, weights, auth_cfg, sem, logger))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

Then in `test_speed`, after the gather block (lines 876-877):

```python
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

insert immediately after it (before `close_logger_handlers`):

```python
    try:
        await deep_probe_pass(grouped_results, logger=logger)
    except Exception:
        logger.debug("deep_probe_pass failed", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_authenticity.py::test_deep_probe_pass_mutates_top_n -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest tests/test_authenticity.py tests/test_scoring.py -q` then `python3 -c "import utils.channel"`
Expected: all PASS, import clean.

- [ ] **Step 6: Commit**

```bash
git add utils/channel.py tests/test_authenticity.py
git commit -m "feat(channel): deep-probe top-N finalists after speed test"
```

---

## Task 10: main.py — re-sort before saving cache

**Files:**
- Modify: `main.py` (the `finally` block inside `main`, around lines 432-443, before `_save_cache`)

- [ ] **Step 1: Add a force flush before cache save**

The deep-probe pass mutates result dicts that the aggregator holds by reference, but the incremental sorts ran before those mutations. The aggregator's `stop()` does a final force-flush (re-sorting `result.txt`), but `_save_cache` runs *before* `stop()` in the `finally`, so `cache.gz` would keep the pre-deep-probe order. Force a re-sort first so cache.gz and result.txt agree.

In `main.py`, in `main`'s `finally` block, change:

```python
            finally:
                logger.info("Phase: finalizing (saving cache/frozen state)")
                status.set_phase("finalizing", progress=95)
                if config.open_history:
                    self._save_cache(self.aggregator.result)
                    frozen.save(constants.frozen_path)
                    logger.debug("Cache and frozen state saved")
                await self._stop_aggregator()
```

to:

```python
            finally:
                logger.info("Phase: finalizing (saving cache/frozen state)")
                status.set_phase("finalizing", progress=95)
                if self.aggregator:
                    try:
                        await self.aggregator.flush_once(force=True)
                    except Exception:
                        logger.debug("final re-sort flush failed", exc_info=True)
                if config.open_history:
                    self._save_cache(self.aggregator.result)
                    frozen.save(constants.frozen_path)
                    logger.debug("Cache and frozen state saved")
                await self._stop_aggregator()
```

- [ ] **Step 2: Verify import + no syntax error**

Run: `python3 -c "import main"`
Expected: no error (a version-info log line may print; that is fine).

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "fix(main): re-sort before cache save so deep-probe order persists"
```

---

## Final verification

- [ ] `python3 -m pytest tests/test_authenticity.py tests/test_scoring.py -v` — all green.
- [ ] `python3 -m pytest tests/ -q --ignore=tests/test_nested_blacklist.py` — no regressions (test_nested_blacklist has a known pre-existing `ipdb` env failure).
- [ ] `python3 -c "import utils.scoring, utils.authenticity, utils.ffmpeg.deep_probe, utils.channel, utils.config, main"` — clean imports.

---

## Spec Coverage Map

| Spec requirement | Task |
|---|---|
| `Q` with A_res/A_fps multipliers | 3 |
| A_fps from mpdecimate keep-ratio | 4 (factor), 7 (signal) |
| A_res from upscale SSIM | 5 (factor), 8 (signal) |
| Cheap bpp prior (all streams) | 2, 3 |
| Deep-probe ffmpeg detectors, fail-open | 7, 8 |
| Pure authenticity helpers | 1, 4, 5 |
| Warm + top-N per station trigger | 9 |
| Config keys / bundles | 6 |
| Logging declared vs effective | 9 |
| Order persists to cache.gz + result.txt | 10 |
| Backward compat (no signals → factors 1.0) | 3 (test) |
| Unit + parser + integration tests | 1–9 |

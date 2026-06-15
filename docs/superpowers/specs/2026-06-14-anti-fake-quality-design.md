# Anti-Fake Quality Detection Design

**Date:** 2026-06-14
**Status:** Approved design — ready for implementation planning
**Builds on:** `2026-06-14-media-ranking-design.md` (the blended quality + loadability score)

## Problem

Providers fake quality metadata to look better than the content actually is:

1. **Upscaling** — a 720p source is upscaled to 1920x1080. The container/codec resolution
   genuinely reads 1080p, but there is no real detail beyond 720p (interpolated, smooth).
2. **Frame duplication** — a 25fps source is presented as 50fps by duplicating every frame.
   The metadata fps reads 50, but there is no new temporal information.

The current ranking already demotes the *common* form of both fakes, because
`encoding_adequacy` (bits-per-pixel-per-frame = `bitrate / (width·height·fps)`) deflates
when either fake inflates its denominator without adding real bits (upscaled frames compress
well; duplicate frames encode as near-zero P-frames). But this defense has gaps:

- The `resolution_score` and `fps_score` dimensions still award full credit for the faked
  metadata, partially offsetting the bpp penalty.
- A provider who pads bitrate (encodes the blurry/duplicated content at a high bitrate)
  restores bpp and defeats the metadata-only signal entirely.

## Goals

- Penalize upscaled resolution and duplicated frame rate so a fake ranks at (or below) its
  true quality tier.
- Use a cheap, metadata-only layer for all streams plus a precise, content-analysis layer for
  the finalists that matter.
- Grade by confidence — nudge borderline cases, crush blatant fakes, avoid over-punishing
  honest soft content.
- Never silently drop a working stream on a misdetection (fail open).

## Non-Goals

- No new heavy dependencies. Detection uses ffmpeg only (already a dependency). No
  numpy/opencv.
- No change to the loadability component, the sustainability gate, or the two-mode
  (fast-scan / full-probe) strategy from the prior design.
- Deep content analysis on every stream (too slow); finalists only.

## Authenticity Model

Quality gains two authenticity factors on the faked dimensions. The encoding-adequacy term is
unchanged — it remains the independent "how well-encoded at its real resolution" axis.

```
Q = w_res·(res_score · A_res) + w_enc·enc + w_fps·(fps_score · A_fps)
```

`A_res, A_fps ∈ [0,1]`, default `1.0`. When deep-probe measurements are present on a result,
they supersede the cheap prior for that dimension.

### A_fps — frame-rate authenticity

- **Deep (mpdecimate ran):**
  - `keep_ratio = kept_frames / total_frames`
  - `effective_fps = declared_fps · keep_ratio`
  - `A_fps = fps_score(effective_fps) / fps_score(declared_fps)`, clamped to `[0,1]`.
  - A 25→50 frame-dup yields `keep_ratio ≈ 0.5` → `effective_fps ≈ 25` → the fps credit
    collapses to the 25fps level.
  - If `declared_fps` is missing or `fps_score(declared_fps) == 0`, `A_fps = 1.0`.

- **Cheap prior (no deep):** `A_fps = bpp_prior` (see below).

### A_res — resolution authenticity

- **Deep (ssim round-trip ran):**
  - Sample a few frames at native `W×H`; for each, downscale to the next-lower resolution
    tier then upscale back to `W×H`; compute `SSIM(original, round-trip)`. Use the mean SSIM.
  - `c_fake = clamp((ssim − ssim_low) / (ssim_high − ssim_low), 0, 1)` — fraction-fake.
  - `A_res = 1 − c_fake · (1 − res_score(lower_tier) / res_score(declared_tier))`
  - Fully-fake (`c_fake = 1`) collapses the resolution credit to the lower tier's level;
    genuine (`c_fake = 0`) leaves it at `1.0`.
  - If declared resolution is unknown or already at the lowest tier, `A_res = 1.0`.

- **Cheap prior (no deep):** `A_res = bpp_prior` (see below).

### Cheap bpp prior

A gentle, floored function of `encoding_adequacy` shared by both dimensions:

```
bpp_prior = max(bpp_prior_floor, min(1.0, adequacy / bpp_prior_knee))
```

With defaults `bpp_prior_floor = 0.7`, `bpp_prior_knee = 0.3`: adequacy ≥ 0.3 → prior 1.0;
adequacy → 0 → prior 0.7. It is deliberately gentle and targets the *metadata credit* that
the additive `enc` term never touches, so overlap with `enc` is minimal and intentional. When
`adequacy` is neutral/unknown (no bitrate), `bpp_prior = 1.0` (no penalty).

## Deep-Probe Mechanics (ffmpeg only)

New module `utils/ffmpeg/deep_probe.py`. Both functions **fail open** (return a sentinel
meaning "authentic / unknown" so the caller leaves `A = 1.0`) on timeout, decode error,
non-zero exit, or missing ffmpeg. Bounded by `deep_probe_sample_seconds` and
`deep_probe_timeout`.

- **`measure_keep_ratio(url, headers, sample_seconds, timeout) -> float | None`**
  - Runs `ffmpeg -t <sample_seconds> -i <url> -vf mpdecimate -loglevel debug -f null -`.
  - Parses the `mpdecimate` debug lines (`keep`/`drop` decisions) → `kept / total`.
  - Returns `None` on failure.

- **`measure_upscale_ssim(url, headers, declared_resolution, lower_resolution, sample_seconds, timeout) -> float | None`**
  - Extracts sample frames and computes mean SSIM between each native frame and its
    downscale-to-`lower_resolution`-then-upscale-back round-trip, using ffmpeg's `scale` +
    `ssim` filters.
  - Returns mean SSIM in `[0,1]`, or `None` on failure.

## Authenticity Helpers (pure)

New module `utils/authenticity.py` — pure functions, no I/O, unit-testable on plain numbers:

- `fps_authenticity(declared_fps, keep_ratio, weights) -> float`
- `resolution_authenticity(declared_resolution, ssim, weights) -> float` (uses
  `ssim_low`/`ssim_high` and the resolution tier helpers)
- `bpp_prior(adequacy, weights) -> float`
- `lower_resolution_tier(resolution) -> str | None` — the next tier down used as the
  SSIM round-trip target and the `A_res` floor reference.

`utils/scoring.py` consumes `A_res`/`A_fps`: if a result carries `a_res`/`a_fps` (set by the
deep-probe pass), use them; otherwise compute the cheap `bpp_prior` and apply it to both.

## Where It Runs

- **Mode:** warm/full-probe only (where `cache.gz` exists). Cold fast-start is untouched.
- **Trigger:** per station, after its speed tests complete, rank by the preliminary
  (non-authenticity) score, take the **top-N finalists** (`deep_probe_top_n`, default 5),
  run deep-probe on each, attach `effective_fps`, `effective_resolution`, `a_res`, `a_fps` to
  those result dicts, then the existing sort uses the authenticity-adjusted `compute_score`.
- **Cost:** ≈ `N × stations` short decodes, warm runs only. Streams outside the top-N keep the
  cheap prior.

## Configuration (new `[Settings]` keys)

| Key | Default | Meaning |
|---|---|---|
| `open_deep_probe` | `True` | Enable deep-probe in warm mode |
| `deep_probe_top_n` | `5` | Finalists per station to deep-probe |
| `deep_probe_sample_seconds` | `4` | Seconds of video to analyze |
| `deep_probe_timeout` | `15` | Per-probe timeout (s) |
| `ssim_low` | `0.96` | SSIM at/below which round-trip is "genuine detail" (A_res→1) |
| `ssim_high` | `0.985` | SSIM at/above which round-trip is "upscaled" (A_res→floor) |
| `bpp_prior_floor` | `0.7` | Lowest value of the cheap prior |
| `bpp_prior_knee` | `0.3` | Adequacy at/above which the cheap prior is 1.0 |

These are exposed as a parallel `authenticity_config` bundle (a dict, mirroring the existing
`ranking_weights` pattern) read by `config.py`. The pure helpers in `utils/authenticity.py`
accept this dict as their `weights`/`config` argument so they stay I/O-free and testable.

## Logging

When deep-probe fires on a stream, log declared vs effective values
(`resolution: 1920x1080 → eff 1280x720`, `fps: 50 → eff 25`, `A_res`, `A_fps`) to
`speed_test.log`, so fakes are visible in operator output.

## Error Handling

- Deep-probe failure (timeout/decode/missing ffmpeg) → fail open: no `a_res`/`a_fps` attached,
  stream keeps the cheap prior. Logged at debug.
- `measure_*` never raises to the caller; they return `None`.
- A `deep_probe_top_n` larger than the candidate count probes all candidates.

## Testing

- **`utils/authenticity.py` unit tests:**
  - `fps_authenticity`: keep_ratio 0.5 on declared 50fps → A_fps ≈ `fps_score(25)/fps_score(50)`; keep_ratio 1.0 → 1.0; missing fps → 1.0.
  - `resolution_authenticity`: high SSIM (0.99) on declared 1080p → A_res collapses toward `res_score(720)/res_score(1080)`; low SSIM (0.95) → 1.0; lowest-tier or unknown → 1.0.
  - `bpp_prior`: adequacy 0.5 → 1.0; adequacy 0.0 → floor 0.7; neutral/unknown → 1.0.
- **Parser tests** for `measure_keep_ratio` and `measure_upscale_ssim` using captured sample
  ffmpeg stderr/output (no live network in unit tests).
- **scoring integration test:** a stream with `a_res`/`a_fps` set (fake) ranks below an honest
  lower-tier stream; a stream without them falls back to the cheap prior; default (no fakery
  signals) reproduces the prior design's ordering.
- **Backward compatibility:** results with no authenticity fields and no adequacy penalty score
  exactly as before — `A_res = A_fps = 1.0`.

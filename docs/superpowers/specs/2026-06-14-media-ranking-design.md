# Media Ranking Redesign

**Date:** 2026-06-14
**Status:** Approved design — ready for implementation planning

## Problem

For each station we assume all media from different sources show 100% identical
content. The current ranking (`get_sort_result` in `utils/speed.py`) sorts purely
by `speed` (MB/s download throughput) descending. Every other collected signal —
`delay`, `resolution`, `fps`, `video_codec`, `audio_codec` — is used only as a
pass/fail filter, never in ordering.

This has two flaws:

1. **Download throughput is not quality.** Since content is identical across
   sources, a higher-quality copy is encoded at a *higher bitrate* — a larger
   payload that can show a *lower* throughput margin. Sorting purely by speed can
   quietly demote the better-quality stream.
2. **Quality signals are wasted.** `fps`, `codec`, and true resolution are
   collected (in fallback paths) but never influence the result order.

## Goals

- Prioritize **actual quality** — not just resolution, but encoding richness
  (bitrate-per-pixel), codec efficiency, and frame rate.
- Prioritize **loadability** — the stream must start fast (low delay) AND sustain
  playback (throughput comfortably exceeds bitrate). Faster is better.
- Blend the two into a single tunable score; a much faster stream may outrank a
  marginally higher-quality one.

## Non-Goals

- No change to how candidates are discovered, deduplicated, or how the whitelist /
  retained-origin streams are handled (they still bypass scoring).
- No change to the IPv6-proxy fast-path (those keep `default_ipv6_result` and
  float to the top as today).

## Scoring Model

Two normalized components (each 0–1), blended into one score. All weights live in
`config.ini` with defaults chosen so that **if only `speed` is present, ordering
matches today's behavior** (no regression for non-probing users).

### Loadability `L` — "will it play, and start fast?"

- `startup = clamp(1 − delay / delay_max)` — lower delay → higher score.
- `margin  = throughput_Mbps / bitrate_Mbps` — measured download throughput vs.
  the stream's own encoded bitrate. Saturating:
  - `margin ≥ margin_target` → `1.0`
  - `margin ≤ 1` (cannot keep up with playback) → `0.0`
  - linear in between.
- `L = w_start·startup + w_margin·margin`

`margin` is the keystone: it **couples quality and speed automatically**. A
higher-bitrate (better) stream demands proportionally more throughput to score
well on loadability, so the quality/speed tension is expressed in the math rather
than bolted on.

`throughput_Mbps = measured_speed_MBps × 8`.

### Quality `Q` — "how good is the picture?"

- `resolution_score` — pixel count normalized into tiers (4K / 1080p / 720p / …).
- `encoding_adequacy` — bits-per-pixel-per-frame
  `bpp = bitrate / (width · height · fps)`, divided by a codec-efficiency factor
  (HEVC/AV1 count for more per bit than H.264), passed through a **saturating**
  curve. This is the "actual quality, not just resolution" signal: it penalizes a
  fake / under-encoded 1080p but does not over-reward bloated bitrate beyond
  "visually good enough."
- `fps_score` — 60 vs 30 vs 25, normalized.
- `Q = w_res·resolution_score + w_enc·encoding_adequacy + w_fps·fps_score`

### Final

```
score = w_quality · Q + w_loadability · L
```

Streams are sorted by `score` descending.

## Two Modes (progressive enhancement)

Mode is chosen **per run** by whether `output/data/cache.gz` (history) exists.
Overridable by config.

### Fast scan — cold start, no history

- Use only cheap signals already gathered in the HTTP download path:
  `delay`, measured `throughput`, and `resolution` + `bitrate` from the m3u8
  variant `bandwidth` (declared) or computed from `segment_size ÷ EXTINF duration`
  (segments are already downloaded, so this is nearly free).
- `fps` and `codec` are unknown → their sub-scores use the **neutral fallback**
  (neither help nor hurt). `Q` effectively reduces to resolution +
  encoding-adequacy-from-declared-bitrate.
- No ffprobe. Goal: usable ranked list as fast as possible.

### Full probe — warm run, history exists

- Run one lightweight `ffprobe` per candidate to obtain *measured* `resolution`,
  `fps`, `video_codec`, and `bitrate`, replacing declared/guessed values.
- Full `Q` with all four signals active.
- This is the refinement pass: the previous run's list already works, so accuracy
  is worth the extra time.

## Missing-Signal Handling (graceful degradation)

- Every sub-score has a defined **neutral fallback** when its signal is absent, so
  `Q` and `L` are always computable. Weights **renormalize over the present
  signals**, so a stream is scored on what is known rather than punished for a
  missing probe.
- `bitrate` unavailable → `margin` falls back to raw-throughput ranking (today's
  behavior) and `encoding_adequacy` goes neutral.
- `resolution` unknown → neutral resolution tier (not zero), so an unprobed-but-
  fast stream still competes.

## Gating (filters applied before scoring)

- `delay == -1` → dropped (dead stream). Unchanged.
- Existing `open_filter_speed` / `open_filter_resolution` / `min_speed` /
  `resolution_speed_map` thresholds still act as hard filters when enabled.
- New optional gate: `margin < 1` (throughput cannot sustain the bitrate) →
  dropped, **unless** `open_supply` is on. This delivers the "ensure it can be
  loaded" guarantee.

## Backward Compatibility

- `get_sort_result` keeps its signature; only its internal sort key changes from
  `speed` to the blended `score`.
- IPv6-proxy streams keep `default_ipv6_result` (infinite speed) and float up.
- Default weights make speed-only inputs reproduce today's ordering.

## Plumbing Changes

- `get_speed_with_download` (or its caller in `get_result`) must surface the
  segment's playback **duration** alongside `size`/`time`, so encoded `bitrate =
  size / duration` can be derived during fast scan.
- A `compute_score(result, mode, weights)` function in `utils/speed.py` (or a new
  small module) encapsulates the formula; `get_sort_result` calls it.
- New config keys under `[Settings]`: component weights (`w_quality`,
  `w_loadability`), sub-weights (`w_start`, `w_margin`, `w_res`, `w_enc`,
  `w_fps`), `delay_max`, `margin_target`, codec-efficiency factors, and a
  mode override.

## Testing

Unit tests for the scoring function, using identical-content fixtures:

- (a) higher bitrate wins at equal resolution;
- (b) a fake / under-encoded 1080p loses to an honest 720p;
- (c) a stream that cannot sustain its bitrate (`margin < 1`) sinks / is gated;
- (d) all-missing-signals input reduces to speed-only ordering (regression guard);
- (e) `startup` ordering: equal everything-else, lower delay wins.

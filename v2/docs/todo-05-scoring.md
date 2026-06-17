# TODO: Feature 05 - Scoring Component

## Tasks
- [x] Design scoring algorithm (quality + loadability + authenticity)
- [x] Implement `ScoringWorker` class
  - [x] `on_fast_scan_complete`: handle fast scan events
  - [x] `on_full_scan_complete`: handle full scan events
  - [x] `on_deep_scan_complete`: handle deep scan events
  - [x] `_compute_scores`: quality and loadability computation
  - [x] `_compute_quality`: resolution, fps, codec, upscale penalty
  - [x] `_compute_loadability`: speed and delay scoring
  - [x] `_update_and_emit_ranking`: station ranking management
- [x] Add `score` field to `MediaSource` type
- [x] Add `with_score` method to `MediaSource`
- [x] Write tests (5 tests: quality, upscale, balance, concurrency, configurable weights)
- [x] All 137 tests pass (no regressions)

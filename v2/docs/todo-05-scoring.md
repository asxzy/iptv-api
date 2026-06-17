# TODO: Feature 05 - Scoring Component

## Status: complete

## Tasks
- [x] Review spec document (spec-05-scoring.md)
- [x] Review TDD document (tdd-05-scoring.md)
- [x] Extend MediaStatus enum with SCORING_COMPLETE (types.py)
- [x] Extend MediaMetrics with loadability_score, composite_score (types.py)
- [x] Implement ScoringWorker (workers/scoring.py)
  - [x] process_queue: listen for DeepScanCompleteEvent
  - [x] score: compute quality, loadability, composite scores
  - [x] _metrics_to_scoring_dict: bridge MediaMetrics → utils.scoring dict
  - [x] Emit ScoreUpdatedEvent
  - [x] Update GlobalDataStore
  - [x] Optional: emit RankingUpdatedEvent
- [x] Write comprehensive tests (test_scoring.py)
  - [x] Quality score calculation tests
  - [x] Loadability score calculation tests
  - [x] Composite score with configurable weights
  - [x] Upscale penalty detection
  - [x] Missing data (neutral fallback)
  - [x] Event emission (ScoreUpdatedEvent, RankingUpdatedEvent)
  - [x] Store integration
  - [x] Concurrent scoring
- [x] Run tests (52 tests, 100% pass, 88.12% coverage)
- [x] Update PROGRESS.md

## Notes
- Reuse existing scoring algorithms from utils/scoring.py
- Bridge MediaMetrics fields to dict format expected by utils.scoring
- Handle missing data gracefully (NEUTRAL fallback)
- Follow same pattern as ValidationWorker

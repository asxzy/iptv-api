# TODO: All Features

## Feature 01: Event Bus & Global Data Store
- [x] Spec document
- [x] TDD document  
- [x] TODO document
- [x] Implement EventBus
- [x] Implement GlobalDataStore
- [x] Write tests (100% pass rate)
- [x] Integration tests
- [x] Coverage check

## Feature 02: Discovery & M3U Resolution
- [x] Spec document
- [x] TDD document
- [x] TODO document
- [x] Implement source parsers
- [x] Implement M3U resolver
- [x] Write tests (100% pass rate)
- [x] Integration tests with EventBus
- [x] Coverage check

## Feature 03: Validation & Filtering
- [x] Spec document
- [x] TDD document
- [x] Implement whitelist/blacklist filtering
- [x] Implement connectivity checks
- [x] Write tests (28 tests, all passing)
- [x] Coverage check

## Feature 04: Scan Modes (Fast/Full/Deep)
- [x] Spec document
- [x] TDD document
- [x] Implement FastScanWorker
- [x] Implement FullScanWorker
- [x] Implement DeepScanWorker
- [x] Implement ScanOrchestrator
- [x] Write tests (66 tests, all passing)
- [x] Code coverage check (scan.py: 92%, project total: 90%)

## Feature 05: Scoring Component
- [x] Spec document
- [x] TDD document
- [x] Implement ScoringWorker
- [x] Reuse utils.scoring algorithms, adapted to v2 architecture
- [x] Configurable weights via constructor
- [x] Composite score: w_Q * Q + w_L * L
- [x] Event-driven: DeepScanCompleteEvent → ScoreUpdatedEvent + RankingUpdatedEvent
- [x] Store integration: updates media source with SCORING_COMPLETE status + scores
- [x] Write tests (52 tests, 100% pass, 88.12% coverage)
  - [x] Quality score calculation
  - [x] Loadability score calculation
  - [x] Configurable weights (quality vs loadability domination)
  - [x] Upscale detection penalty (a_res)
  - [x] Missing data → NEUTRAL fallback
  - [x] Event emission (ScoreUpdatedEvent, RankingUpdatedEvent)
  - [x] Store integration
  - [x] Concurrent scoring (20 sources, no race conditions)
  - [x] Codec efficiency (hevc vs h264)
  - [x] Edge cases (infinite speed, zero values, negative delay)
- [x] Coverage check (88.12 >= 88%)

## Feature 06: Result Writer & Global Store Updates
- [ ] Spec document
- [ ] TDD document
- [ ] TODO document
- [ ] Implement ResultWriter
- [ ] Write tests
- [ ] Coverage check

## Feature 07: Proxy Mode
- [ ] Spec document
- [ ] TDD document
- [ ] TODO document
- [ ] Implement proxy inspector
- [ ] Write tests (aiming for 100% coverage)
- [ ] Coverage check

## Feature 08: Orchestrator & Web Service
- [ ] Spec document
- [ ] TDD document
- [ ] TODO document
- [ ] Implement orchestrator
- [ ] Update web service endpoints
- [ ] Integration tests (aiming for 95%+ coverage)
- [ ] Coverage check

# TODO: Feature 08 - Orchestrator & Web Service

## Tasks
- [x] Spec document
- [x] TDD document
- [x] Implement `Orchestrator` class
  - [x] Pipeline stages: Discovery → Validation → Scan → Scoring → Result Writer
  - [x] Job lifecycle events (Started, Progress, Completed, Failed)
  - [x] Configurable scan modes
  - [x] Configurable concurrency per mode
  - [x] Stage enable/disable switches
  - [x] Phase tracking and progress publishing
  - [x] Error handling
- [x] Write tests (8 tests: pipeline, events, error, modes, concurrency, progress)
- [x] All 175 tests pass (no regressions)

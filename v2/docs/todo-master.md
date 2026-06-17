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
- [x] Implement scoring algorithms
- [x] Implement configurable weights
- [x] Write tests (5 tests, all passing)
- [x] Coverage check (no regressions, 137 total tests pass)

## Feature 06: Result Writer & Global Store Updates
- [x] Spec document
- [x] TDD document
- [x] TODO document
- [x] Implement ResultWriter
- [x] Write tests (9 tests, all passing)
- [x] Coverage check (no regressions, 146 total tests pass)

## Feature 07: Proxy Mode
- [x] Spec document
- [x] TDD document
- [x] TODO document
- [x] Implement ProxyInspector
- [x] Implement AdFilter (ad segment + CUE/Discontinuity filtering)
- [x] Implement PlaylistFilter (master/media dispatching)
- [x] Implement UpscalerInterface (abstract base)
- [x] Write tests (21 tests, all passing)
- [x] Coverage check (no regressions, 167 total tests pass)

## Feature 08: Orchestrator & Web Service
- [ ] Spec document
- [ ] TDD document
- [ ] TODO document
- [ ] Implement orchestrator
- [ ] Update web service endpoints
- [ ] Integration tests (aiming for 95%+ coverage)
- [ ] Coverage check

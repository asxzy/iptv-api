# TDD: Orchestrator & Web Service

## Test-Driven Development Plan

### Test: Full Pipeline Run
**Given**: Configured workers and source URLs
**When**: Orchestrator runs the full pipeline
**Then**: All stages execute and output files are written

### Test: Job Started Event
**Given**: An orchestrator run
**When**: The run starts
**Then**: ScanJobStartedEvent is published with job_id and mode

### Test: Job Completed Event
**Given**: A successful pipeline run
**When**: All stages complete
**Then**: ScanJobCompletedEvent is published with stats

### Test: Job Failed Event
**Given**: A pipeline stage throws an exception
**When**: The orchestrator catches it
**Then**: ScanJobFailedEvent with error_message is published

### Test: Configurable Scan Modes
**Given**: Config specifying fast+full only
**When**: Orchestrator runs
**Then**: Only fast and full scan workers are used

### Test: Configurable Concurrency
**Given**: Config with custom concurrency limits
**When**: Orchestrator creates workers
**Then**: Workers use the configured limits

### Test: Progress Tracking
**Given**: A pipeline with multiple sources
**When**: Progress is tracked
**Then**: Progress events show completed/total per phase

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_orchestrator.py -v
```

## Coverage Requirements
- Minimum 88% code coverage
- All edge cases tested (empty source list, worker failures, config overrides)

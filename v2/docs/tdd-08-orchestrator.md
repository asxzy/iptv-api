# TDD: Orchestrator & Web Service

## Test-Driven Development Plan

### Phase 1: Orchestrator Core

#### Test: Initialization
**Given**: An Orchestrator with default configuration
**When**: The orchestrator is initialized
**Then**: It should have the correct default values (scan mode, etc.)

#### Test: Worker Lifecycle
**Given**: An Orchestrator
**When**: The orchestrator starts
**Then**: It should start all workers in the correct order
**When**: The orchestrator stops
**Then**: It should stop all workers in the reverse order

#### Test: Scan Job Start
**Given**: An Orchestrator
**When**: A ScanJobStartedEvent is received with scan mode = Fast
**Then**: The orchestrator should start the scan job and eventually emit a ScanJobCompletedEvent

#### Test: Scan Modes
**Given**: An Orchestrator
**When**: A ScanJobStartedEvent is received with scan mode = Full
**Then**: The orchestrator should run the pipeline in Full mode
**When**: A ScanJobStartedEvent is received with scan mode = Deep
**Then**: The orchestrator should run the pipeline in Deep mode

#### Test: Error Handling
**Given**: An Orchestrator and a worker that fails to start
**When**: The orchestrator tries to start the worker
**Then**: The orchestrator should emit a ScanJobFailedEvent and stop all workers

#### Test: Graceful Shutdown
**Given**: An Orchestrator that is running
**When**: A stop signal is received
**Then**: The orchestrator should stop all workers and emit a ScanJobCompletedEvent or ScanJobFailedEvent as appropriate

#### Test: Progress Reporting
**Given**: An Orchestrator with a Progress Reporter
**When**: Events are received from workers
**Then**: The Progress Reporter should update the processing_status singleton

### Phase 2: Integration with Web Service (Conceptual)

#### Test: Result File Availability
**Given**: An Orchestrator that has completed a scan job
**When**: The Result Writer has generated the result files
**Then**: The web service (original service/app.py) should be able to serve the result files

#### Test: Update Status Endpoint
**Given**: An Orchestrator that is running a scan job
**When**: The Progress Reporter has updated the processing_status singleton
**Then**: The web service's /update-status endpoint should return the current progress

### Phase 3: Scheduling (Optional, for future work)

#### Test: One-time Scan
**Given**: An Orchestrator configured for one-time scan
**When**: The orchestrator starts
**Then**: It should run one scan job and then stop

#### Test: Continuous Scan
**Given**: An Orchestrator configured for continuous scan (using the existing scheduler)
**When**: The orchestrator starts
**Then**: It should run scan jobs at the configured intervals

## Implementation Order
1. Implement the Orchestrator class (v2/core/workers/orchestrator.py)
2. Implement the Progress Reporter class (v2/core/workers/progress_reporter.py)
3. Update the EventBus if needed (likely not needed)
4. Write comprehensive unit tests for the Orchestrator and Progress Reporter
5. (Optional) Test integration with the web service (we can do a basic test by checking that the result files are generated and the processing_status is updated)
6. Run tests to ensure they pass
7. Update TODO document to "complete"
8. Update PROGRESS.md to reflect completion
9. Commit the work with a descriptive message

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_orchestrator.py -v
```

## Coverage Requirements
- Minimum 80% code coverage (we can adjust based on difficulty)
- All edge cases tested (worker failures, etc.)


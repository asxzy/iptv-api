# Spec: Orchestrator & Web Service

## Overview
A main orchestrator that coordinates the full pipeline: Discovery → Validation → Scan → Scoring → Result Writer. Supports configurable scan modes and emits job lifecycle events.

## Components

### 1. Orchestrator
- Coordinates all pipeline stages in sequence
- Manages job lifecycle (start → progress → complete/fail)
- Supports configurable scan modes (FAST, FULL, DEEP)
- Tracks progress per phase
- Configures workers based on settings

### 2. Pipeline Stages
1. **Discovery**: Load sources from subscribe files, parse playlists
2. **Validation**: Filter URLs against whitelist/blacklist, check connectivity
3. **Scan**: Run Fast/Full/Deep scans based on configuration
4. **Scoring**: Compute quality and loadability scores (reactive)
5. **Result Write**: Generate TXT and M3U output files

### 3. Configuration
- `scan_modes`: List of scan modes to run (fast, full, deep)
- `concurrency_fast`: Max concurrent fast scans
- `concurrency_full`: Max concurrent full scans
- `concurrency_deep`: Max concurrent deep scans
- `open_discovery`: Enable discovery stage
- `open_validation`: Enable validation stage
- `open_scoring`: Enable scoring stage
- `output_dir`: Result output directory

## Event Types
- Uses existing ScanJobStartedEvent, ScanJobProgressEvent, ScanJobCompletedEvent, ScanJobFailedEvent

## Acceptance Criteria
- [ ] Runs full pipeline in sequence
- [ ] Supports configurable scan modes
- [ ] Emits job started event at beginning
- [ ] Emits progress events during pipeline
- [ ] Emits completed event on success
- [ ] Emits failed event on error
- [ ] Tracks elapsed time
- [ ] Configurable worker settings

# TDD: Fast/Full/Deep Scan Modes

## Test-Driven Development Plan

### Test: Fast Mode Success
**Given**: A valid media URL
**When**: Fast scan runs
**Then**: Returns status=available, emits FastScanCompleteEvent

### Test: Full Mode Measures Speed
**Given**: A media URL with known bandwidth
**When**: Full scan runs
**Then**: Measures speed within 20% of expected

### Test: Deep Mode Detects Upscale
**Given**: A 720p video claiming to be 1080p
**When**: Deep scan runs
**Then**: Returns is_upscaled=True, a_res < 1.0

### Test: Parallel Workers
**Given**: 10 sources to scan
**When**: Full mode runs with 5 workers
**Then**: Completes in ~2x source time (not 10x)

### Test: Resource Limit
**Given**: 10 sources in deep mode
**When**: Only 3 FFmpeg processes allowed
**Then**: Only 3 deep scans run concurrently

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_scan_modes.py -v
```

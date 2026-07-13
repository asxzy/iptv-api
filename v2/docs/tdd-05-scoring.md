# TDD: Scoring Component

## Test-Driven Development Plan

### Test: Quality Score Calculation
**Given**: A source with 1920x1080, 30fps, h264, 5Mbps
**When**: Quality score is computed
**Then**: Returns score between 0 and 1

### Test: Upscale Penalty
**Given**: A source claiming 1080p but actual resolution is 720p
**When**: Scored with a_res=0.5
**Then**: Quality score is penalized by 50%

### Test: Speed vs Quality Balance
**Given**: Two sources (high quality/slow vs low quality/fast)
**When**: Scored with w_Q=0.7, w_L=0.3
**Then**: High quality source ranks higher

### Test: Concurrent Scoring
**Given**: 100 sources to score simultaneously
**When**: All threads request scoring
**Then**: No race conditions, all scores computed correctly

### Test: Configurable Weights
**Given**: Config w_Q=0.8, w_L=0.2
**When**: Scores are computed
**Then**: Quality dominates over loadability

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_scoring.py -v
```

## Coverage Requirements
- Minimum 95% code coverage
- All edge cases tested (missing data, zero values, invalid inputs)

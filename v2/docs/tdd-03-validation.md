# TDD: Connectivity Validation & Filtering Stage

## Test-Driven Development Plan

### Test: Whitelist Pass
**Given**: A URL in the whitelist
**When**: Validation runs
**Then**: URL is accepted without further checks

### Test: Blacklist Reject
**Given**: A URL matching a blacklist keyword
**When**: Validation runs
**Then**: URL is rejected, RejectedEvent is emitted

### Test: Connectivity Check Success
**Given**: A valid media URL returning 200 OK
**When**: HEAD request is sent
**Then**: URL is validated

### Test: Connectivity Check Failure
**Given**: A URL that returns 404
**When**: HEAD request is sent
**Then**: URL is rejected

### Test: Content-Type Validation
**Given**: A URL returning text/html
**When**: Content-Type is checked
**Then**: URL is rejected (not a media file)

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_validation.py -v
```

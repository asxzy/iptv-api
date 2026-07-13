# TDD: Result Writer & Global Store Updates

## Test-Driven Development Plan

### Test: TXT Output Generation
**Given**: A store with multiple stations and sources
**When**: TXT output is generated
**Then**: Output contains station_name,url lines

### Test: M3U Output Generation
**Given**: A store with multiple stations and sources
**When**: M3U output is generated
**Then**: Output contains EXTINF and URL entries

### Test: IPv4/IPv6 Splitting
**Given**: Sources with IPv4 and IPv6 URLs
**When**: Output is split
**Then**: IPv4 sources go to ipv4/, IPv6 to ipv6/

### Test: Best Source Per Station
**Given**: Multiple sources per station with different scores
**When**: Output is generated
**Then**: Only the highest-scored source per station is included

### Test: Event Emission
**Given**: A write cycle completes
**When**: Output is written
**Then**: ResultWriterCompletedEvent is published

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_result_writer.py -v
```

## Coverage Requirements
- Minimum 88% code coverage
- All edge cases tested (empty store, missing files, write errors)

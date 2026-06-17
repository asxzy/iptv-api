# TDD: Result Writer & Global Store Updates

## Test-Driven Development Plan

### Phase 1: Result Writer Core

#### Test: Initialization
**Given**: A ResultWriter with default configuration
**When**: The worker is initialized
**Then**: It should have the correct default values (write_interval, etc.)

#### Test: Event Subscription
**Given**: A ResultWorker subscribed to ScoreUpdatedEvent
**When**: A ScoreUpdatedEvent is published
**Then**: The worker should receive and process the event

#### Test: Store Update
**Given**: A ResultWorker and a media source in the Global Data Store
**When**: A ScoreUpdatedEvent arrives for that source
**Then**: The Global Data Store should be updated with the new scores and status

#### Test: File Generation Trigger
**Given**: A ResultWorker with open_realtime_write = true
**When**: A ScoreUpdatedEvent is received
**Then**: A debounced timer should be triggered to regenerate the result files

#### Test: File Generation (Debounce)
**Given**: A ResultWriter with open_realtime_write = true and write_interval = 0.1s
**When**: Multiple ScoreUpdatedEvents arrive in quick succession
**Then**: The result files should be regenerated only once after the debounce period

#### Test: File Generation (End of Scan)
**Given**: A ResultWorker with open_realtime_write = false
**When**: A ScanJobCompletedEvent is received
**Then**: The result files should be generated

#### Test: Output Format (txt)
**Given**: A set of scored media sources in the Global Data Store
**When**: The result files are generated
**Then**: The result.txt file should contain the correct format (station,url lines, with #genre# lines)

#### Test: Output Format (m3u)
**Given**: A set of scored media sources with open_m3u_result = true
**When**: The result files are generated
**Then**: The result.m3u file should contain the correct M3U format with EXTINF tags

#### Test: IP Type Separation
**Given**: A set of media sources with both IPv4 and IPv6 addresses
**When**: The result files are generated
**Then**: The ipv4/result.txt should contain only IPv4 sources, and ipv6/result.txt only IPv6 sources

#### Test: Multi-source Format
**Given**: A station with multiple URLs (from different sources)
**When**: The result files are generated with merge_source = true
**Then**: The station should appear on one line with URLs joined by '#'

#### Test: Configuration Respect
**Given**: A ResultWorker with specific configuration values (e.g., urls_limit = 5)
**When**: The result files are generated
**Then**: The output should respect the configuration (e.g., only top 5 URLs per station)

#### Test: Edge Cases
**Given**: Empty Global Data Store
**When**: The result files are generated
**Then**: The files should contain only the header and possibly update time lines

#### Test: Concurrent Updates
**Given**: Multiple ScoreUpdatedEvents arriving concurrently
**When**: The ResultWorker processes them
**Then**: The Global Data Store should be updated correctly without race conditions

### Phase 2: Integration

#### Test: End-to-End Flow
**Given**: A full pipeline from Discovery to Scoring
**When**: A media source is discovered, validated, scanned, and scored
**Then**: The result files should reflect the scored source in the correct order

#### Test: Real-time Web Service
**Given**: A web service reading the result files
**When**: The ResultWriter updates the result files
**Then**: The web service should serve the updated content immediately (or after the debounce)

### Phase 3: Performance

#### Test: Debounce Efficiency
**Given**: A high frequency of ScoreUpdatedEvents
**When**: The ResultWorker is running
**Then**: The result files should not be regenerated more than once per write_interval

#### Test: Memory Usage
**Given**: A large number of media sources (e.g., 10000)
**When**: The ResultWorker is running
**Then**: The memory usage should be reasonable (not grow indefinitely)

## Implementation Order
1. Extend MediaStatus if needed (already done in Feature 05? We may need to add a RESULT_WRITTEN status or similar)
2. Implement ResultWorker class with event handling and store updates
3. Implement file generation logic (reusing original functions where possible)
4. Implement debounce mechanism
5. Integrate with EventBus and GlobalDataStore
6. Write unit tests
7. Run tests and verify coverage

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_result_writer.py -v
```

## Coverage Requirements
- Minimum 90% code coverage (to match the project average)
- All edge cases tested (missing data, zero values, invalid inputs)


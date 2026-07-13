# TDD: Discovery & M3U Resolution Stage

## Test-Driven Development Plan

### Phase 1: Source Parsing

#### Test: Parse M3U Playlist
**Given**: An M3U file with 3 stations and 2 URLs each
**When**: The parser processes it
**Then**: 3 Station objects are created with 2 MediaSources each

#### Test: Parse Plain Text Playlist
**Given**: A TXT file with `name,url` format lines
**When**: The parser processes it
**Then**: Correct station and media source objects are created

#### Test: Handle Malformed Input
**Given**: A file with invalid lines and missing URLs
**When**: The parser processes it
**Then**: Valid lines are processed, errors are reported via events

### Phase 2: M3U Resolution

#### Test: Follow Single Redirect
**Given**: An M3U URL that returns 302 to another URL
**When**: The resolver follows it
**Then**: The final URL is returned

#### Test: Resolve Nested M3U
**Given**: An M3U that references another M3U which contains a media URL
**When**: The resolver processes it
**Then**: The final media URL is found (recursion depth 2)

#### Test: Respect Max Depth
**Given**: Deeply nested M3U (depth > max)
**When**: The resolver reaches max depth
**Then**: Returns the URL at max depth, emits warning event

#### Test: Timeout Handling
**Given**: A slow server that responds after 15s
**When**: The resolver fetches with 10s timeout
**Then**: TimeoutError is raised, error event is emitted

### Phase 3: Event Integration

#### Test: End-to-End Discovery
**Given**: A subscribe.txt with 2 sources
**When**: Discovery worker processes them
**Then**: Correct events are emitted to the bus

## Implementation Order
1. Source parsers (M3U, TXT)
2. Redirect/M3U resolver
3. Discovery worker with event emission
4. Integration tests

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_discovery.py -v
```

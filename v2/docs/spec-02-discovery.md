# Spec: Discovery & M3U Resolution Stage

## Overview
The Discovery stage is the entry point of the v2 scanning pipeline. It reads subscription sources from `subscribe.txt`, resolves nested M3U playlists to their final media URLs, and emits `MediaSourceDiscovered` events for downstream processing.

## Requirements
1. **Source Parsing**: Parse `subscribe.txt` for M3U, M3U8, and plain text listings
2. **M3U Resolution**: Follow nested M3U files and redirects to find the actual media file
3. **Station Grouping**: Group sources by station/channel name
4. **Event Emission**: Emit typed events for each resolved media source
5. **Error Handling**: Gracefully handle broken links, timeouts, and malformed playlists

## Technical Specifications
- Support recursive M3U nesting (configurable max depth)
- Handle HTTP 302/301 redirects
- Support user-agent and referer headers from subscribe.txt
- Timeout: 10s per request, max 30s per chain
- Respect rate limits (configurable)

## Data Flow
```
subscribe.txt
     |
     v
[M3U Parser] -> [Station A: URL1, URL2, ...]
     |
     v
[Redirect Resolver] -> Final media URLs
     |
     v
Event: MediaSourceDiscovered(station=A, url=...)
```

## Event Types
- `StationDiscoveredEvent`: A new station is found
- `MediaSourceDiscoveredEvent`: A media URL is resolved
- `DiscoveryErrorEvent`: Failure during discovery

## Acceptance Criteria
- [ ] Parses M3U, M3U8, and TXT format sources
- [ ] Resolves nested M3U with redirect following
- [ ] Groups sources by station name
- [ ] Emits typed events for each resolved URL
- [ ] Handles malformed sources gracefully
- [ ] Configurable max recursion depth
- [ ] Respects timeouts and rate limits

## Dependencies
- Event Bus (spec-01)
- aiohttp for async HTTP requests
- m3u8 library for playlist parsing

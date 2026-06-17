# TDD: Proxy Mode

## Test-Driven Development Plan

### Test: Keyword Whitelist Match
**Given**: A URI and a whitelist with keywords
**When**: The URI is inspected
**Then**: Returns allowed with the matching keyword

### Test: Keyword Blacklist Match
**Given**: A URI and a blacklist with keywords
**When**: The URI is inspected
**Then**: Returns blocked with the matching keyword

### Test: Regex Whitelist Match
**Given**: A URI matching a whitelist regex pattern
**When**: The URI is inspected
**Then**: Returns allowed with the matching pattern

### Test: Ad Segment Filtering via Keyword
**Given**: A playlist containing an ad segment URI matching a keyword
**When**: The playlist is filtered
**Then**: The ad segment (with its EXTINF) is removed

### Test: CUE-OUT/CUE-IN Ad Removal
**Given**: A playlist with CUE-OUT/CUE-IN markers
**When**: The playlist is filtered
**Then**: All segments between markers are removed

### Test: Discontinuity Ad Block
**Given**: A playlist with discontinuity-bounded segments where at least one matches ad filter
**When**: The playlist is filtered
**Then**: The entire discontinuity block is removed

### Test: Master Playlist Rewriting
**Given**: A master playlist with STREAM-INF variants
**When**: The playlist is filtered
**Then**: Variant URIs are rewritten to proxy URLs

### Test: Relative URI Resolution
**Given**: A playlist with relative URIs
**When**: The playlist is filtered
**Then**: URIs are resolved to absolute CDN URLs

### Test: UpscalerInterface
**Given**: A subclass of UpscalerInterface
**When**: A concrete implementation is created
**Then**: It can analyze URLs and return results

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_proxy.py -v
```

## Coverage Requirements
- Minimum 88% code coverage
- All edge cases tested (empty lists, malformed playlists, cyclic redirects)

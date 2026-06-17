# Spec: Proxy Mode

## Overview
A proxy worker that inspects URIs against whitelist/blacklist, filters ad segments from HLS playlists, rewrites playlist URIs for proxy forwarding, and provides an interface for future upscaler algorithms.

## Components

### 1. ProxyInspector
- Inspects URIs against configured whitelist/blacklist (keyword and regex)
- Classifies URIs as allowed, blocked, or requires review
- Provides match details (which rule triggered, position)

### 2. AdFilter
- Filters ad segments from HLS media playlists
- Supports keyword and regex matching against segment URIs
- Handles CUE-OUT/CUE-IN markers to drop ad breaks
- Handles EXT-X-DISCONTINUITY bounded ad blocks
- Preserves non-ad segments with original tags

### 3. PlaylistFilter
- Filters master playlists (rewrites variant URIs to proxy)
- Filters media playlists (ad removal + URI rewriting)
- Resolves relative URIs to absolute for CDN direct fetching

### 4. UpscalerInterface
- Abstract base for future upscaler algorithms
- Defines `analyze(url) -> dict` method signature
- Ready for ML-based upscale detection

## Event Types
- `ProxyRequestEvent`: Emitted when a proxy request is received
- `ProxyFilteredEvent`: Emitted when content is filtered
- `ProxyBlockedEvent`: Emitted when a URI is blocked

## Acceptance Criteria
- [ ] Inspects URIs against keyword whitelist/blacklist
- [ ] Inspects URIs against regex whitelist/blacklist
- [ ] Removes ad segments from HLS playlists via keyword match
- [ ] Removes ad segments via CUE-OUT/CUE-IN markers
- [ ] Removes ad segments via discontinuity boundaries
- [ ] Rewrites master playlist variant URIs to proxy
- [ ] Resolves relative URIs to absolute
- [ ] UpscalerInterface can be subclassed
- [ ] Emits events for proxy operations

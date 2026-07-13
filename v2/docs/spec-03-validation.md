# Spec: Connectivity Validation & Filtering Stage

## Overview
The Validation stage filters resolved media URLs against white/blacklists and checks basic connectivity before passing them to the scan stages.

## Requirements
1. **Whitelist/Blacklist Filtering**: Check URLs against configured lists
2. **Connectivity Check**: Verify the media URL is reachable (HEAD request)
3. **Content-Type Validation**: Ensure the URL returns a valid media content type
4. **Event Emission**: Emit success/failure events for downstream processing

## Technical Specifications
- Support keyword-based and regex-based matching
- Configurable timeout for connectivity checks (default: 5s)
- Validate Content-Type headers (video/*, application/x-mpegurl, etc.)
- Support custom headers per source

## Event Types
- `URLValidatedEvent`: URL passed all checks
- `URLRejectedEvent`: URL failed validation
- `ValidationErrorEvent`: Unexpected error during validation

## Acceptance Criteria
- [ ] Filters URLs against whitelist/blacklist
- [ ] Performs HEAD request to verify connectivity
- [ ] Validates Content-Type header
- [ ] Emits typed events for each validated URL
- [ ] Handles network errors gracefully
- [ ] Supports custom headers per source

# TDD: Proxy Mode

## Test-Driven Development Plan

### Phase 1: Global Data Store Extension

#### Test: Whitelist and Blacklist Storage
**Given**: A Global Data Store
**When**: The whitelist and blacklist sets are updated
**Then**: The Global Data Store should store the sets correctly

#### Test: Whitelist and Blacklist Retrieval
**Given**: A Global Data Store with stored whitelist and blacklist sets
**When**: The whitelist and blacklist are retrieved
**Then**: The correct sets should be returned

#### Test: ValidationWorker Updates GDS
**Given**: A ValidationWorker that has read whitelist and blacklist files
**When**: The ValidationWorker processes the files
**Then**: The Global Data Store should be updated with the whitelist and blacklist sets

### Phase 2: ProxyWorker Core

#### Test: Initialization
**Given**: A ProxyWorker with default configuration
**When**: The worker is initialized
**Then**: It should have the correct default values (timeout, etc.)

#### Test: Whitelist Check
**Given**: A ProxyWorker with a whitelist containing "example.com"
**When**: Checking a URL "http://example.com/video.m3u8"
**Then**: The URL should be considered whitelisted

#### Test: Blacklist Check
**Given**: A ProxyWorker with a blacklist containing "bad.com"
**When**: Checking a URL "http://bad.com/video.m3u8"
**Then**: The URL should be considered blacklisted

#### Test: Whitelist Overrides Blacklist
**Given**: A ProxyWorker with whitelist containing "good.com" and blacklist containing "good.com"
**When**: Checking a URL "http://good.com/video.m3u8"
**Then**: The URL should be considered whitelisted (whitelist takes precedence)

#### Test: Neither in List
**Given**: A ProxyWorker with empty whitelist and blacklist
**When**: Checking a URL "http://example.com/video.m3u8"
**Then**: The URL should be allowed by default (or blocked by default? We'll make it configurable; default to allowed)

#### Test: Blocked Request
**Given**: A ProxyWorker with a blacklist containing "bad.com"
**When**: Handling a request to "http://bad.com/video.m3u8"
**Then**: The ProxyWorker should return an error response (e.g., 403 Forbidden) and not forward the request

#### Test: Allowed Request
**Given**: A ProxyWorker with an empty blacklist and a whitelist containing "good.com" (or none, default allow)
**When**: Handling a request to "http://good.com/video.m3u8"
**Then**: The ProxyWorker should forward the request to the upstream and return the upstream's response

#### Test: Upscaler Interface
**Given**: A ProxyWorker with an upscaler algorithm configured
**When**: Handling a request to "http://example.com/video.m3u8"
**Then**: The ProxyWorker should invoke the upscaler algorithm to modify the request (e.g., change the URL or headers) before forwarding

#### Test: Error Handling
**Given**: A ProxyWorker and an upstream that returns an error
**When**: Handling a request to an allowed URL
**Then**: The ProxyWorker should return the upstream's error response to the client

#### Test: Timeout Handling
**Given**: A ProxyWorker with a short timeout
**When**: Handling a request to an upstream that does not respond in time
**Then**: The ProxyWorker should return a timeout error

### Phase 3: Integration with ValidationWorker

#### Test: Dynamic Whitelist/Blacklist Update
**Given**: A ValidationWorker that updates the Global Data Store with whitelist and blacklist sets
**When**: The ValidationWorker processes new whitelist and blacklist files (e.g., after auto-disabling)
**Then**: The ProxyWorker should use the updated sets for subsequent requests

### Phase 4: End-to-End (Optional Integration with Web Service)
**Given**: A web service that uses the ProxyWorker to handle /proxy requests
**When**: A client makes a request to /proxy?url=<allowed_url>
**Then**: The web service should return the upstream's response
**When**: A client makes a request to /proxy?url=<blocked_url>
**Then**: The web service should return a 403 Forbidden response

## Implementation Order
1. Extend Global Data Store to store whitelist and blacklist sets.
2. Update ValidationWorker to update the Global Data Store with the whitelist and blacklist sets when it reads the files.
3. Implement ProxyWorker class with methods to check whitelist/blacklist and handle proxy requests.
4. Write unit tests for the ProxyWorker.
5. (Optional) Update the original service/app.py to use the ProxyWorker for the /proxy endpoint.
6. Write unit tests for the integration (if we do step 5).
7. Run tests to ensure they pass.
8. Update TODO document to "complete".
9. Update PROGRESS.md to reflect completion.
10. Commit the work with a descriptive message.

## Running Tests
```bash
cd v2
python -m pytest core/tests/test_proxy.py -v
```

## Coverage Requirements
- Minimum 85% code coverage (we can adjust based on difficulty)
- All edge cases tested (empty lists, overlapping lists, etc.)


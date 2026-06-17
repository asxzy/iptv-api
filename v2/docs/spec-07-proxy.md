# Spec: Proxy Mode

## Overview
Proxy Mode is a new feature that allows dynamic inspection of URIs against whitelist and blacklist, and provides an interface for future upscaler algorithms. It should work as a pass-through proxy that can block or allow requests based on the current whitelist/blacklist, and optionally modify the request to improve media quality (e.g., by adding upscaling parameters or selecting a different quality tier).

## Requirements
1. **Dynamic Inspection**: The proxy should check each request against the current whitelist and blacklist (which can be updated at runtime) and block or allow the request accordingly.
2. **Request Forwarding**: For allowed requests, the proxy should forward the request to the original upstream and return the response to the client.
3. **Upscaler Interface**: The proxy should provide a hook (e.g., a configuration option or a callback) that allows an upscaler algorithm to modify the request (e.g., by changing the resolution parameter in the URL or adding headers) before forwarding.
4. **Logging**: The proxy should log blocked requests and allowed requests for monitoring.
5. **Performance**: The proxy should be efficient and not introduce significant latency.
6. **Integration**: The proxy should be integrated into the web service as an endpoint (e.g., /proxy) that can be used by clients to fetch media through the proxy.
7. **Configuration**: The proxy should be configurable via config.ini (e.g., open_proxy, proxy_timeout, etc.).
8. **HTTPS Support**: The proxy should handle both HTTP and HTTPS upstream requests (though for HTTPS, it may need to terminate SSL or use tunneling; we can start with HTTP only for simplicity).

## Technical Specifications
- The proxy endpoint should accept a URL parameter (e.g., /proxy?url=<encoded_upstream_url>).
- It should decode the URL, check it against the whitelist and blacklist.
- If the URL is in the whitelist, allow it (optional: still check blacklist? Typically whitelist overrides blacklist).
- If the URL is in the blacklist, block it.
- If not in either, we can allow by default or block by default? We'll follow the original code: if not in whitelist and not in blacklist, allow (but we can make it configurable).
- Before forwarding, if an upscaler algorithm is configured, invoke it to modify the URL (or headers, etc.).
- Forward the request to the upstream (using aiohttp or requests) and stream the response back to the client.
- Handle errors and timeouts appropriately.
- Log the action (blocked/allowed) and the URL.

## Event Types
- The proxy does not necessarily need to emit events to the EventBus, but it could log events for monitoring.
- We could emit a ProxyAccessEvent for each request (allowed or blocked) for integration with the progress reporting.

## Acceptance Criteria
- [ ] The proxy endpoint is accessible at /proxy.
- [ ] Correctly blocks URLs that match the blacklist.
- [ ] Correctly allows URLs that match the whitelist.
- [ ] Correctly handles URLs that match both whitelist and blacklist (whitelist takes precedence).
- [ ] Correctly forwards allowed requests to the upstream and returns the response.
- [ ] The upscaler interface is present and can be configured to modify the request.
- [ ] The proxy logs each request (allowed/blocked).
- [ ] The proxy respects configuration options.
- [ ] All tests pass.
- [ ] No regressions in existing functionality.

## Dependencies
- Event Bus (spec-01) (optional, for logging events)
- Global Data Store (spec-02) (to read the current whitelist and blacklist, or we can read from the config files directly; but note that the whitelist and blacklist can be updated at runtime via the auto-disable feature, so we should read from the Global Data Store if it's kept up to date, or we can read from the files and update via events. We can have the ValidationWorker update the Global Data Store with the current whitelist and blacklist lists, or we can have the proxy read from the files directly and update via a file watcher. For simplicity, we can have the proxy read from the whitelist.txt and blacklist.txt files directly, and we can rely on the fact that the ValidationWorker updates these files when auto-disabling is enabled. However, note that the ValidationWorker in v2 does not currently update the files; it only updates the Global Data Store. We have two options:
   a) Make the ValidationWorker also update the whitelist.txt and blacklist.txt files (like the original code does).
   b) Have the Proxy read from the Global Data Store, and we extend the Global Data Store to store the whitelist and blacklist lists.

We chose to extend the Global Data Store to store the whitelist and blacklist lists (as sets of strings) and update them when the ValidationWorker processes the whitelist and blacklist files. This way, the Proxy can always have the current lists without file I/O.

We will need to update the Global Data Store to store the whitelist and blacklist sets, and update them when the ValidationWorker reads the files.

We can do this in the ValidationWorker: when it reads the whitelist and blacklist files, it updates the Global Data Store with the sets.

Then the Proxy can read from the Global Data Store.

This keeps the whitelist and blacklist consistent across components.

## Implementation Plan
1. Extend Global Data Store to store whitelist and blacklist sets (and optionally the raw lists from files).
2. Update the ValidationWorker to update the Global Data Store with the whitelist and blacklist sets when it reads the files.
3. Implement the ProxyWorker (or we can implement the proxy as a web service endpoint directly in v2/service/app.py? But we are refactoring for modularity, so we should create a ProxyWorker that can be run as a service or integrated into the web server.
   However, note that the web service is already in the original codebase (service/app.py). We are not refactoring the web service in v2 yet (that's Feature 08). So for now, we can implement the proxy as a modification to the original service/app.py, but we want to keep the v2 code separate.

   Alternatively, we can create a ProxyWorker that runs as a standalone HTTP server (using aiohttp) that listens on a different port, and then we can configure the original web service to use it as a forwarding proxy? Or we can replace the proxy endpoint in the original service/app.py with a version that uses our ProxyWorker.

   Given the complexity, and since the web service is not yet refactored in v2, we can implement the proxy endpoint in the original service/app.py by importing our ProxyWorker logic. But we want to keep the v2 code modular and testable.

   We can create a ProxyWorker that provides a function to handle a proxy request, and then we can call that function from the original service/app.py.

   We can do this by having the ProxyWorker be a class that we instantiate and use in the original service/app.py.

   We can keep the ProxyWorker in v2/core/workers/proxy.py, and then in the original service/app.py, we can import it and use it.

   To avoid circular dependencies, we can have the ProxyWorker depend only on the v2 core (EventBus, GlobalDataStore, etc.) and not on the original service.

   Then, in the original service/app.py, we can import the ProxyWorker from v2.core.workers.proxy.

   We will need to adjust the sys.path to allow importing from v2.

   We can do that in the original service/app.py by inserting the v2 directory into sys.path.

   This is acceptable because we are incrementally refactoring.

   Alternatively, we can wait until we refactor the web service in Feature 08, and then we can have the web service in v2 as well.

   Given the time, and since the user's request is to refactor for better modularity, we can choose to implement the proxy endpoint in the original service/app.py but using our modular ProxyWorker from v2.

   We will need to update the original service/app.py to use the ProxyWorker.

   We can do that as part of this feature, or we can leave it for Feature 08.

   Let's read the user's requirement: "Proxy Mode: This is a new feature that should be future compatiable. Proxy Mode can dynamitcally inspect if an uri is in black/white list in case of a media becomes unavaliable/interruppted from upstream. It also leaves an interface to allow upscaler algorithm to improve the media's quality."

   The user does not specify that the proxy must be integrated into the existing web service, but it is implied because the original codebase already has a proxy endpoint.

   We will implement the ProxyWorker in v2, and then we will update the original service/app.py to use it.

   We can do that in this feature, or we can leave it for Feature 08 and just have the ProxyWorker ready.

   To keep the workflow clean, we will implement the ProxyWorker and its tests, and then we will update the original service/app.py in a separate step (or we can do it as part of this feature).

   Let's decide to update the original service/app.py as part of this feature, so that the feature is fully integrated.

   We will need to modify the original service/app.py, which is outside the v2 directory. We can do that as part of the task.

   We will update the TODO and spec accordingly.

   Alternatively, we can create a mock web service in v2 for testing, and then later integrate with the original service.

   Given the time, and to avoid modifying the original codebase in the v2 task, we will implement the ProxyWorker and its tests, and we will leave the integration to the original service for Feature 08.

   We will note in the TODO that the integration with the web service is pending.

   We will update the TODO accordingly.

   Let's proceed with implementing the ProxyWorker and its tests, and we will update the spec and TODO to reflect that the web service integration is a separate step.

   We will adjust the acceptance criteria accordingly.

   We will also update the Global Data Store to store the whitelist and blacklist sets.

   We will update the ValidationWorker to update the Global Data Store with the whitelist and blacklist sets.

   We will implement the ProxyWorker that reads the whitelist and blacklist sets from the Global Data Store.

   The ProxyWorker will provide a function to handle a proxy request (given a URL, headers, etc.) and return the response or an error.

   We will write tests for the ProxyWorker.

   We will then, in a separate step (or we can note in the TODO), update the original service/app.py to use the ProxyWorker.

   Let's proceed.


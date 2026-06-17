# TODO: Feature 07 - Proxy Mode

## Tasks
- [x] Spec document
- [x] TDD document
- [x] Add proxy events (ProxyRequestEvent, ProxyFilteredEvent, ProxyBlockedEvent)
- [x] Implement `ProxyInspector` (keyword/regex whitelist/blacklist)
- [x] Implement `AdFilter` (ad segment filtering, CUE-OUT/IN, discontinuity)
- [x] Implement `PlaylistFilter` (master/media dispatching)
- [x] Implement `UpscalerInterface` (abstract base)
- [x] Implement `ProxyWorker` (coordination + event publishing)
- [x] Write tests (21 tests: inspector, ad filter, playlist, upscaler)
- [x] All 167 tests pass (no regressions)

"""
Shared fixtures and configuration for the IPTV-API test suite.

pytest-asyncio is already installed; we set the event_loop fixture scope to
"session" so async tests share a single loop (faster, no per-test teardown).
"""
import asyncio
from collections import defaultdict

import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Async-aware fake fetch helpers  (used by nested-blacklist tests)
# ---------------------------------------------------------------------------

class FakeAsyncFetch:
    """Async fake fetcher matching the contract of `nested_url_blocked_async`.

    Behaviour is identical to the sync ``FakeFetch`` in test_nested_blacklist,
    but its ``__call__`` is an async coroutine:

        chain, content = await fetch(url)

    - ``content_map``:  dict[url → playlist text]
    - ``redirects``:    dict[url → redirect_target]
    - call_counts are tracked per url for verification.
    """

    def __init__(self, content_map=None, redirects=None):
        self._content = content_map or {}
        self._redirects = redirects or {}
        self.call_counts: dict[str, int] = defaultdict(int)

    async def __call__(self, url):
        self.call_counts[url] += 1
        chain = [url]
        cur = url
        seen = {url}
        while cur in self._redirects:
            nxt = self._redirects[cur]
            chain.append(nxt)
            if nxt in seen:
                break
            seen.add(nxt)
            cur = nxt
        return chain, self._content.get(cur, "")

    def total_calls(self) -> int:
        return sum(self.call_counts.values())


@pytest.fixture
def fake_async_fetch():
    return FakeAsyncFetch


# ---------------------------------------------------------------------------
# Shared blacklist keyword list
# ---------------------------------------------------------------------------

@pytest.fixture
def blacklist():
    return ["/audio/", "bad.example", "nosignal"]

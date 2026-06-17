"""
v2/core/tests/test_proxy.py

Comprehensive tests for the ProxyWorker implementation.
Run with: python -m pytest core/tests/test_proxy.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import aiohttp

import sys
sys.path.insert(0, '/Users/asxzy/src/iptv-api/v2')

from core.workers.proxy import ProxyWorker, ProxyAccessEvent
from core.bus import EventBus
from core.store import GlobalDataStore
from core.workers.validation import ValidationWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_get_cm(status=200, headers=None, body=b"stream data"):
    """Create a mock async context manager for session.get()."""
    mock_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_response.status = status
    mock_response.headers = headers if headers is not None else {
        "Content-Type": "video/mp4",
    }
    mock_response.read = AsyncMock(return_value=body)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_response)
    cm.__aexit__ = AsyncMock(return_value=True)
    return cm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def store():
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
async def proxy_worker(store, event_bus):
    worker = ProxyWorker(
        store=store,
        event_bus=event_bus,
        timeout=2.0,
        max_redirects=3,
        default_allow=True,
    )
    await worker.start()
    yield worker
    await worker.stop()


@pytest.fixture
async def restrictive_proxy_worker(store, event_bus):
    worker = ProxyWorker(
        store=store,
        event_bus=event_bus,
        timeout=2.0,
        default_allow=False,
    )
    await worker.start()
    yield worker
    await worker.stop()


@pytest.fixture
async def populated_store(store):
    await store.update_whitelist({"good.com", "premium-cdn"})
    await store.update_blacklist({"bad.com", "malware", "tracker"})
    return store


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------

class TestGlobalDataStoreWhitelistBlacklist:
    """Tests for the GDS whitelist/blacklist extension."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_whitelist(self, store):
        expected = {"good.com", "premium-cdn"}
        await store.update_whitelist(expected)
        result = await store.get_whitelist()
        assert result == expected

    @pytest.mark.asyncio
    async def test_store_and_retrieve_blacklist(self, store):
        expected = {"bad.com", "malware"}
        await store.update_blacklist(expected)
        result = await store.get_blacklist()
        assert result == expected

    @pytest.mark.asyncio
    async def test_whitelist_isolation(self, store):
        await store.update_whitelist({"a.com"})
        await store.update_blacklist({"b.com"})
        wl = await store.get_whitelist()
        bl = await store.get_blacklist()
        assert "b.com" not in wl
        assert "a.com" not in bl

    @pytest.mark.asyncio
    async def test_lists_initialization_state(self, store):
        wl_init, bl_init = await store.are_lists_initialized()
        assert wl_init is False
        assert bl_init is False

        await store.update_whitelist({"a.com"})
        wl_init, bl_init = await store.are_lists_initialized()
        assert wl_init is True
        assert bl_init is False

        await store.update_blacklist({"b.com"})
        wl_init, bl_init = await store.are_lists_initialized()
        assert wl_init is True
        assert bl_init is True

    @pytest.mark.asyncio
    async def test_clear_also_clears_lists(self, store):
        await store.update_whitelist({"a.com"})
        await store.update_blacklist({"b.com"})
        await store.clear()
        wl = await store.get_whitelist()
        bl = await store.get_blacklist()
        assert len(wl) == 0
        assert len(bl) == 0


class TestValidationWorkerFileLoading:
    """Tests for ValidationWorker's file loading & GDS update."""

    @pytest.mark.asyncio
    async def test_parse_blacklist_file(self, tmp_path, store, event_bus):
        bl_file = tmp_path / "blacklist.txt"
        bl_file.write_text(
            "# comment\n"
            "bad.com\n"
            "malware\n"
            "\n"
            "tracker\n"
        )
        worker = ValidationWorker(event_bus=event_bus, store=store)
        entries = worker._parse_blacklist_file(str(bl_file))
        assert entries == {"bad.com", "malware", "tracker"}

    @pytest.mark.asyncio
    async def test_parse_blacklist_file_missing(self, store, event_bus):
        worker = ValidationWorker(event_bus=event_bus, store=store)
        entries = worker._parse_blacklist_file("/nonexistent/blacklist.txt")
        assert entries == set()

    @pytest.mark.asyncio
    async def test_parse_whitelist_file(self, tmp_path, store, event_bus):
        wl_file = tmp_path / "whitelist.txt"
        wl_file.write_text(
            "# comment\n"
            "CCTV-1, http://good.com/stream\n"
            "http://global.com/stream\n"
            "[KEYWORDS]\n"
            "premium-cdn\n"
            "CCTV-2, fast-cdn\n"
        )
        worker = ValidationWorker(event_bus=event_bus, store=store)
        entries = worker._parse_whitelist_file(str(wl_file))
        assert "http://good.com/stream" in entries
        assert "http://global.com/stream" in entries
        assert "premium-cdn" in entries
        assert "fast-cdn" in entries

    @pytest.mark.asyncio
    async def test_parse_whitelist_file_missing(self, store, event_bus):
        worker = ValidationWorker(event_bus=event_bus, store=store)
        entries = worker._parse_whitelist_file("/nonexistent/whitelist.txt")
        assert entries == set()

    @pytest.mark.asyncio
    async def test_load_whitelist_blacklist_files_updates_gds(
        self, tmp_path, store, event_bus,
    ):
        wl_file = tmp_path / "whitelist.txt"
        wl_file.write_text("http://good.com/stream\npremium-cdn\n")
        bl_file = tmp_path / "blacklist.txt"
        bl_file.write_text("bad.com\nmalware\n")

        worker = ValidationWorker(event_bus=event_bus, store=store)
        await worker.load_whitelist_blacklist_files(str(wl_file), str(bl_file))

        wl = await store.get_whitelist()
        bl = await store.get_blacklist()
        assert "http://good.com/stream" in wl
        assert "premium-cdn" in wl
        assert "bad.com" in bl
        assert "malware" in bl

    @pytest.mark.asyncio
    async def test_load_files_empty_lists(self, tmp_path, store, event_bus):
        wl_file = tmp_path / "whitelist.txt"
        wl_file.write_text("")
        bl_file = tmp_path / "blacklist.txt"
        bl_file.write_text("")

        worker = ValidationWorker(event_bus=event_bus, store=store)
        await worker.load_whitelist_blacklist_files(str(wl_file), str(bl_file))

        wl = await store.get_whitelist()
        bl = await store.get_blacklist()
        assert len(wl) == 0
        assert len(bl) == 0


class TestProxyWorkerInitialization:

    @pytest.mark.asyncio
    async def test_default_config(self, store):
        worker = ProxyWorker(store=store)
        assert worker.timeout == 10.0
        assert worker.max_redirects == 5
        assert worker.default_allow is True
        assert worker.upscaler is None
        assert worker._session is None

    @pytest.mark.asyncio
    async def test_custom_config(self, store):
        worker = ProxyWorker(
            store=store,
            timeout=5.0,
            max_redirects=2,
            default_allow=False,
        )
        assert worker.timeout == 5.0
        assert worker.max_redirects == 2
        assert worker.default_allow is False

    @pytest.mark.asyncio
    async def test_start_stop(self, store):
        worker = ProxyWorker(store=store)
        assert worker._session is None
        await worker.start()
        assert worker._session is not None
        assert not worker._session.closed
        await worker.stop()
        assert worker._session is None or worker._session.closed

    @pytest.mark.asyncio
    async def test_initial_metrics(self, store):
        worker = ProxyWorker(store=store)
        metrics = worker.get_metrics()
        assert metrics['total_requests'] == 0
        assert metrics['allowed'] == 0
        assert metrics['blocked'] == 0
        assert metrics['errors'] == 0
        assert metrics['timeouts'] == 0


class TestProxyWorkerAccessControl:

    @pytest.mark.asyncio
    async def test_is_whitelisted_match(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        assert await worker.is_whitelisted("http://good.com/stream.m3u8")
        assert await worker.is_whitelisted("http://premium-cdn.example.com/stream")

    @pytest.mark.asyncio
    async def test_is_whitelisted_no_match(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        assert not await worker.is_whitelisted("http://example.com/stream.m3u8")
        assert not await worker.is_whitelisted("http://evil.com/stream")

    @pytest.mark.asyncio
    async def test_is_blacklisted_match(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        assert await worker.is_blacklisted("http://bad.com/stream.m3u8")
        assert await worker.is_blacklisted("http://cdn.example.com/malware.exe")
        assert await worker.is_blacklisted("http://tracker.example.com/stream")

    @pytest.mark.asyncio
    async def test_is_blacklisted_no_match(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        assert not await worker.is_blacklisted("http://good.com/stream.m3u8")

    @pytest.mark.asyncio
    async def test_check_access_whitelist_overrides_blacklist(self, populated_store):
        """URL that matches both whitelist and blacklist should be allowed."""
        await populated_store.update_whitelist({"good.com"})
        await populated_store.update_blacklist({"good.com"})
        worker = ProxyWorker(store=populated_store)
        allowed, reason = await worker.check_access("http://good.com/stream.m3u8")
        assert allowed is True
        assert "whitelist" in reason

    @pytest.mark.asyncio
    async def test_check_access_whitelist_allowed(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        allowed, reason = await worker.check_access("http://premium-cdn.example.com/stream")
        assert allowed is True
        assert "whitelist" in reason

    @pytest.mark.asyncio
    async def test_check_access_blacklist_blocked(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        allowed, reason = await worker.check_access("http://bad.com/stream.m3u8")
        assert allowed is False
        assert "blacklist" in reason

    @pytest.mark.asyncio
    async def test_check_access_default_allow(self, store):
        worker = ProxyWorker(store=store, default_allow=True)
        allowed, reason = await worker.check_access("http://unknown.com/stream")
        assert allowed is True
        assert "default allow" in reason

    @pytest.mark.asyncio
    async def test_check_access_default_deny(self, store):
        worker = ProxyWorker(store=store, default_allow=False)
        allowed, reason = await worker.check_access("http://unknown.com/stream")
        assert allowed is False
        assert "default deny" in reason

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, populated_store):
        worker = ProxyWorker(store=populated_store)
        assert await worker.is_whitelisted("http://GOOD.COM/stream")
        assert await worker.is_blacklisted("http://BAD.COM/stream")

    @pytest.mark.asyncio
    async def test_empty_lists(self, store):
        worker = ProxyWorker(store=store, default_allow=True)
        allowed, reason = await worker.check_access("http://example.com/stream")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_substring_matching(self, store):
        await store.update_blacklist({"nosignal"})
        worker = ProxyWorker(store=store)
        assert await worker.is_blacklisted("http://cdn.example.com/nosignal/stream.m3u8")
        assert await worker.is_blacklisted("http://cdn.example.com/nosignal_test/stream")


class TestProxyWorkerRequestHandling:

    @pytest.mark.asyncio
    async def test_blocked_request_returns_403(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            status, headers, body = await worker.handle_request(
                "http://bad.com/stream.m3u8"
            )
            assert status == 403
            assert b"Blocked" in body
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_allowed_request_forwards(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(200, body=b"live stream data")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=mock_cm)
            mock_session.get = get_mock
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/stream.m3u8"
            )
            assert status == 200
            assert body == b"live stream data"
            assert get_mock.call_count == 1
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_default_allow_allowed(self, store, event_bus):
        worker = ProxyWorker(
            store=store,
            event_bus=event_bus,
            timeout=2.0,
            default_allow=True,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(200, body=b"data")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://example.com/stream.m3u8"
            )
            assert status == 200
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_default_deny_blocks_unknown(self, store, event_bus):
        worker = ProxyWorker(
            store=store,
            event_bus=event_bus,
            timeout=2.0,
            default_allow=False,
        )
        await worker.start()
        try:
            status, headers, body = await worker.handle_request(
                "http://example.com/stream.m3u8"
            )
            assert status == 403
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_forwards_custom_headers(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(200, body=b"data")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=mock_cm)
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/stream.m3u8",
                headers={"Authorization": "Bearer test-token"},
            )

            call_kwargs = get_mock.call_args[1]
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_default_user_agent(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(200, body=b"data")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=mock_cm)
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/stream.m3u8"
            )

            call_kwargs = get_mock.call_args[1]
            assert "User-Agent" in call_kwargs["headers"]
            assert "IPTV-API" in call_kwargs["headers"]["User-Agent"]
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_redirect_following(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(200, body=b"data")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=mock_cm)
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/redirect"
            )

            call_kwargs = get_mock.call_args[1]
            assert call_kwargs.get("allow_redirects") is True
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_upstream_error_propagated(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(502, body=b"upstream error")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/stream"
            )
            assert status == 502
            assert b"upstream error" in body
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_upstream_404_propagated(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = _make_get_cm(404, body=b"not found")
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/missing"
            )
            assert status == 404
        finally:
            await worker.stop()


class TestProxyWorkerTimeout:

    @pytest.mark.asyncio
    async def test_timeout_returns_504(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=0.5,
        )
        await worker.start()
        try:
            async def _timeout_side(*args, **kwargs):
                raise asyncio.TimeoutError()

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_cm.__aexit__ = AsyncMock(return_value=True)

            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/stream"
            )
            assert status == 504
            assert b"timeout" in body.lower()
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_timeout_increments_metrics(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=0.5,
        )
        await worker.start()
        try:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_cm.__aexit__ = AsyncMock(return_value=True)

            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            await worker.handle_request("http://premium-cdn.example.com/stream")

            metrics = worker.get_metrics()
            assert metrics['timeouts'] == 1
            assert metrics['errors'] == 1
        finally:
            await worker.stop()


class TestProxyWorkerClientError:

    @pytest.mark.asyncio
    async def test_client_error_returns_502(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection refused")
            )
            mock_cm.__aexit__ = AsyncMock(return_value=True)

            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/stream"
            )
            assert status == 502
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_client_error_logged(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("refused")
            )
            mock_cm.__aexit__ = AsyncMock(return_value=True)

            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(return_value=mock_cm)
            worker._session = mock_session

            await worker.handle_request("http://premium-cdn.example.com/stream")

            metrics = worker.get_metrics()
            assert metrics['errors'] == 1
        finally:
            await worker.stop()


class TestProxyWorkerUpscaler:

    @pytest.mark.asyncio
    async def test_upscaler_called_with_url_and_headers(self, populated_store, event_bus):
        upscaler_called = []

        async def my_upscaler(url: str, headers: dict):
            upscaler_called.append((url, headers))
            return ("http://modified.com/stream", {"X-Upscaled": "true"})

        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
            upscaler=my_upscaler,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=_make_get_cm(200, body=b"data"))
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/stream",
                headers={"Original": "value"},
            )

            # Upscaler should have been called with original URL and headers
            assert len(upscaler_called) == 1
            call_url, call_headers = upscaler_called[0]
            assert call_url == "http://premium-cdn.example.com/stream"
            assert call_headers["Original"] == "value"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_upscaler_modifies_url_and_headers(self, populated_store, event_bus):
        async def my_upscaler(url: str, headers: dict):
            modified_url = url.replace("720p", "1080p")
            headers["X-Quality"] = "high"
            return modified_url, headers

        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
            upscaler=my_upscaler,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(return_value=_make_get_cm(200, body=b"data"))
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/720p/stream.m3u8",
            )

            call_kwargs = get_mock.call_args[1]
            # The URL should have been modified by upscaler
            actual_url = get_mock.call_args[0][0]
            assert "1080p" in actual_url
            assert call_kwargs["headers"].get("X-Quality") == "high"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_upscaler_error_does_not_block_request(self, populated_store, event_bus):
        async def broken_upscaler(url, headers):
            raise RuntimeError("Upscaler crashed")

        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
            upscaler=broken_upscaler,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(
                return_value=_make_get_cm(200, body=b"data")
            )
            worker._session = mock_session

            # Should still forward the request even if upscaler fails
            status, headers, body = await worker.handle_request(
                "http://premium-cdn.example.com/stream"
            )
            assert status == 200
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_no_upscaler_no_modification(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
            upscaler=None,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            get_mock = MagicMock(
                return_value=_make_get_cm(200, body=b"data")
            )
            mock_session.get = get_mock
            worker._session = mock_session

            await worker.handle_request(
                "http://premium-cdn.example.com/stream",
            )

            # URL should remain unchanged
            actual_url = get_mock.call_args[0][0]
            assert actual_url == "http://premium-cdn.example.com/stream"
        finally:
            await worker.stop()


class TestProxyWorkerEvents:

    @pytest.mark.asyncio
    async def test_blocked_request_emits_event(self, populated_store, event_bus):
        proxy_queue = event_bus.subscribe(ProxyAccessEvent)

        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            await worker.handle_request("http://bad.com/stream.m3u8")
            event = await asyncio.wait_for(proxy_queue.get(), timeout=1.0)
            assert isinstance(event, ProxyAccessEvent)
            assert event.url == "http://bad.com/stream.m3u8"
            assert event.allowed is False
            assert "blacklist" in event.reason
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_allowed_request_emits_event(self, populated_store, event_bus):
        proxy_queue = event_bus.subscribe(ProxyAccessEvent)

        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(
                return_value=_make_get_cm(200, body=b"data")
            )
            worker._session = mock_session

            await worker.handle_request("http://premium-cdn.example.com/stream")
            event = await asyncio.wait_for(proxy_queue.get(), timeout=1.0)
            assert isinstance(event, ProxyAccessEvent)
            assert event.allowed is True
            assert event.status_code == 200
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_no_event_bus_no_crash(self, populated_store):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=None,
            timeout=2.0,
        )
        await worker.start()
        try:
            status, headers, body = await worker.handle_request(
                "http://bad.com/stream.m3u8"
            )
            assert status == 403
        finally:
            await worker.stop()


class TestProxyWorkerMetrics:

    @pytest.mark.asyncio
    async def test_metrics_track_blocked(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            await worker.handle_request("http://bad.com/stream.m3u8")
            await worker.handle_request("http://malware.example.com/stream")
            await worker.handle_request("http://tracker.example.com/stream")

            metrics = worker.get_metrics()
            assert metrics['total_requests'] == 3
            assert metrics['blocked'] == 3
            assert metrics['allowed'] == 0
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_metrics_track_allowed(self, populated_store, event_bus):
        worker = ProxyWorker(
            store=populated_store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.get = MagicMock(
                return_value=_make_get_cm(200, body=b"data")
            )
            worker._session = mock_session

            await worker.handle_request("http://premium-cdn.example.com/a")
            await worker.handle_request("http://premium-cdn.example.com/b")

            metrics = worker.get_metrics()
            assert metrics['total_requests'] == 2
            assert metrics['allowed'] == 2
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_metrics_isolation(self, store, event_bus):
        """Each ProxyWorker instance should have its own metrics."""
        worker1 = ProxyWorker(store=store, event_bus=event_bus, timeout=2.0)
        worker2 = ProxyWorker(store=store, event_bus=event_bus, timeout=2.0)
        assert worker1.get_metrics() == worker2.get_metrics()


class TestProxyWorkerDynamicUpdates:

    @pytest.mark.asyncio
    async def test_dynamic_whitelist_update(self, store, event_bus):
        worker = ProxyWorker(
            store=store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            # Before whitelist — unknown URL is allowed (default_allow=True)
            allowed, reason = await worker.check_access(
                "http://new-whitelisted.com/stream"
            )
            assert allowed is True
            assert "default allow" in reason

            # Add to whitelist via store
            await store.update_whitelist({"new-whitelisted.com"})

            # Now it should be whitelisted
            allowed, reason = await worker.check_access(
                "http://new-whitelisted.com/stream"
            )
            assert allowed is True
            assert "whitelist" in reason
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_dynamic_blacklist_update(self, store, event_bus):
        worker = ProxyWorker(
            store=store,
            event_bus=event_bus,
            timeout=2.0,
        )
        await worker.start()
        try:
            # Before blacklist — URL is allowed
            allowed, reason = await worker.check_access(
                "http://new-bad.com/stream"
            )
            assert allowed is True

            # Add to blacklist via store
            await store.update_blacklist({"new-bad.com"})

            # Now it should be blocked
            allowed, reason = await worker.check_access(
                "http://new-bad.com/stream"
            )
            assert allowed is False
            assert "blacklist" in reason
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_replacing_whitelist_removes_old_entries(self, store, event_bus):
        worker = ProxyWorker(store=store, event_bus=event_bus)
        await store.update_whitelist({"old.com"})
        assert await worker.is_whitelisted("http://old.com/stream")

        await store.update_whitelist({"new.com"})
        assert not await worker.is_whitelisted("http://old.com/stream")
        assert await worker.is_whitelisted("http://new.com/stream")


class TestProxyWorkerValidationWorkerIntegration:

    @pytest.mark.asyncio
    async def test_validation_worker_load_updates_proxy_access(
        self, tmp_path, store, event_bus,
    ):
        wl_file = tmp_path / "whitelist.txt"
        wl_file.write_text("good-cdn\n")
        bl_file = tmp_path / "blacklist.txt"
        bl_file.write_text("evil-cdn\n")

        # ValidationWorker loads files → updates GDS
        vw = ValidationWorker(event_bus=event_bus, store=store)
        await vw.load_whitelist_blacklist_files(str(wl_file), str(bl_file))

        # ProxyWorker reads from GDS
        pw = ProxyWorker(store=store, event_bus=event_bus)
        await pw.start()
        try:
            assert await pw.is_whitelisted("http://good-cdn.com/stream")
            assert await pw.is_blacklisted("http://evil-cdn.com/stream")
            assert not await pw.is_whitelisted("http://unknown.com/stream")
        finally:
            await pw.stop()

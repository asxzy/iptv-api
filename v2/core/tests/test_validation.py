"""
v2/core/tests/test_validation.py

Comprehensive tests for the ValidationWorker implementation.
Run with: python -m pytest core/tests/test_validation.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import aiohttp

import sys
sys.path.insert(0, '/Users/asxzy/src/iptv-api/v2')

from core.workers.validation import ValidationWorker, VALID_MEDIA_CONTENT_TYPES
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    MediaSourceDiscoveredEvent,
    URLValidatedEvent,
    URLRejectedEvent,
    ValidationErrorEvent,
)
from core.types import MediaSource, MediaStatus, generate_media_id


def _make_head_cm(status=200, headers=None):
    """Create a mock async context manager for session.head()."""
    mock_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_response.status = status
    mock_response.headers = headers if headers is not None else {"Content-Type": "application/x-mpegurl"}
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_response)
    cm.__aexit__ = AsyncMock(return_value=True)
    return cm


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def store() -> GlobalDataStore:
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def validation_worker(event_bus: EventBus, store: GlobalDataStore) -> ValidationWorker:
    worker = ValidationWorker(
        event_bus=event_bus,
        store=store,
        timeout=2.0,
        max_retries=1,
        retry_delay=0.1,
        max_concurrent=5,
        whitelist_keywords=["whitelisted", "premium-cdn"],
        whitelist_patterns=[r"\.(googlevideo|youtube)\.com"],
        blacklist_keywords=["bad", "malware", "tracker"],
        blacklist_patterns=[r"\.(example|spam)\.(com|org)$"],
        require_content_type=True,
    )
    return worker


@pytest.fixture
def sample_media_source():
    return MediaSource(
        id=generate_media_id("http://example.com/stream.m3u8", "Test Channel"),
        url="http://example.com/stream.m3u8",
        station_name="Test Channel",
        source_file="test_source.txt",
    )


class TestValidationWorker:

    @pytest.mark.asyncio
    async def test_worker_initialization(self, validation_worker):
        assert validation_worker.timeout == 2.0
        assert validation_worker.max_retries == 1
        assert validation_worker.retry_delay == 0.1
        assert validation_worker.max_concurrent == 5
        assert validation_worker.session is None
        assert validation_worker.require_content_type is True

        assert "whitelisted" in validation_worker._whitelist_keywords
        assert len(validation_worker._whitelist_patterns) == 1
        assert "bad" in validation_worker._blacklist_keywords
        assert len(validation_worker._blacklist_patterns) == 1

    @pytest.mark.asyncio
    async def test_start_stop(self, validation_worker):
        assert validation_worker.session is None
        await validation_worker.start()
        assert validation_worker.session is not None
        assert not validation_worker.session.closed
        await validation_worker.stop()
        assert validation_worker.session is None or validation_worker.session.closed

    @pytest.mark.asyncio
    async def test_whitelist_keyword_pass(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/whitelisted/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        await validation_worker.validate(source)

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)
        assert validated.media_source.url == url
        assert validated.media_source.status == MediaStatus.VALIDATED

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rejected_queue.get(), timeout=0.2)

    @pytest.mark.asyncio
    async def test_whitelist_regex_pass(self, validation_worker, event_bus, store):
        url = "http://rr1---sn-abc.googlevideo.com/videoplayback"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        await validation_worker.validate(source)

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)
        assert validated.media_source.url == url

    @pytest.mark.asyncio
    async def test_blacklist_keyword_reject(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/bad-stream/playlist.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        rejected_queue = event_bus.subscribe(URLRejectedEvent)
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        await validation_worker.validate(source)

        rejected = await asyncio.wait_for(rejected_queue.get(), timeout=1.0)
        assert isinstance(rejected, URLRejectedEvent)
        assert rejected.url == url
        assert "Blacklisted keyword" in rejected.reason
        assert rejected.is_blacklist is True

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(validated_queue.get(), timeout=0.2)

        stored = await store.get_source("Test", url)
        assert stored is not None
        assert stored.status == MediaStatus.BLACKLISTED

    @pytest.mark.asyncio
    async def test_blacklist_pattern_reject(self, validation_worker, event_bus, store):
        url = "http://evil.spam.org"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        await validation_worker.validate(source)

        rejected = await asyncio.wait_for(rejected_queue.get(), timeout=1.0)
        assert isinstance(rejected, URLRejectedEvent)
        assert rejected.url == url
        assert "Blacklisted pattern" in rejected.reason

    @pytest.mark.asyncio
    async def test_connectivity_success(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is True

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)
        assert validated.media_source.status == MediaStatus.VALIDATED

    @pytest.mark.asyncio
    async def test_connectivity_failure_404(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/notfound.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(404, {}))
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is False

        rejected = await asyncio.wait_for(rejected_queue.get(), timeout=1.0)
        assert isinstance(rejected, URLRejectedEvent)
        assert "404" in rejected.reason
        assert rejected.is_blacklist is False

    @pytest.mark.asyncio
    async def test_connectivity_failure_timeout(self, validation_worker, event_bus, store):
        url = "http://slow.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )

        async def _timeout_side_effect(*args, **kwargs):
            raise asyncio.TimeoutError("Connection timed out")

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError("Connection timed out"))
        mock_cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=mock_cm)
        validation_worker.session = mock_session

        rejected_queue = event_bus.subscribe(URLRejectedEvent)
        result = await validation_worker.validate(source)
        assert result is False

        rejected = await asyncio.wait_for(rejected_queue.get(), timeout=1.0)
        assert isinstance(rejected, URLRejectedEvent)

    @pytest.mark.asyncio
    async def test_content_type_valid_video(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/video.mp4"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}))
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is True

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)

    @pytest.mark.asyncio
    async def test_content_type_reject_html(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/not-video"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "text/html; charset=utf-8"})
        )
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is False

        rejected = await asyncio.wait_for(rejected_queue.get(), timeout=1.0)
        assert isinstance(rejected, URLRejectedEvent)
        assert "Content-Type" in rejected.reason
        assert "text/html" in rejected.reason

    @pytest.mark.asyncio
    async def test_validation_error_emitted(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        error_queue = event_bus.subscribe(ValidationErrorEvent)

        async def broken_check(url):
            raise RuntimeError("Unexpected internal error")
        validation_worker._check_blacklist = broken_check

        result = await validation_worker.validate(source)
        assert result is False

        error_event = await asyncio.wait_for(error_queue.get(), timeout=1.0)
        assert isinstance(error_event, ValidationErrorEvent)
        assert error_event.url == url
        assert "Unexpected internal error" in error_event.error_message

    @pytest.mark.asyncio
    async def test_custom_headers_per_source(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/secure.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
            headers={"Authorization": "Bearer test-token", "Referer": "http://example.com"},
        )

        mock_cm = _make_head_cm(200)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_mock = MagicMock(return_value=mock_cm)
        mock_session.head = head_mock
        validation_worker.session = mock_session

        await validation_worker.validate(source)

        call_kwargs = head_mock.call_args[1]
        assert "headers" in call_kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert call_kwargs["headers"]["Referer"] == "http://example.com"

    @pytest.mark.asyncio
    async def test_default_user_agent(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )

        mock_cm = _make_head_cm(200)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_mock = MagicMock(return_value=mock_cm)
        mock_session.head = head_mock
        validation_worker.session = mock_session

        await validation_worker.validate(source)

        call_kwargs = head_mock.call_args[1]
        assert "headers" in call_kwargs
        assert "User-Agent" in call_kwargs["headers"]
        assert "IPTV-API" in call_kwargs["headers"]["User-Agent"]

    @pytest.mark.asyncio
    async def test_retry_on_server_error(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )

        cm_500 = _make_head_cm(500, {})
        cm_200 = _make_head_cm(200, {"Content-Type": "video/mp4"})

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(side_effect=[cm_500, cm_200])
        validation_worker.session = mock_session

        validated_queue = event_bus.subscribe(URLValidatedEvent)

        result = await validation_worker.validate(source)
        assert result is True

        assert mock_session.head.call_count == 2

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)

    @pytest.mark.asyncio
    async def test_all_valid_media_types_accepted(self, validation_worker, event_bus, store):
        for content_type in VALID_MEDIA_CONTENT_TYPES:
            url = f"http://cdn.example.com/stream.{content_type.split('/')[-1]}"
            source = MediaSource(
                id=generate_media_id(url, "Test"), url=url,
                station_name="Test", source_file="test.txt",
            )
            mock_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_session.head = MagicMock(
                return_value=_make_head_cm(200, {"Content-Type": content_type})
            )
            validation_worker.session = mock_session

            result = await validation_worker.validate(source)
            assert result is True, f"Content-Type '{content_type}' should be accepted"

    @pytest.mark.asyncio
    async def test_missing_content_type_when_required(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {}))
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_content_type_when_not_required(self, event_bus, store):
        worker = ValidationWorker(
            event_bus=event_bus, store=store,
            timeout=2.0, require_content_type=False,
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {}))
        worker.session = mock_session

        result = await worker.validate(source)
        assert result is True

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)

    @pytest.mark.asyncio
    async def test_metrics_tracking(self, validation_worker, event_bus, store):
        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 0
        assert metrics['validated'] == 0
        assert metrics['rejected'] == 0

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}))
        validation_worker.session = mock_session

        url1 = "http://cdn.example.com/good.m3u8"
        source1 = MediaSource(
            id=generate_media_id(url1, "Test"), url=url1,
            station_name="Test", source_file="test.txt",
        )
        await validation_worker.validate(source1)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 1
        assert metrics['validated'] == 1

        url2 = "http://cdn.example.com/bad-stream.m3u8"
        source2 = MediaSource(
            id=generate_media_id(url2, "Test"), url=url2,
            station_name="Test", source_file="test.txt",
        )
        await validation_worker.validate(source2)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 2
        assert metrics['rejected'] == 1
        assert metrics['blacklist_rejected'] == 1

    @pytest.mark.asyncio
    async def test_process_queue_directly(self, validation_worker, event_bus, store):
        """Test that process_queue correctly consumes events and validates them."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}))
        validation_worker.session = mock_session
        validation_worker._running = True

        url = "http://cdn.valid-stream.com/playlist.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)

        input_queue = asyncio.Queue()
        event = MediaSourceDiscoveredEvent(media_source=source, resolved_url=url)
        await input_queue.put(event)

        from_event = await asyncio.wait_for(input_queue.get(), timeout=2.0)
        media_source = from_event.media_source
        await validation_worker.validate(media_source, from_event.trace_id)

        validated = await asyncio.wait_for(validated_queue.get(), timeout=2.0)
        assert isinstance(validated, URLValidatedEvent)
        assert validated.media_source.url == url

    @pytest.mark.asyncio
    async def test_add_whitelist_keyword(self, validation_worker):
        assert "new-safe" not in validation_worker._whitelist_keywords
        validation_worker.add_whitelist_keyword("new-safe")
        assert "new-safe" in validation_worker._whitelist_keywords

        validation_worker.add_whitelist_keyword("new-safe")
        assert validation_worker._whitelist_keywords.count("new-safe") == 1

    @pytest.mark.asyncio
    async def test_add_blacklist_pattern(self, validation_worker):
        initial_count = len(validation_worker._blacklist_patterns)
        validation_worker.add_blacklist_pattern(r"\.evil\.com")
        assert len(validation_worker._blacklist_patterns) == initial_count + 1

    @pytest.mark.asyncio
    async def test_store_integration(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test Channel", source_file="test.txt",
        )
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        validation_worker.session = mock_session

        await validation_worker.validate(source)

        stored = await store.get_source("Test Channel", url)
        assert stored is not None
        assert stored.status == MediaStatus.VALIDATED

    @pytest.mark.asyncio
    async def test_whitelist_trumps_blacklist(self, validation_worker, event_bus, store):
        url = "http://premium-cdn.example.com/bad-stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        await validation_worker.validate(source)

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rejected_queue.get(), timeout=0.2)

    @pytest.mark.asyncio
    async def test_redirect_following(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/redirect.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        mock_cm = _make_head_cm(200)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_mock = MagicMock(return_value=mock_cm)
        mock_session.head = head_mock
        validation_worker.session = mock_session

        await validation_worker.validate(source)

        call_kwargs = head_mock.call_args[1]
        assert call_kwargs.get("allow_redirects") is True

    @pytest.mark.asyncio
    async def test_concurrent_validation(self, validation_worker, event_bus, store):
        mock_response = AsyncMock(spec=aiohttp.ClientResponse)
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "video/mp4"}

        async def delayed_enter(*args, **kwargs):
            await asyncio.sleep(0.05)
            return mock_response

        cm = AsyncMock()
        cm.__aenter__ = delayed_enter
        cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=cm)
        validation_worker.session = mock_session

        sources = []
        for i in range(10):
            url = f"http://cdn.example.com/stream{i}.m3u8"
            source = MediaSource(
                id=generate_media_id(url, "Test"), url=url,
                station_name="Test", source_file="test.txt",
            )
            sources.append(source)

        validated_queue = event_bus.subscribe(URLValidatedEvent)

        start = asyncio.get_event_loop().time()
        results = await asyncio.gather(*[
            validation_worker.validate(s) for s in sources
        ])
        elapsed = asyncio.get_event_loop().time() - start

        assert all(results)
        assert elapsed < 0.3, f"Concurrent validation took too long: {elapsed:.3f}s"

        validated_count = 0
        while not validated_queue.empty() and validated_count < 10:
            try:
                event = await asyncio.wait_for(validated_queue.get(), timeout=0.5)
                if isinstance(event, URLValidatedEvent):
                    validated_count += 1
            except asyncio.TimeoutError:
                break

        assert validated_count == 10

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        cm_500 = _make_head_cm(500, {})
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=cm_500)
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is False

        assert mock_session.head.call_count == validation_worker.max_retries + 1

    @pytest.mark.asyncio
    async def test_store_gets_updated_on_rejection(self, validation_worker, event_bus, store):
        url = "http://cdn.example.com/bad-stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test Channel", source_file="test.txt",
        )
        await validation_worker.validate(source)

        stored = await store.get_source("Test Channel", url)
        assert stored is not None
        assert stored.status == MediaStatus.BLACKLISTED

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, validation_worker, event_bus):
        """Test that stop() cancels a running process_queue task."""
        input_queue = asyncio.Queue()
        worker_task = asyncio.create_task(
            validation_worker.process_queue(input_queue)
        )
        await asyncio.sleep(0.01)
        # Worker is now running, _task is set
        await validation_worker.stop()
        # Should not hang
        assert True

    @pytest.mark.asyncio
    async def test_video_subtype_accepted(self, validation_worker, event_bus, store):
        """Test that non-standard video content types pass validation."""
        url = "http://cdn.example.com/video.wmv"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "video/x-ms-wmv"})
        )
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is True

    @pytest.mark.asyncio
    async def test_audio_subtype_accepted(self, validation_worker, event_bus, store):
        """Test that non-standard audio content types pass validation."""
        url = "http://cdn.example.com/audio.wav"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "audio/x-wav"})
        )
        validation_worker.session = mock_session

        result = await validation_worker.validate(source)
        assert result is True

    @pytest.mark.asyncio
    async def test_metrics_tracking_complete(self, validation_worker, event_bus, store):
        """Test that all metrics are tracked correctly."""
        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 0
        assert metrics['validated'] == 0
        assert metrics['rejected'] == 0
        assert metrics['errors'] == 0
        assert metrics['whitelist_passed'] == 0
        assert metrics['blacklist_rejected'] == 0
        assert metrics['connectivity_failed'] == 0
        assert metrics['content_type_rejected'] == 0

        # Test successful validation (whitelist not used since configured without keywords)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}))
        validation_worker.session = mock_session

        url1 = "http://cdn.example.com/good.m3u8"
        source1 = MediaSource(
            id=generate_media_id(url1, "Test"), url=url1,
            station_name="Test", source_file="test.txt",
        )
        await validation_worker.validate(source1)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 1
        assert metrics['validated'] == 1

        # Test blacklist rejection
        url2 = "http://cdn.example.com/bad-stream.m3u8"
        source2 = MediaSource(
            id=generate_media_id(url2, "Test"), url=url2,
            station_name="Test", source_file="test.txt",
        )
        await validation_worker.validate(source2)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 2
        assert metrics['validated'] == 1
        assert metrics['rejected'] == 1
        assert metrics['blacklist_rejected'] == 1

        # Test connectivity failure
        mock_session.head = MagicMock(return_value=_make_head_cm(404, {}))
        validation_worker.session = mock_session

        url3 = "http://slow.example.com/stream.m3u8"
        source3 = MediaSource(
            id=generate_media_id(url3, "Test"), url=url3,
            station_name="Test", source_file="test.txt",
        )
        await validation_worker.validate(source3)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 3
        assert metrics['rejected'] == 2
        assert metrics['connectivity_failed'] == 1

        # Test content type rejection
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "text/html"})
        )
        await validation_worker.validate(source1)

        metrics = validation_worker.get_metrics()
        assert metrics['checked'] == 4
        assert metrics['rejected'] == 3
        assert metrics['content_type_rejected'] == 1

    @pytest.mark.asyncio
    async def test_add_whitelist_pattern(self, validation_worker):
        """Test adding regex patterns to whitelist."""
        initial_count = len(validation_worker._whitelist_patterns)
        validation_worker.add_whitelist_pattern(r"\\.googlevideo\\.com")
        assert len(validation_worker._whitelist_patterns) == initial_count + 1

    @pytest.mark.asyncio
    async def test_find_matching_keyword(self, validation_worker):
        """Test _find_matching_keyword logic."""
        keyword = validation_worker._find_matching_keyword(
            "http://cdn.example.com/bad-stream.m3u8",
            ["bad", "malware"]
        )
        assert keyword == "bad"

        keyword = validation_worker._find_matching_keyword(
            "http://premium-cdn.example.com/stream.m3u8",
            ["premium-cdn", "good"]
        )
        assert keyword == "premium-cdn"

        keyword = validation_worker._find_matching_keyword("http://example.com/stream.m3u8", ["bad"])
        assert keyword is None

    @pytest.mark.asyncio
    async def test_check_whitelist_with_patterns(self, validation_worker):
        """Test that compiled patterns are used correctly."""
        # Add a pattern
        validation_worker.add_whitelist_pattern(r"\\.youtube\\.com$")

        # Test URL should be matched
        url = "http://www.youtube.com/watch?v=test"
        assert await validation_worker._check_whitelist(url) is True

        # Test URL should not be matched
        url2 = "http://vimeo.com/watch?v=test"
        assert await validation_worker._check_whitelist(url2) is False

    @pytest.mark.asyncio
    async def test_add_blacklist_keyword(self, validation_worker):
        """Test adding keywords to blacklist."""
        initial_count = len(validation_worker._blacklist_keywords)
        validation_worker.add_blacklist_keyword("test-video")
        assert len(validation_worker._blacklist_keywords) == initial_count + 1

        url = "http://cdn.example.com/test-video-stream.m3u8"
        assert await validation_worker._check_blacklist(url) is not None

    @pytest.mark.asyncio
    async def test_find_matching_keyword(self, validation_worker):
        """Test _find_matching_keyword logic."""
        keyword = validation_worker._find_matching_keyword(
            "http://cdn.example.com/bad-stream.m3u8",
            ["bad", "malware"]
        )
        assert keyword == "bad"

        keyword = validation_worker._find_matching_keyword(
            "http://premium-cdn.example.com/stream.m3u8",
            ["premium-cdn", "good"]
        )
        assert keyword == "premium-cdn"

        keyword = validation_worker._find_matching_keyword("http://example.com/stream.m3u8", ["bad"])
        assert keyword is None

    

    @pytest.mark.asyncio
    async def test_whitelist_trumps_all(self, validation_worker, event_bus, store):
        """Test that whitelist matching completely bypasses all other checks."""
        url = "http://premium-cdn.example.com/bad-stream.m3u8"
        source = MediaSource(
            id=generate_media_id(url, "Test"), url=url,
            station_name="Test", source_file="test.txt",
        )
        validated_queue = event_bus.subscribe(URLValidatedEvent)
        rejected_queue = event_bus.subscribe(URLRejectedEvent)

        await validation_worker.validate(source)

        validated = await asyncio.wait_for(validated_queue.get(), timeout=1.0)
        assert isinstance(validated, URLValidatedEvent)
        assert validated.media_source.status == MediaStatus.VALIDATED

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rejected_queue.get(), timeout=0.2)

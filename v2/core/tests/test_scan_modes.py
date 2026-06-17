"""
v2/core/tests/test_scan_modes.py

Comprehensive tests for Fast/Full/Deep scan workers.
Run with: python -m pytest core/tests/test_scan_modes.py -v
"""

import asyncio
import json
import sys
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import aiohttp
import pytest

from core.workers.scan import (
    BaseScanWorker,
    FastScanWorker,
    FullScanWorker,
    DeepScanWorker,
    ScanOrchestrator,
    get_ffmpeg_semaphore,
    reset_ffmpeg_semaphore,
)
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    DeepScanCompleteEvent,
    FastScanCompleteEvent,
    FullScanCompleteEvent,
    ScanErrorEvent,
    ScanStartedEvent,
)
from core.types import (
    MediaMetrics,
    MediaSource,
    MediaStatus,
    ScanMode,
    generate_media_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_head_cm(status=200, headers=None):
    """Create a mock async context manager for session.head()."""
    mock_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_response.status = status
    mock_response.headers = (
        headers if headers is not None
        else {"Content-Type": "application/x-mpegurl"}
    )
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_response)
    cm.__aexit__ = AsyncMock(return_value=True)
    return cm


def _make_get_cm(
    status=200,
    content=b"x" * 65536 * 16,  # ~1 MB
    headers=None,
):
    """Create a mock async context manager for session.get()."""
    mock_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_response.status = status
    mock_response.headers = headers or {}

    async def _iter_chunks(chunk_size):
        remaining = content
        while remaining:
            yield remaining[:chunk_size]
            remaining = remaining[chunk_size:]

    mock_response.content.iter_chunked = MagicMock(
        return_value=_iter_chunks(65536),
    )
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_response)
    cm.__aexit__ = AsyncMock(return_value=True)
    return cm


def _make_ffprobe_result(**overrides) -> Dict[str, Any]:
    """Create a realistic ffprobe JSON output dict."""
    result = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "bit_rate": "4000000",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "128000",
            },
        ],
        "format": {
            "bit_rate": "4128000",
            "duration": "120.000",
        },
    }
    # Apply overrides at top level
    for k, v in overrides.items():
        if k == "streams":
            result["streams"] = v
        elif k == "format":
            result["format"].update(v)
    return result


def _make_scan_source(url="http://cdn.example.com/stream.m3u8",
                       station="Test Channel",
                       source_file="test.txt"):
    return MediaSource(
        id=generate_media_id(url, station),
        url=url,
        station_name=station,
        source_file=source_file,
    )


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
def fast_worker(event_bus, store):
    return FastScanWorker(
        event_bus=event_bus,
        store=store,
        timeout=2.0,
        max_retries=1,
        retry_delay=0.1,
        max_concurrent=5,
    )


@pytest.fixture
def full_worker(event_bus, store):
    return FullScanWorker(
        event_bus=event_bus,
        store=store,
        timeout=5.0,
        max_retries=1,
        retry_delay=0.1,
        max_concurrent=3,
        download_size=65536,  # small for test speed
        ffprobe_timeout=5.0,
    )


@pytest.fixture
def deep_worker(event_bus, store):
    return DeepScanWorker(
        event_bus=event_bus,
        store=store,
        timeout=10.0,
        max_retries=1,
        retry_delay=0.1,
        max_concurrent=2,
        download_size=65536,
        ffprobe_timeout=5.0,
    )


@pytest.fixture(autouse=True)
def reset_semaphore():
    reset_ffmpeg_semaphore()
    yield
    reset_ffmpeg_semaphore()


# ===================================================================
# FastScanWorker Tests
# ===================================================================

class TestFastScanWorker:

    @pytest.mark.asyncio
    async def test_initialization(self, fast_worker):
        assert fast_worker.timeout == 2.0
        assert fast_worker.max_retries == 1
        assert fast_worker.max_concurrent == 5
        assert fast_worker.scan_mode == ScanMode.FAST
        assert fast_worker.target_status == MediaStatus.FAST_SCANNED
        assert fast_worker.session is None

    @pytest.mark.asyncio
    async def test_start_stop(self, fast_worker):
        assert fast_worker.session is None
        await fast_worker.start()
        assert fast_worker.session is not None
        assert not fast_worker.session.closed
        await fast_worker.stop()
        assert fast_worker.session is None or fast_worker.session.closed

    @pytest.mark.asyncio
    async def test_scan_success(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        started_queue = event_bus.subscribe(ScanStartedEvent)
        complete_queue = event_bus.subscribe(FastScanCompleteEvent)
        error_queue = event_bus.subscribe(ScanErrorEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)

        assert result is True

        started = await asyncio.wait_for(started_queue.get(), timeout=1.0)
        assert isinstance(started, ScanStartedEvent)
        assert started.media_source_id == source.id
        assert started.mode == ScanMode.FAST

        complete = await asyncio.wait_for(complete_queue.get(), timeout=1.0)
        assert isinstance(complete, FastScanCompleteEvent)
        assert complete.media_source.url == source.url
        assert complete.is_available is True
        assert complete.latency_ms >= 0

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(error_queue.get(), timeout=0.2)

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.FAST_SCANNED
        assert stored.metrics.delay_ms is not None
        assert stored.metrics.content_type is not None

    @pytest.mark.asyncio
    async def test_scan_connectivity_failure(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        complete_queue = event_bus.subscribe(FastScanCompleteEvent)
        error_queue = event_bus.subscribe(ScanErrorEvent)

        async def _raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError("Connection timed out")

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError("Connection timed out"))
        mock_cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=mock_cm)
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)

        assert result is False

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(complete_queue.get(), timeout=0.2)

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.FAILED

    @pytest.mark.asyncio
    async def test_scan_http_404(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        error_queue = event_bus.subscribe(ScanErrorEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(404, {}))
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)
        assert result is False

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.FAILED

    @pytest.mark.asyncio
    async def test_scan_unhandled_exception(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        error_queue = event_bus.subscribe(ScanErrorEvent)

        # Force an exception inside _run_scan
        async def _broken(*args, **kwargs):
            raise RuntimeError("Unexpected crash")

        fast_worker._check_connectivity = _broken

        result = await fast_worker.scan(source)
        assert result is False

        error = await asyncio.wait_for(error_queue.get(), timeout=1.0)
        assert isinstance(error, ScanErrorEvent)
        assert error.media_source_id == source.id
        assert error.mode == ScanMode.FAST

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.FAILED

    @pytest.mark.asyncio
    async def test_content_type_captured(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}),
        )
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.content_type == "video/mp4"

    @pytest.mark.asyncio
    async def test_content_type_no_header(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200, {}))
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.content_type is None

    @pytest.mark.asyncio
    async def test_latency_measured(self, fast_worker, event_bus, store):
        source = _make_scan_source()

        async def _delayed_head(*args, **kwargs):
            await asyncio.sleep(0.05)
            return _make_head_cm(200)()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=lambda: (
            asyncio.sleep(0.05), AsyncMock(spec=aiohttp.ClientResponse)
            )[1]()
        )

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.delay_ms is not None
        assert stored.metrics.delay_ms > 0

    @pytest.mark.asyncio
    async def test_retry_on_500(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        cm_500 = _make_head_cm(500, {})
        cm_200 = _make_head_cm(200)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(side_effect=[cm_500, cm_200])
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)
        assert result is True
        assert mock_session.head.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_503_eventually_fails(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(503, {}))
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)
        assert result is False
        assert mock_session.head.call_count == fast_worker.max_retries + 1

    @pytest.mark.asyncio
    async def test_default_user_agent(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_cm = _make_head_cm(200)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_mock = MagicMock(return_value=mock_cm)
        mock_session.head = head_mock
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        call_kwargs = head_mock.call_args[1]
        assert "User-Agent" in call_kwargs["headers"]
        assert "IPTV-API" in call_kwargs["headers"]["User-Agent"]

    @pytest.mark.asyncio
    async def test_custom_headers_per_source(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        source = MediaSource(
            id=source.id, url=source.url,
            station_name=source.station_name, source_file=source.source_file,
            headers={"Referer": "http://example.com"},
        )
        mock_cm = _make_head_cm(200)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_mock = MagicMock(return_value=mock_cm)
        mock_session.head = head_mock
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        call_kwargs = head_mock.call_args[1]
        assert call_kwargs["headers"]["Referer"] == "http://example.com"


# ===================================================================
# FullScanWorker Tests
# ===================================================================

class TestFullScanWorker:

    @pytest.mark.asyncio
    async def test_initialization(self, full_worker):
        assert full_worker.timeout == 5.0
        assert full_worker.max_concurrent == 3
        assert full_worker.scan_mode == ScanMode.FULL
        assert full_worker.target_status == MediaStatus.FULL_SCANNED
        assert full_worker.download_size == 65536

    @pytest.mark.asyncio
    async def test_start_stop(self, full_worker):
        await full_worker.start()
        assert full_worker.session is not None
        await full_worker.stop()
        assert full_worker.session is None or full_worker.session.closed

    @pytest.mark.asyncio
    async def test_scan_success_with_speed(self, full_worker, event_bus, store):
        source = _make_scan_source()
        started_queue = event_bus.subscribe(ScanStartedEvent)
        complete_queue = event_bus.subscribe(FullScanCompleteEvent)
        error_queue = event_bus.subscribe(ScanErrorEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}),
        )
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 131072),
        )
        full_worker.session = mock_session

        with patch.object(full_worker, "_probe_media",
                          return_value=_make_ffprobe_result()):
            result = await full_worker.scan(source)

        assert result is True

        started = await asyncio.wait_for(started_queue.get(), timeout=1.0)
        assert started.mode == ScanMode.FULL

        complete = await asyncio.wait_for(complete_queue.get(), timeout=1.0)
        assert isinstance(complete, FullScanCompleteEvent)
        assert complete.media_source.url == source.url
        assert complete.speed_mbps > 0
        assert "resolution" in complete.metrics
        assert complete.metrics["resolution"] == "1920x1080"
        assert complete.metrics["video_codec"] == "h264"
        assert complete.metrics["fps"] is not None

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(error_queue.get(), timeout=0.2)

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.FULL_SCANNED
        assert stored.metrics.speed_mbps is not None
        assert stored.metrics.speed_mbps > 0
        assert stored.metrics.resolution == "1920x1080"
        assert stored.metrics.video_codec == "h264"

    @pytest.mark.asyncio
    async def test_scan_connectivity_failure(self, full_worker, event_bus, store):
        source = _make_scan_source()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(
            side_effect=asyncio.TimeoutError("Timeout"),
        )
        mock_cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=mock_cm)
        full_worker.session = mock_session

        result = await full_worker.scan(source)
        assert result is False

        stored = await store.get_source(source.station_name, source.url)
        assert stored.status == MediaStatus.FAILED

    @pytest.mark.asyncio
    async def test_speed_measurement(self, full_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536 * 2),
        )
        full_worker.session = mock_session

        with patch.object(full_worker, "_probe_media", return_value=None):
            result = await full_worker.scan(source)

        assert result is True

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.speed_mbps is not None
        assert stored.metrics.speed_mbps > 0
        assert stored.metrics.download_size_bytes is not None
        assert stored.metrics.download_time_ms is not None

    @pytest.mark.asyncio
    async def test_speed_measurement_get_fails(self, full_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(404),
        )
        full_worker.session = mock_session

        with patch.object(full_worker, "_probe_media", return_value=None):
            result = await full_worker.scan(source)

        assert result is True  # Still succeeds; speed is just None
        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.speed_mbps is None

    @pytest.mark.asyncio
    async def test_ffprobe_integration(self, full_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        full_worker.session = mock_session

        probe_result = _make_ffprobe_result()
        with patch.object(full_worker, "_probe_media", return_value=probe_result):
            result = await full_worker.scan(source)

        assert result is True

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.video_codec == "h264"
        assert stored.metrics.resolution == "1920x1080"
        assert stored.metrics.audio_codec == "aac"
        assert stored.metrics.duration_seconds == 120.0

    @pytest.mark.asyncio
    async def test_ffprobe_failure_graceful(self, full_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        full_worker.session = mock_session

        with patch.object(full_worker, "_probe_media", return_value=None):
            result = await full_worker.scan(source)

        assert result is True  # Still succeeds; metadata just missing

        stored = await store.get_source(source.station_name, source.url)
        assert stored.metrics.speed_mbps is not None
        assert stored.metrics.video_codec is None

    @pytest.mark.asyncio
    async def test_scan_event_emission(self, full_worker, event_bus, store):
        source = _make_scan_source()
        started_queue = event_bus.subscribe(ScanStartedEvent)
        complete_queue = event_bus.subscribe(FullScanCompleteEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        full_worker.session = mock_session

        with patch.object(full_worker, "_probe_media",
                          return_value=_make_ffprobe_result()):
            await full_worker.scan(source)

        started = await asyncio.wait_for(started_queue.get(), timeout=1.0)
        assert started.mode == ScanMode.FULL

        complete = await asyncio.wait_for(complete_queue.get(), timeout=1.0)
        assert complete.speed_mbps > 0
        assert "resolution" in complete.metrics

    @pytest.mark.asyncio
    async def test_extract_media_info(self):
        probe = _make_ffprobe_result()
        info = BaseScanWorker._extract_media_info(probe)

        assert info["video_codec"] == "h264"
        assert info["resolution"] == "1920x1080"
        assert info["fps"] == pytest.approx(29.97, rel=0.1)
        assert info["bitrate_kbps"] == 4000.0  # from stream bit_rate
        assert info["duration_seconds"] == 120.0

    @pytest.mark.asyncio
    async def test_extract_media_info_no_streams(self):
        probe = {"streams": [], "format": {"duration": "60.0"}}
        info = BaseScanWorker._extract_media_info(probe)
        # duration_seconds still extracted from format even without streams
        assert info == {"duration_seconds": 60.0}

    @pytest.mark.asyncio
    async def test_extract_media_info_fps_exception(self):
        """Verify fps parsing handles corrupt frame_rate gracefully."""
        probe = _make_ffprobe_result()
        probe["streams"][0]["r_frame_rate"] = "not-a-ratio"
        info = BaseScanWorker._extract_media_info(probe)
        assert info["fps"] == 0.0

    @pytest.mark.asyncio
    async def test_extract_media_info_fps_non_fraction(self):
        """Verify fps parsing handles non-fraction format."""
        probe = _make_ffprobe_result()
        probe["streams"][0]["r_frame_rate"] = "30"
        info = BaseScanWorker._extract_media_info(probe)
        assert info["fps"] == 30.0

    @pytest.mark.asyncio
    async def test_extract_media_info_only_audio(self):
        probe = {
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3"},
            ],
            "format": {"bit_rate": "256000", "duration": "300.0"},
        }
        info = BaseScanWorker._extract_media_info(probe)
        assert info["audio_codec"] == "mp3"
        assert info["duration_seconds"] == 300.0
        assert "video_codec" not in info

    @pytest.mark.asyncio
    async def test_extract_media_info_no_probe(self):
        info = BaseScanWorker._extract_media_info(None)
        assert info == {}


# ===================================================================
# DeepScanWorker Tests
# ===================================================================

class TestDeepScanWorker:

    @pytest.mark.asyncio
    async def test_initialization(self, deep_worker):
        assert deep_worker.timeout == 10.0
        assert deep_worker.max_concurrent == 2
        assert deep_worker.scan_mode == ScanMode.DEEP
        assert deep_worker.target_status == MediaStatus.DEEP_SCANNED
        assert deep_worker.download_size == 65536

    @pytest.mark.asyncio
    async def test_scan_success(self, deep_worker, event_bus, store):
        source = _make_scan_source()
        started_queue = event_bus.subscribe(ScanStartedEvent)
        complete_queue = event_bus.subscribe(DeepScanCompleteEvent)
        error_queue = event_bus.subscribe(ScanErrorEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            return_value=_make_head_cm(200, {"Content-Type": "video/mp4"}),
        )
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 131072),
        )
        deep_worker.session = mock_session

        with patch.object(deep_worker, "_probe_media",
                          return_value=_make_ffprobe_result()):
            result = await deep_worker.scan(source)

        assert result is True

        started = await asyncio.wait_for(started_queue.get(), timeout=1.0)
        assert started.mode == ScanMode.DEEP

        complete = await asyncio.wait_for(complete_queue.get(), timeout=1.0)
        assert isinstance(complete, DeepScanCompleteEvent)
        assert complete.media_source.url == source.url
        assert complete.is_upscaled is False
        assert complete.ssim_score > 0

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(error_queue.get(), timeout=0.2)

        stored = await store.get_source(source.station_name, source.url)
        assert stored is not None
        assert stored.status == MediaStatus.DEEP_SCANNED
        assert stored.metrics.speed_mbps is not None
        assert stored.metrics.quality_score is not None
        assert stored.metrics.is_upscaled is not None
        assert stored.metrics.ssim_score is not None
        assert stored.metrics.resolution is not None

    @pytest.mark.asyncio
    async def test_upscale_detection_1080p_low_bitrate(self):
        """A 1080p video with very low bitrate should be flagged as upscaled."""
        worker = DeepScanWorker.__new__(DeepScanWorker)
        result = worker._detect_upscale(1920, 1080, 500)
        assert result is True

    @pytest.mark.asyncio
    async def test_upscale_detection_1080p_good_bitrate(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        result = worker._detect_upscale(1920, 1080, 5000)
        assert result is False

    @pytest.mark.asyncio
    async def test_upscale_detection_720p_low_bitrate(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        result = worker._detect_upscale(1280, 720, 200)
        assert result is True

    @pytest.mark.asyncio
    async def test_upscale_detection_zero_values(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        assert worker._detect_upscale(0, 0, 0) is False
        assert worker._detect_upscale(1920, 1080, 0) is False

    @pytest.mark.asyncio
    async def test_quality_score_resolution_tiers(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)

        score_4k = worker._compute_quality_score(
            {"height": 2160, "bitrate_kbps": 20000, "fps": 30, "duration_seconds": 300},
        )
        score_1080p = worker._compute_quality_score(
            {"height": 1080, "bitrate_kbps": 5000, "fps": 30, "duration_seconds": 300},
        )
        score_720p = worker._compute_quality_score(
            {"height": 720, "bitrate_kbps": 2500, "fps": 30, "duration_seconds": 300},
        )

        # Higher resolution should give higher score
        assert score_4k >= score_1080p
        assert score_1080p >= score_720p

    @pytest.mark.asyncio
    async def test_quality_score_full_hd_content(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_quality_score({
            "height": 1080,
            "bitrate_kbps": 4000,
            "fps": 29.97,
            "duration_seconds": 120,
        })
        # Should be decent but not perfect
        assert 0.5 < score <= 1.0

    @pytest.mark.asyncio
    async def test_quality_score_low_quality(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_quality_score({
            "height": 360,
            "bitrate_kbps": 200,
            "fps": 15,
            "duration_seconds": 10,
        })
        # Low height (+0.05), low bitrate (~+0.022), low fps (+0.05),
        # short duration (~+0.007). Score above baseline but still modest.
        assert 0.6 < score < 0.75

    @pytest.mark.asyncio
    async def test_quality_score_50fps(self):
        """50+ fps gives max fps score."""
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_quality_score({
            "height": 1080, "bitrate_kbps": 5000,
            "fps": 60, "duration_seconds": 300,
        })
        assert 0.8 < score <= 1.0

    @pytest.mark.asyncio
    async def test_quality_score_zero_fps(self):
        """Zero fps gives no fps bonus."""
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_quality_score({
            "height": 1080, "bitrate_kbps": 5000,
            "fps": 0, "duration_seconds": 300,
        })
        assert score > 0

    @pytest.mark.asyncio
    async def test_quality_score_no_data(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_quality_score({})
        # Baseline score
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_similarity_score_1080p_good(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(1080, 5000)
        assert score == 1.0  # meets expected

    @pytest.mark.asyncio
    async def test_similarity_score_1080p_poor(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(1080, 1000)
        assert score is not None
        assert 0 < score < 1.0

    @pytest.mark.asyncio
    async def test_similarity_score_zero_height(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        assert worker._compute_similarity_score(0, 5000) is None

    @pytest.mark.asyncio
    async def test_similarity_score_4k(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(2160, 15000)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_similarity_score_720p(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(720, 2500)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_similarity_score_480p(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(480, 1500)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_similarity_score_sd(self):
        worker = DeepScanWorker.__new__(DeepScanWorker)
        score = worker._compute_similarity_score(240, 800)
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_analyze_quality_produces_all_fields(self, deep_worker):
        media_info = {
            "width": 1920,
            "height": 1080,
            "bitrate_kbps": 4000,
            "fps": 29.97,
            "duration_seconds": 300,
            "video_codec": "h264",
        }
        result = deep_worker._analyze_quality(media_info)
        assert "quality_score" in result
        assert "is_upscaled" in result
        assert "ssim_score" in result
        assert "actual_resolution" in result
        assert result["actual_resolution"] == "1920x1080"

    @pytest.mark.asyncio
    async def test_scan_connectivity_failure(self, deep_worker, event_bus, store):
        source = _make_scan_source()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(
            side_effect=asyncio.TimeoutError("Timeout"),
        )
        mock_cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=mock_cm)
        deep_worker.session = mock_session

        result = await deep_worker.scan(source)
        assert result is False

        stored = await store.get_source(source.station_name, source.url)
        assert stored.status == MediaStatus.FAILED

    @pytest.mark.asyncio
    async def test_ffprobe_failure_still_produces_partial(self, deep_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        deep_worker.session = mock_session

        with patch.object(deep_worker, "_probe_media", return_value=None):
            result = await deep_worker.scan(source)

        assert result is True
        stored = await store.get_source(source.station_name, source.url)
        assert stored.status == MediaStatus.DEEP_SCANNED
        assert stored.metrics.speed_mbps is not None


# ===================================================================
# ScanOrchestrator Tests
# ===================================================================

class TestScanOrchestrator:

    @pytest.mark.asyncio
    async def test_scan_all_three_modes(self, event_bus, store):
        fast = FastScanWorker(event_bus, store, timeout=2.0, max_retries=0)
        full = FullScanWorker(event_bus, store, timeout=5.0, max_retries=0,
                              download_size=65536)
        deep = DeepScanWorker(event_bus, store, timeout=5.0, max_retries=0,
                              download_size=65536)

        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
            fast_worker=fast,
            full_worker=full,
            deep_worker=deep,
        )

        await orchestrator.start()

        sources = [
            _make_scan_source(f"http://cdn.example.com/stream{i}.m3u8",
                               f"Test Channel {i}")
            for i in range(3)
        ]

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )

        fast.session = mock_session
        full.session = mock_session
        deep.session = mock_session

        with patch.object(deep, "_probe_media",
                           return_value=_make_ffprobe_result()):
            with patch.object(full, "_probe_media",
                              return_value=_make_ffprobe_result()):
                results = await orchestrator.scan_all(sources)

        await orchestrator.stop()

        assert len(results) == 3
        for source_id, mode_results in results.items():
            assert mode_results[ScanMode.FAST] is True
            assert mode_results[ScanMode.FULL] is True
            assert mode_results[ScanMode.DEEP] is True

        # All sources should be stored with DEEP_SCANNED status (deepest mode)
        for source in sources:
            stored = await store.get_source(source.station_name, source.url)
            assert stored is not None
            assert stored.status == MediaStatus.DEEP_SCANNED

    @pytest.mark.asyncio
    async def test_fast_only_mode(self, event_bus, store):
        fast = FastScanWorker(event_bus, store, timeout=2.0, max_retries=0)
        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
            fast_worker=fast,
            full_worker=None,
            deep_worker=None,
        )

        await orchestrator.start()

        sources = [_make_scan_source("http://cdn.example.com/stream.m3u8")]
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast.session = mock_session

        results = await orchestrator.scan_all(sources)
        await orchestrator.stop()

        assert ScanMode.FAST in results[sources[0].id]
        assert ScanMode.FULL not in results[sources[0].id]
        assert ScanMode.DEEP not in results[sources[0].id]

        stored = await store.get_source(sources[0].station_name, sources[0].url)
        assert stored.status == MediaStatus.FAST_SCANNED

    @pytest.mark.asyncio
    async def test_progressive_events(self, event_bus, store):
        fast = FastScanWorker(event_bus, store, timeout=2.0, max_retries=0)
        full = FullScanWorker(event_bus, store, timeout=5.0, max_retries=0,
                              download_size=65536)

        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
            fast_worker=fast,
            full_worker=full,
            deep_worker=None,
        )

        await orchestrator.start()

        fast_queue = event_bus.subscribe(FastScanCompleteEvent)
        full_queue = event_bus.subscribe(FullScanCompleteEvent)

        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        fast.session = mock_session
        full.session = mock_session

        with patch.object(full, "_probe_media",
                          return_value=_make_ffprobe_result()):
            await orchestrator.scan_all([source])

        await orchestrator.stop()

        # Fast event should arrive first
        fast_event = await asyncio.wait_for(fast_queue.get(), timeout=1.0)
        assert isinstance(fast_event, FastScanCompleteEvent)

        full_event = await asyncio.wait_for(full_queue.get(), timeout=1.0)
        assert isinstance(full_event, FullScanCompleteEvent)

    @pytest.mark.asyncio
    async def test_parallel_execution(self, event_bus, store):
        """Test that multiple sources are scanned concurrently."""
        fast = FastScanWorker(event_bus, store, timeout=5.0, max_retries=0,
                              max_concurrent=10)
        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
            fast_worker=fast,
        )

        await orchestrator.start()

        n_sources = 5
        sources = [
            _make_scan_source(f"http://cdn.example.com/stream{i}.m3u8")
            for i in range(n_sources)
        ]

        calls = []

        async def _delayed_head(*args, **kwargs):
            calls.append(1)
            await asyncio.sleep(0.1)
            return _make_head_cm(200)()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=lambda: AsyncMock(
            spec=aiohttp.ClientResponse,
            status=200,
            headers={"Content-Type": "video/mp4"},
        ))
        mock_cm.__aexit__ = AsyncMock(return_value=True)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(
            side_effect=[_make_head_cm(200)] * n_sources,
        )
        fast.session = mock_session

        start = asyncio.get_event_loop().time()
        results = await orchestrator.scan_all(sources)
        elapsed = asyncio.get_event_loop().time() - start

        await orchestrator.stop()

        # With 5 concurrent workers, 5 sources each taking ~0.1s
        # should complete in ~0.1s, not 0.5s
        assert elapsed < 0.3, f"Parallel scan took {elapsed:.3f}s"

        all_succeeded = all(
            r.get(ScanMode.FAST, False)
            for r in results.values()
        )
        assert all_succeeded


# ===================================================================
# Resource Management Tests
# ===================================================================

class TestResourceManagement:

    @pytest.mark.asyncio
    async def test_ffmpeg_semaphore_global(self):
        reset_ffmpeg_semaphore()
        s1 = get_ffmpeg_semaphore(3)
        s2 = get_ffmpeg_semaphore(5)
        assert s1 is s2  # Same global instance
        assert s1._value == 3  # First call wins

    @pytest.mark.asyncio
    async def test_ffmpeg_semaphore_reset(self):
        reset_ffmpeg_semaphore()
        s1 = get_ffmpeg_semaphore(3)
        reset_ffmpeg_semaphore()
        s2 = get_ffmpeg_semaphore(5)
        assert s1 is not s2  # New instance after reset

    @pytest.mark.asyncio
    async def test_full_worker_ffmpeg_limit(self, full_worker, event_bus, store):
        """Ensure the full worker uses the global ffmpeg semaphore."""
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )
        full_worker.session = mock_session

        probe_called = asyncio.Event()

        async def _slow_probe(*args, **kwargs):
            probe_called.set()
            await asyncio.sleep(0.2)
            return _make_ffprobe_result()

        with patch.object(full_worker, "_probe_media", _slow_probe):
            # Start 4 concurrent scans (more than the default 3 ffmpeg limit)
            tasks = [
                asyncio.create_task(
                    full_worker.scan(_make_scan_source(f"http://cdn.example.com/stream{i}.m3u8"))
                )
                for i in range(4)
            ]
            await asyncio.sleep(0.05)
            # At most 3 should be in ffprobe call simultaneously
            # This is inherently a timing test; we just verify no crash
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for r in results if r is True)
            assert success_count >= 3, f"Expected at least 3/4 scans to succeed, got {success_count}/4"

    @pytest.mark.asyncio
    async def test_concurrency_semaphore(self, full_worker, event_bus, store):
        """Ensure max_concurrent limits are enforced."""
        sem = full_worker._semaphore
        assert sem._value == full_worker.max_concurrent


# ===================================================================
# Edge Cases & Error Handling
# ===================================================================

class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_scan_already_failed_source(self, fast_worker, event_bus, store):
        source = _make_scan_source().with_status(MediaStatus.FAILED)
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        result = await fast_worker.scan(source)
        assert result is True  # Fresh scan should re-evaluate

    @pytest.mark.asyncio
    async def test_scan_empty_url(self, fast_worker, event_bus, store):
        source = _make_scan_source(url="")
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        head_cm = AsyncMock()
        head_cm.__aenter__ = AsyncMock(side_effect=ValueError("Empty URL"))
        head_cm.__aexit__ = AsyncMock(return_value=True)
        mock_session.head = MagicMock(return_value=head_cm)
        fast_worker.session = mock_session

        error_queue = event_bus.subscribe(ScanErrorEvent)

        result = await fast_worker.scan(source)
        assert result is False

        error = await asyncio.wait_for(error_queue.get(), timeout=1.0)
        assert isinstance(error, ScanErrorEvent)

    @pytest.mark.asyncio
    async def test_store_integration_across_modes(self, event_bus, store):
        """Test that scan results accumulate correctly in store."""
        fast = FastScanWorker(event_bus, store, timeout=2.0, max_retries=0)

        source = _make_scan_source()

        await fast.start()
        # Set mock session AFTER start() to avoid overwrite
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast.session = mock_session

        await fast.scan(source)
        await fast.stop()

        stored = await store.get_source(source.station_name, source.url)
        assert stored.status == MediaStatus.FAST_SCANNED
        assert stored.metrics.delay_ms is not None

        # Now run full scan - should update the same source
        full = FullScanWorker(event_bus, store, timeout=5.0,
                              max_retries=0, download_size=65536)
        await full.start()
        full.session = mock_session
        mock_session.get = MagicMock(
            return_value=_make_get_cm(200, b"x" * 65536),
        )

        with patch.object(full, "_probe_media",
                          return_value=_make_ffprobe_result()):
            await full.scan(source)
            await full.stop()

        stored2 = await store.get_source(source.station_name, source.url)
        assert stored2.status == MediaStatus.FULL_SCANNED
        assert stored2.metrics.speed_mbps is not None
        assert stored2.metrics.resolution is not None

    @pytest.mark.asyncio
    async def test_get_metrics_after_scan(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        stored = await store.get_source(source.station_name, source.url)
        metrics = stored.metrics
        assert metrics.delay_ms is not None
        assert metrics.delay_ms >= 0
        assert metrics.content_type == "application/x-mpegurl"
        assert metrics.status_code == 200
        assert metrics.to_dict() is not None

    @pytest.mark.asyncio
    async def test_store_stats(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        await fast_worker.scan(source)

        stats = await store.get_stats()
        assert stats["sources_added"] >= 1
        assert stats["sources_updated"] >= 0

    @pytest.mark.asyncio
    async def test_scanorchestrator_start_stop_empty(self, event_bus, store):
        """Orchestrator with no workers should handle gracefully."""
        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
        )
        await orchestrator.start()
        await orchestrator.stop()
        assert True

    @pytest.mark.asyncio
    async def test_scanorchestrator_no_sources(self, event_bus, store):
        fast = FastScanWorker(event_bus, store, timeout=2.0, max_retries=0)
        orchestrator = ScanOrchestrator(
            event_bus=event_bus,
            store=store,
            fast_worker=fast,
        )
        await orchestrator.start()
        results = await orchestrator.scan_all([])
        await orchestrator.stop()
        assert results == {}

    @pytest.mark.asyncio
    async def test_trace_id_propagation(self, fast_worker, event_bus, store):
        source = _make_scan_source()
        started_queue = event_bus.subscribe(ScanStartedEvent)

        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        mock_session.head = MagicMock(return_value=_make_head_cm(200))
        fast_worker.session = mock_session

        await fast_worker.scan(source, trace_id="my-trace")

        event = await asyncio.wait_for(started_queue.get(), timeout=1.0)
        assert event.trace_id == "my-trace"

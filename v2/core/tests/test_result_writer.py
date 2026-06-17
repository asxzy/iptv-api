"""
v2/core/tests/test_result_writer.py

Comprehensive tests for the ResultWorker.
Run with: python -m pytest core/tests/test_result_writer.py -v
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from collections import defaultdict

import sys
import os

sys.path.insert(0, "/Users/asxzy/src/iptv-api/v2")

from core.workers.result_writer import ResultWorker
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    ScoreUpdatedEvent,
    ScanJobCompletedEvent,
    ResultUpdatedEvent,
)
from core.types import (
    MediaSource,
    MediaMetrics,
    MediaStatus,
    generate_media_id,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def store():
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def result_worker(event_bus, store):
    worker = ResultWorker(
        event_bus=event_bus,
        store=store,
        write_interval=0.1,
        realtime_write=True,
    )
    return worker


def _source(
    url: str = "http://cdn.example.com/stream.m3u8",
    station: str = "Test Channel",
    metrics: MediaMetrics = None,
    status: MediaStatus = MediaStatus.SCORING_COMPLETE,
    source_file: str = "config/subscribe.txt",
    headers: dict = None,
) -> MediaSource:
    return MediaSource(
        id=generate_media_id(url, station),
        url=url,
        station_name=station,
        source_file=source_file,
        metrics=metrics or MediaMetrics(),
        status=status,
        headers=headers or {},
    )


def _scored_metrics(
    delay_ms: float = 300.0,
    speed_mbps: float = 20.0,
    resolution: str = "1920x1080",
    video_codec: str = "h264",
    fps: float = 30.0,
    quality_score: float = 0.85,
    loadability_score: float = 0.90,
    composite_score: float = 0.875,
) -> MediaMetrics:
    return MediaMetrics(
        delay_ms=delay_ms,
        speed_mbps=speed_mbps,
        resolution=resolution,
        video_codec=video_codec,
        fps=fps,
        quality_score=quality_score,
        loadability_score=loadability_score,
        composite_score=composite_score,
    )


# ── Mocking helpers ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_utils():
    """Mock all lazy-imported utils functions to avoid import cascades."""
    with patch("core.workers.result_writer.ResultWorker._get_write_channel_to_file") as mock_write, \
         patch("core.workers.result_writer.ResultWorker._get_config") as mock_cfg, \
         patch("core.workers.result_writer.ResultWorker._get_result_store") as mock_store:

        # Mock the write_channel_to_file function
        mock_write_fn = MagicMock()
        mock_write.return_value = mock_write_fn

        # Mock config
        mock_config = MagicMock()
        mock_config.open_realtime_write = True
        mock_config.ipv6_support = False
        mock_config.open_m3u_result = True
        mock_config.open_rtmp = False
        mock_config.urls_limit = 10
        mock_config.final_file = "output/result.txt"
        mock_cfg.return_value = mock_config

        # Mock result store
        mock_rs = MagicMock()
        mock_store.return_value = mock_rs

        yield {
            "write_fn": mock_write_fn,
            "config": mock_config,
            "result_store": mock_rs,
            "_get_write": mock_write,
            "_get_cfg": mock_cfg,
            "_get_store": mock_store,
        }


# ── Initialization ────────────────────────────────────────────────────────────


class TestInitialization:

    @pytest.mark.asyncio
    async def test_default_values(self, result_worker):
        assert result_worker.write_interval == 0.1
        assert result_worker.realtime_write is True
        assert result_worker._running is False
        assert result_worker._dirty is False
        assert result_worker._write_in_progress is False
        assert result_worker.get_metrics()["events_received"] == 0
        assert result_worker.get_metrics()["writes_completed"] == 0
        assert result_worker.get_metrics()["writes_failed"] == 0

    @pytest.mark.asyncio
    async def test_realtime_write_from_config(self, event_bus, store, mock_utils):
        mock_utils["config"].open_realtime_write = False
        worker = ResultWorker(event_bus=event_bus, store=store)
        assert worker.realtime_write is False

    @pytest.mark.asyncio
    async def test_custom_realtime_write_override(self, event_bus, store, mock_utils):
        mock_utils["config"].open_realtime_write = True
        worker = ResultWorker(event_bus=event_bus, store=store, realtime_write=False)
        assert worker.realtime_write is False

    @pytest.mark.asyncio
    async def test_custom_write_interval(self, event_bus, store):
        worker = ResultWorker(event_bus=event_bus, store=store, write_interval=5.0)
        assert worker.write_interval == 5.0

    @pytest.mark.asyncio
    async def test_start_stop(self, result_worker):
        assert result_worker._running is False
        await result_worker.start()
        assert result_worker._running is True
        await result_worker.stop()
        assert result_worker._running is False


# ── Event handling ────────────────────────────────────────────────────────────


class TestEventHandling:

    @pytest.mark.asyncio
    async def test_score_update_sets_dirty_and_triggers_debounce(
        self, result_worker, store
    ):
        await result_worker.start()
        ms = _source(metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        event = ScoreUpdatedEvent(
            media_source_id=ms.id,
            station_name=ms.station_name,
            quality_score=0.85,
            loadability_score=0.9,
            composite_score=0.875,
        )

        q = asyncio.Queue()
        q.put_nowait(event)
        task = asyncio.create_task(result_worker.process_queue(q))

        await asyncio.sleep(0.05)

        assert result_worker._dirty is True
        assert result_worker.get_metrics()["events_received"] == 1
        assert result_worker.get_metrics()["writes_triggered"] == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_scan_complete_triggers_flush(
        self, result_worker, store, mock_utils
    ):
        await result_worker.start()

        ms = _source(metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        event = ScanJobCompletedEvent(
            job_id="test-job",
            total_sources=1,
            succeeded=1,
            failed=0,
            elapsed_seconds=10.0,
        )

        q = asyncio.Queue()
        q.put_nowait(event)
        task = asyncio.create_task(result_worker.process_queue(q))

        await asyncio.sleep(0.15)

        assert mock_utils["write_fn"].called
        assert result_worker.get_metrics()["writes_completed"] == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_unknown_event_ignored(self, result_worker):
        await result_worker.start()

        from core.events import Event

        class UnknownEvent(Event):
            pass

        q = asyncio.Queue()
        q.put_nowait(UnknownEvent())
        task = asyncio.create_task(result_worker.process_queue(q))

        await asyncio.sleep(0.05)

        assert result_worker.get_metrics()["events_received"] == 1
        assert result_worker.get_metrics()["writes_triggered"] == 0
        assert result_worker._dirty is False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_event_error_does_not_crash(self, result_worker):
        await result_worker.start()

        with patch.object(
            result_worker, "_handle_score_update", side_effect=RuntimeError("test error")
        ):
            q = asyncio.Queue()
            q.put_nowait(ScoreUpdatedEvent(station_name="test"))
            task = asyncio.create_task(result_worker.process_queue(q))
            await asyncio.sleep(0.1)
            assert result_worker._running is True

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await result_worker.stop()


# ── Debounce behavior ─────────────────────────────────────────────────────────


class TestDebounce:

    @pytest.mark.asyncio
    async def test_debounce_combines_multiple_updates(
        self, result_worker, store, mock_utils
    ):
        await result_worker.start()

        for i in range(5):
            ms = _source(
                url=f"http://cdn.example.com/stream{i}.m3u8",
                metrics=_scored_metrics(composite_score=0.9 - i * 0.1),
            )
            await store.add_or_update_source(ms)

        q = asyncio.Queue()
        for i in range(5):
            event = ScoreUpdatedEvent(
                media_source_id=f"id-{i}",
                station_name="Test Channel",
                quality_score=0.9 - i * 0.1,
                loadability_score=0.9 - i * 0.1,
                composite_score=0.9 - i * 0.1,
            )
            q.put_nowait(event)

        task = asyncio.create_task(result_worker.process_queue(q))

        await asyncio.sleep(0.3)

        assert mock_utils["write_fn"].call_count == 1
        assert result_worker.get_metrics()["writes_completed"] == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_realtime_write_disabled_no_automatic_flush(
        self, event_bus, store, mock_utils
    ):
        mock_utils["config"].open_realtime_write = False
        worker = ResultWorker(
            event_bus=event_bus,
            store=store,
            write_interval=0.1,
            realtime_write=False,
        )
        await worker.start()

        ms = _source(metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        event = ScoreUpdatedEvent(
            media_source_id=ms.id,
            station_name="Test Channel",
            quality_score=0.85,
            loadability_score=0.9,
            composite_score=0.875,
        )

        q = asyncio.Queue()
        q.put_nowait(event)
        task = asyncio.create_task(worker.process_queue(q))

        await asyncio.sleep(0.3)

        assert mock_utils["write_fn"].called is False

        q.put_nowait(ScanJobCompletedEvent(job_id="test"))
        await asyncio.sleep(0.15)

        assert mock_utils["write_fn"].called is True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await worker.stop()

    @pytest.mark.asyncio
    async def test_consecutive_updates_reset_debounce(
        self, result_worker, store, mock_utils
    ):
        await result_worker.start()

        ms = _source(metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        q = asyncio.Queue()
        task = asyncio.create_task(result_worker.process_queue(q))

        q.put_nowait(ScoreUpdatedEvent(
            media_source_id=ms.id, station_name="Test Channel",
            quality_score=0.85, loadability_score=0.9, composite_score=0.875,
        ))
        await asyncio.sleep(0.03)
        q.put_nowait(ScoreUpdatedEvent(
            media_source_id=ms.id, station_name="Test Channel",
            quality_score=0.90, loadability_score=0.95, composite_score=0.925,
        ))

        await asyncio.sleep(0.3)

        assert mock_utils["write_fn"].call_count == 1
        assert result_worker.get_metrics()["writes_completed"] == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()


# ── Data conversion ───────────────────────────────────────────────────────────


class TestDataConversion:

    @pytest.mark.asyncio
    async def test_build_category_channel_data_empty_store(self, result_worker, store):
        data = result_worker._build_category_channel_data()
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_build_category_channel_data_single_source(self, result_worker, store):
        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data) == 1
        assert "subscribe" in data
        assert "CCTV1" in data["subscribe"]
        assert len(data["subscribe"]["CCTV1"]) == 1
        item = data["subscribe"]["CCTV1"][0]
        assert item["url"] == "http://cdn.example.com/stream.m3u8"
        assert item["origin"] == "subscribe"
        assert item["resolution"] == "1920x1080"

    @pytest.mark.asyncio
    async def test_build_category_channel_data_multiple_sources_same_station(
        self, result_worker, store
    ):
        urls = [
            ("http://cdn.example.com/high.m3u8", _scored_metrics(composite_score=0.9)),
            ("http://cdn.example.com/medium.m3u8", _scored_metrics(composite_score=0.7)),
            ("http://cdn.example.com/low.m3u8", _scored_metrics(composite_score=0.5)),
        ]
        for url, metrics in urls:
            ms = _source(url=url, station="CCTV1", metrics=metrics)
            await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data["subscribe"]["CCTV1"]) == 3

        items = data["subscribe"]["CCTV1"]
        assert items[0]["url"] == "http://cdn.example.com/high.m3u8"
        assert items[1]["url"] == "http://cdn.example.com/medium.m3u8"
        assert items[2]["url"] == "http://cdn.example.com/low.m3u8"

    @pytest.mark.asyncio
    async def test_build_category_channel_data_multiple_stations(
        self, result_worker, store
    ):
        stations = ["CCTV1", "CCTV2", "CCTV3"]
        for i, name in enumerate(stations):
            ms = _source(
                url=f"http://cdn.example.com/{name}.m3u8",
                station=name,
                metrics=_scored_metrics(composite_score=0.9 - i * 0.1),
            )
            await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data) == 1
        assert len(data["subscribe"]) == 3

    @pytest.mark.asyncio
    async def test_origin_mapping_from_source_file(self, result_worker, store):
        sources = [
            ("config/subscribe.txt", "subscribe"),
            ("config/local.txt", "local"),
            ("config/whitelist.txt", "whitelist"),
        ]
        for src_file, expected_origin in sources:
            ms = _source(
                url=f"http://cdn.example.com/{expected_origin}.m3u8",
                source_file=src_file,
                metrics=_scored_metrics(),
            )
            await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        all_items = []
        for channels in data.values():
            for items in channels.values():
                all_items.extend(items)

        origins = {item["origin"] for item in all_items}
        assert "subscribe" in origins
        assert "local" in origins
        assert "whitelist" in origins

    @pytest.mark.asyncio
    async def test_sources_without_metrics_use_defaults(self, result_worker, store):
        ms = _source(metrics=MediaMetrics())
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data["subscribe"]["Test Channel"]) == 1
        item = data["subscribe"]["Test Channel"][0]
        assert item["resolution"] is None
        assert item["fps"] is None

    @pytest.mark.asyncio
    async def test_infer_category_fallback(self, result_worker, store):
        ms = _source(source_file="unknown.txt", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert "list" in data
        assert len(data["list"]["Test Channel"]) == 1


# ── File generation ──────────────────────────────────────────────────────────


class TestFileGeneration:

    @pytest.mark.asyncio
    async def test_flush_calls_write_channel_to_file(
        self, result_worker, store, mock_utils
    ):
        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        result_worker._dirty = True
        await result_worker._flush()

        assert mock_utils["write_fn"].called
        args, kwargs = mock_utils["write_fn"].call_args
        data = args[0]
        assert "subscribe" in data
        assert "CCTV1" in data["subscribe"]

    @pytest.mark.asyncio
    async def test_flush_not_called_when_not_dirty(self, result_worker, mock_utils):
        result_worker._dirty = False
        await result_worker._flush()
        assert not mock_utils["write_fn"].called

    @pytest.mark.asyncio
    async def test_flush_not_called_when_write_in_progress(
        self, result_worker, store, mock_utils
    ):
        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        result_worker._dirty = True
        result_worker._write_in_progress = True
        await result_worker._flush()
        assert not mock_utils["write_fn"].called

    @pytest.mark.asyncio
    async def test_flush_handles_error_gracefully(
        self, result_worker, store, mock_utils
    ):
        mock_utils["write_fn"].side_effect = RuntimeError("write error")

        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        result_worker._dirty = True
        await result_worker._flush()

        assert result_worker.get_metrics()["writes_failed"] == 1

    @pytest.mark.asyncio
    async def test_flush_updates_result_store(
        self, result_worker, store, mock_utils
    ):
        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        result_worker._dirty = True
        await result_worker._flush()

        assert mock_utils["result_store"].store_data.called

    @pytest.mark.asyncio
    async def test_flush_emits_result_updated_event(
        self, result_worker, store, mock_utils
    ):
        event_queue = result_worker.event_bus.subscribe(ResultUpdatedEvent)

        ms = _source(station="CCTV1", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        result_worker._dirty = True
        await result_worker._flush()

        event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
        assert isinstance(event, ResultUpdatedEvent)
        assert event.total_stations == 1
        assert event.total_sources == 1

    @pytest.mark.asyncio
    async def test_flush_with_empty_store_does_not_write(
        self, result_worker, mock_utils
    ):
        result_worker._dirty = True
        await result_worker._flush()
        assert not mock_utils["write_fn"].called


# ── Integration with store ────────────────────────────────────────────────────


class TestStoreIntegration:

    @pytest.mark.asyncio
    async def test_score_update_processed_through_store(
        self, result_worker, store, mock_utils
    ):
        await result_worker.start()

        ms = _source(metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        event = ScoreUpdatedEvent(
            media_source_id=ms.id,
            station_name=ms.station_name,
            quality_score=0.85,
            loadability_score=0.9,
            composite_score=0.875,
        )

        q = asyncio.Queue()
        q.put_nowait(event)
        task = asyncio.create_task(result_worker.process_queue(q))

        await asyncio.sleep(0.3)

        assert result_worker.get_metrics()["writes_completed"] >= 1

        if mock_utils["write_fn"].called:
            args, _ = mock_utils["write_fn"].call_args
            data = args[0]
            found = False
            for cate, channels in data.items():
                for name, items in channels.items():
                    for item in items:
                        if item["url"] == ms.url:
                            found = True
            assert found

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_multiple_stations_in_store(
        self, result_worker, store, mock_utils
    ):
        for i in range(5):
            ms = _source(
                url=f"http://cdn.example.com/stream{i}.m3u8",
                station=f"Channel {i}",
                metrics=_scored_metrics(composite_score=0.9 - i * 0.1),
            )
            await store.add_or_update_source(ms)

        result_worker._dirty = True
        await result_worker._flush()

        assert mock_utils["write_fn"].called
        args, _ = mock_utils["write_fn"].call_args
        data = args[0]

        total_channels = sum(len(channels) for channels in data.values())
        assert total_channels == 5


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_store_no_crash(self, result_worker, mock_utils):
        result_worker._dirty = True
        await result_worker._flush()
        assert not mock_utils["write_fn"].called

    @pytest.mark.asyncio
    async def test_station_with_no_sources(
        self, result_worker, store, mock_utils
    ):
        await store.get_or_create_station("Empty Station")
        result_worker._dirty = True
        await result_worker._flush()
        assert not mock_utils["write_fn"].called

    @pytest.mark.asyncio
    async def test_source_with_partial_metrics(self, result_worker, store):
        metrics = MediaMetrics(
            speed_mbps=15.0,
            delay_ms=200.0,
        )
        ms = _source(metrics=metrics)
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data["subscribe"]["Test Channel"]) == 1
        item = data["subscribe"]["Test Channel"][0]
        assert item["resolution"] is None
        assert item["fps"] is None
        assert item["url"] == "http://cdn.example.com/stream.m3u8"
        assert item["origin"] == "subscribe"

    @pytest.mark.asyncio
    async def test_special_characters_in_station_name(self, result_worker, store):
        ms = _source(
            station="CCTV-1/HD \u6d4b\u8bd5 (Backup)",
            url="http://cdn.example.com/test.m3u8",
            metrics=_scored_metrics(),
        )
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert "CCTV-1/HD \u6d4b\u8bd5 (Backup)" in data["subscribe"]

    @pytest.mark.asyncio
    async def test_very_long_url(self, result_worker, store):
        long_url = "http://cdn.example.com/" + "a" * 500 + ".m3u8"
        ms = _source(url=long_url, metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data["subscribe"]["Test Channel"]) == 1
        assert data["subscribe"]["Test Channel"][0]["url"] == long_url

    @pytest.mark.asyncio
    async def test_source_with_empty_url_does_not_break(self, result_worker, store):
        ms = _source(url="", metrics=_scored_metrics())
        await store.add_or_update_source(ms)

        data = result_worker._build_category_channel_data()
        assert len(data["subscribe"]["Test Channel"]) == 1
        assert data["subscribe"]["Test Channel"][0]["url"] == ""


# ── Concurrent behavior ───────────────────────────────────────────────────────


class TestConcurrentBehavior:

    @pytest.mark.asyncio
    async def test_concurrent_score_updates_no_race(
        self, result_worker, store, mock_utils
    ):
        await result_worker.start()

        async def send_score(i: int):
            ms = _source(
                url=f"http://cdn.example.com/stream{i}.m3u8",
                station=f"Channel {i % 5}",
                metrics=_scored_metrics(composite_score=0.9),
            )
            await store.add_or_update_source(ms)
            return ScoreUpdatedEvent(
                media_source_id=ms.id,
                station_name=f"Channel {i % 5}",
                quality_score=0.85,
                loadability_score=0.9,
                composite_score=0.875,
            )

        events = await asyncio.gather(*[send_score(i) for i in range(20)])

        q = asyncio.Queue()
        for evt in events:
            q.put_nowait(evt)

        task = asyncio.create_task(result_worker.process_queue(q))
        await asyncio.sleep(0.5)

        assert result_worker.get_metrics()["writes_completed"] >= 1
        assert result_worker.get_metrics()["events_received"] == 20

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await result_worker.stop()

    @pytest.mark.asyncio
    async def test_consecutive_starts_stops(self, result_worker):
        for _ in range(3):
            await result_worker.start()
            assert result_worker._running is True
            await result_worker.stop()
            assert result_worker._running is False


# ── Helper methods ────────────────────────────────────────────────────────────


class TestHelperMethods:

    def test_get_origin_subscribe(self):
        source = _source(source_file="config/subscribe.txt")
        assert ResultWorker._get_origin(source) == "subscribe"

    def test_get_origin_local(self):
        source = _source(source_file="config/local.txt")
        assert ResultWorker._get_origin(source) == "local"

    def test_get_origin_whitelist(self):
        source = _source(source_file="config/whitelist.txt")
        assert ResultWorker._get_origin(source) == "whitelist"

    def test_get_origin_hls(self):
        source = _source(source_file="config/hls.txt")
        assert ResultWorker._get_origin(source) == "hls"

    def test_get_origin_default(self):
        source = _source(source_file="unknown.txt")
        assert ResultWorker._get_origin(source) == "subscribe"

    def test_get_origin_empty_source_file(self):
        source = _source(source_file="")
        assert ResultWorker._get_origin(source) == "subscribe"

    @pytest.mark.asyncio
    async def test_infer_category_from_subscribe_source(self, result_worker, store):
        ms = _source(source_file="config/subscribe.txt")
        await store.add_or_update_source(ms)
        data = result_worker._build_category_channel_data()
        assert "subscribe" in data

    @pytest.mark.asyncio
    async def test_infer_category_fallback_to_list(self, result_worker, store):
        ms = _source(source_file="some_custom_file.txt")
        await store.add_or_update_source(ms)
        data = result_worker._build_category_channel_data()
        assert "list" in data

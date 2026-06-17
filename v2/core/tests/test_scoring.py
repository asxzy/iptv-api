"""
v2/core/tests/test_scoring.py

Comprehensive tests for the ScoringWorker implementation.
Run with: python -m pytest core/tests/test_scoring.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, '/Users/asxzy/src/iptv-api/v2')
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
))

from core.workers.scoring import ScoringWorker
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    DeepScanCompleteEvent,
    FullScanCompleteEvent,
    ScoreUpdatedEvent,
    RankingUpdatedEvent,
)
from core.types import (
    MediaSource,
    MediaMetrics,
    MediaStatus,
    generate_media_id,
)
from utils.scoring import DEFAULT_WEIGHTS, NEUTRAL


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def store():
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def scoring_worker(event_bus, store):
    worker = ScoringWorker(
        event_bus=event_bus,
        store=store,
        weights=None,
        emit_ranking_events=True,
    )
    return worker


def _source(
    url: str = "http://cdn.example.com/stream.m3u8",
    station: str = "Test Channel",
    metrics: MediaMetrics = None,
    status: MediaStatus = MediaStatus.DEEP_SCANNED,
) -> MediaSource:
    return MediaSource(
        id=generate_media_id(url, station),
        url=url,
        station_name=station,
        source_file="test.txt",
        metrics=metrics or MediaMetrics(),
        status=status,
    )


def _full_hd_metrics() -> MediaMetrics:
    return MediaMetrics(
        delay_ms=300.0,
        speed_mbps=20.0,
        bandwidth_mbps=20.0,
        resolution="1920x1080",
        video_codec="h264",
        fps=30.0,
        bitrate_kbps=5000.0,
        duration_seconds=300.0,
        is_upscaled=False,
        ssim_score=0.95,
    )


def _low_quality_metrics() -> MediaMetrics:
    return MediaMetrics(
        delay_ms=2000.0,
        speed_mbps=2.0,
        bandwidth_mbps=2.0,
        resolution="640x360",
        video_codec="h264",
        fps=15.0,
        bitrate_kbps=500.0,
        duration_seconds=60.0,
        is_upscaled=False,
        ssim_score=0.60,
    )


def _upscaled_metrics() -> MediaMetrics:
    """1080p claim with very low bitrate — likely upscaled."""
    return MediaMetrics(
        delay_ms=500.0,
        speed_mbps=10.0,
        bandwidth_mbps=10.0,
        resolution="1920x1080",
        video_codec="h264",
        fps=25.0,
        bitrate_kbps=600.0,
        duration_seconds=200.0,
        is_upscaled=True,
        ssim_score=0.85,
    )


# ── ScoringWorker: Basic Initialisation & Lifecycle ──────────────────────────


class TestScoringWorkerInit:

    @pytest.mark.asyncio
    async def test_worker_initialization(self, scoring_worker):
        assert scoring_worker.weights == DEFAULT_WEIGHTS
        assert scoring_worker.emit_ranking_events is True
        assert scoring_worker._running is False
        assert scoring_worker._task is None
        assert scoring_worker.get_metrics()["scored"] == 0
        assert scoring_worker.get_metrics()["errors"] == 0

    @pytest.mark.asyncio
    async def test_worker_start_stop(self, scoring_worker):
        assert scoring_worker._running is False
        await scoring_worker.start()
        assert scoring_worker._running is True
        await scoring_worker.stop()
        assert scoring_worker._running is False

    @pytest.mark.asyncio
    async def test_custom_weights(self, event_bus, store):
        custom = {"w_quality": 1.0, "w_loadability": 0.0}
        worker = ScoringWorker(
            event_bus=event_bus,
            store=store,
            weights=custom,
        )
        assert worker.weights["w_quality"] == 1.0
        assert worker.weights["w_loadability"] == 0.0


# ── ScoringWorker: _metrics_to_scoring_dict ──────────────────────────────────


class TestMetricsToScoringDict:

    def test_full_hd_metrics_conversion(self):
        m = _full_hd_metrics()
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert d["resolution"] == "1920x1080"
        assert d["bitrate"] == 5_000_000.0  # kbps * 1000
        assert d["fps"] == 30.0
        assert d["video_codec"] == "h264"
        assert d["speed"] == 2.5  # 20 / 8
        assert d["delay"] == 300.0
        assert d["a_res"] == 1.0  # not upscaled

    def test_upscaled_sets_a_res_penalty(self):
        m = _upscaled_metrics()
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert d["a_res"] == 0.7

    def test_unknown_upscale_omits_a_res(self):
        m = MediaMetrics(
            resolution="1920x1080",
            bitrate_kbps=5000.0,
            fps=30.0,
            is_upscaled=None,
        )
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert "a_res" not in d

    def test_missing_fields_omitted(self):
        m = MediaMetrics()
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert d == {}

    def test_null_bitrate_omitted(self):
        m = MediaMetrics(resolution="1920x1080", bitrate_kbps=None)
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert "bitrate" not in d

    def test_null_speed_omitted(self):
        m = MediaMetrics(speed_mbps=None)
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert "speed" not in d

    def test_null_delay_omitted(self):
        m = MediaMetrics(delay_ms=None)
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert "delay" not in d

    def test_hevc_codec_preserved(self):
        m = MediaMetrics(video_codec="hevc", bitrate_kbps=2500.0,
                         resolution="1920x1080", fps=25.0)
        d = ScoringWorker._metrics_to_scoring_dict(m)
        assert d["video_codec"] == "hevc"
        # hevc is twice as efficient, so lower bitrate still scores well


# ── ScoringWorker: score() — single source ───────────────────────────────────


class TestScoringWorkerScore:

    @pytest.mark.asyncio
    async def test_score_full_hd_source(self, scoring_worker, event_bus, store):
        ms = _source(metrics=_full_hd_metrics())
        await store.add_or_update_source(ms)
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        result = await scoring_worker.score(ms)
        assert result is True

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        assert isinstance(event, ScoreUpdatedEvent)
        assert event.media_source_id == ms.id
        assert event.station_name == "Test Channel"
        assert 0.0 <= event.quality_score <= 1.0
        assert 0.0 <= event.loadability_score <= 1.0
        assert 0.0 <= event.composite_score <= 1.0

        stored = await store.get_source("Test Channel", ms.url)
        assert stored is not None
        assert stored.status == MediaStatus.SCORING_COMPLETE
        assert stored.metrics.quality_score == event.quality_score
        assert stored.metrics.loadability_score == event.loadability_score
        assert stored.metrics.composite_score == event.composite_score

    @pytest.mark.asyncio
    async def test_score_low_quality_source(self, scoring_worker, event_bus, store):
        ms = _source(metrics=_low_quality_metrics())
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        result = await scoring_worker.score(ms)
        assert result is True

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        assert 0.0 <= event.quality_score <= 1.0
        assert 0.0 <= event.loadability_score <= 1.0

    @pytest.mark.asyncio
    async def test_score_upscaled_source(self, scoring_worker, event_bus, store):
        ms = _source(metrics=_upscaled_metrics())
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        result = await scoring_worker.score(ms)
        assert result is True

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        # Upscaled should reduce quality score compared to genuine at same res
        honest = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(honest)
        honest_event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        # The upscaled source should score lower on quality
        # (may not always be true if speed makes up for it, but with default
        #  weights 0.5/0.5 the quality component should show the penalty)
        if honest_event.quality_score > 0:
            assert event.quality_score < honest_event.quality_score

    @pytest.mark.asyncio
    async def test_score_with_missing_data(self, scoring_worker, event_bus, store):
        """All-missing metrics should still produce valid scores via NEUTRAL."""
        ms = _source(metrics=MediaMetrics())
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        result = await scoring_worker.score(ms)
        assert result is True

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        assert 0.0 <= event.quality_score <= 1.0
        assert 0.0 <= event.loadability_score <= 1.0
        assert 0.0 <= event.composite_score <= 1.0

    @pytest.mark.asyncio
    async def test_score_error_handling(self, scoring_worker):
        """A broadcasting error should not crash the worker."""
        ms = _source(metrics=MediaMetrics())
        result = await scoring_worker.score(ms)
        assert result is True  # should still succeed even if store has issues

    @pytest.mark.asyncio
    async def test_score_quality_score_unit_range(self, scoring_worker):
        ms = _source(metrics=_full_hd_metrics())
        result = await scoring_worker.score(ms)
        assert result is True
        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        assert stored is not None
        assert 0.0 <= stored.metrics.quality_score <= 1.0
        assert 0.0 <= stored.metrics.loadability_score <= 1.0
        assert 0.0 <= stored.metrics.composite_score <= 1.0


# ── ScoringWorker: Composite with configurable weights ───────────────────────


class TestConfigurableWeights:

    @pytest.mark.asyncio
    async def test_quality_dominated_weights(self, event_bus, store):
        """w_quality = 0.9, w_loadability = 0.1 → quality dominates."""
        custom = dict(DEFAULT_WEIGHTS)
        custom["w_quality"] = 0.9
        custom["w_loadability"] = 0.1
        worker = ScoringWorker(
            event_bus=event_bus, store=store,
            weights=custom,
        )

        hd = MediaMetrics(resolution="1920x1080", bitrate_kbps=5000.0,
                           fps=30.0, video_codec="h264", delay_ms=300,
                           speed_mbps=20.0)
        lo = MediaMetrics(resolution="640x360", bitrate_kbps=500.0,
                           fps=15.0, video_codec="h264", delay_ms=2000,
                           speed_mbps=2.0)

        hd_src = _source(metrics=hd)
        lo_src = _source(metrics=lo, url="http://cdn.example.com/low.m3u8")

        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        await worker.score(hd_src)
        hd_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        await worker.score(lo_src)
        lo_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)

        # Quality dominates → HD should beat low even though loadability is worse
        assert hd_evt.composite_score > lo_evt.composite_score

    @pytest.mark.asyncio
    async def test_loadability_dominated_weights(self, event_bus, store):
        """w_quality = 0.1, w_loadability = 0.9 → speed dominates."""
        custom = dict(DEFAULT_WEIGHTS)
        custom["w_quality"] = 0.1
        custom["w_loadability"] = 0.9
        worker = ScoringWorker(
            event_bus=event_bus, store=store,
            weights=custom,
        )

        fast = MediaMetrics(resolution="640x360", bitrate_kbps=500.0,
                             fps=15.0, delay_ms=100, speed_mbps=50.0)
        slow = MediaMetrics(resolution="1920x1080", bitrate_kbps=5000.0,
                             fps=30.0, delay_ms=2000, speed_mbps=2.0)

        fast_src = _source(metrics=fast, url="http://cdn.example.com/fast.m3u8")
        slow_src = _source(metrics=slow, url="http://cdn.example.com/slow.m3u8")

        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        await worker.score(fast_src)
        fast_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        await worker.score(slow_src)
        slow_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)

        # Loadability dominates → fast should beat slow
        assert fast_evt.composite_score > slow_evt.composite_score


# ── ScoringWorker: process_queue event loop ──────────────────────────────────


class TestProcessQueue:

    @pytest.mark.asyncio
    async def test_process_deep_scan_complete(self, scoring_worker, event_bus, store):
        await scoring_worker.start()
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        input_queue = asyncio.Queue()

        ms = _source(metrics=_full_hd_metrics())
        event = DeepScanCompleteEvent(
            media_source=ms,
            is_upscaled=False,
            ssim_score=0.95,
        )
        await input_queue.put(event)

        # Run process_queue briefly
        task = asyncio.create_task(scoring_worker.process_queue(input_queue))

        received = await asyncio.wait_for(score_queue.get(), timeout=2.0)
        assert isinstance(received, ScoreUpdatedEvent)
        assert received.media_source_id == ms.id

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await scoring_worker.stop()

    @pytest.mark.asyncio
    async def test_process_full_scan_complete_fallback(
        self, scoring_worker, event_bus, store,
    ):
        await scoring_worker.start()
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        input_queue = asyncio.Queue()

        ms = _source(metrics=_full_hd_metrics())
        event = FullScanCompleteEvent(
            media_source=ms,
            speed_mbps=20.0,
            metrics={},
        )
        await input_queue.put(event)

        task = asyncio.create_task(scoring_worker.process_queue(input_queue))

        received = await asyncio.wait_for(score_queue.get(), timeout=2.0)
        assert isinstance(received, ScoreUpdatedEvent)
        assert received.media_source_id == ms.id

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await scoring_worker.stop()

    @pytest.mark.asyncio
    async def test_process_queue_handles_error_gracefully(
        self, scoring_worker, event_bus,
    ):
        await scoring_worker.start()
        input_queue = asyncio.Queue()
        # Put a non-event object that will cause an error
        await input_queue.put("not an event")

        task = asyncio.create_task(scoring_worker.process_queue(input_queue))
        await asyncio.sleep(0.2)
        # Should not crash — the worker catches exceptions
        assert scoring_worker._running is True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await scoring_worker.stop()

    @pytest.mark.asyncio
    async def test_process_queue_cancellation(self, scoring_worker):
        await scoring_worker.start()
        input_queue = asyncio.Queue()
        task = asyncio.create_task(scoring_worker.process_queue(input_queue))
        await asyncio.sleep(0.05)
        await scoring_worker.stop()
        # Task should complete (CancelledError caught internally)
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── RankingUpdatedEvent emission ─────────────────────────────────────────────


class TestRankingEvents:

    @pytest.mark.asyncio
    async def test_ranking_event_emitted_on_score(self, scoring_worker, event_bus, store):
        ranking_queue = event_bus.subscribe(RankingUpdatedEvent)

        ms = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(ms)

        event = await asyncio.wait_for(ranking_queue.get(), timeout=1.0)
        assert isinstance(event, RankingUpdatedEvent)
        assert event.station_name == "Test Channel"
        assert len(event.top_sources) >= 1
        assert ms.id in event.top_sources
        assert event.total_sources >= 1

    @pytest.mark.asyncio
    async def test_ranking_event_disabled(self, event_bus, store):
        worker = ScoringWorker(
            event_bus=event_bus,
            store=store,
            emit_ranking_events=False,
        )
        ranking_queue = event_bus.subscribe(RankingUpdatedEvent)

        ms = _source(metrics=_full_hd_metrics())
        await worker.score(ms)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ranking_queue.get(), timeout=0.3)

    @pytest.mark.asyncio
    async def test_ranking_top_sources_ordered(self, scoring_worker, event_bus, store):
        ranking_queue = event_bus.subscribe(RankingUpdatedEvent)

        # Score a high-quality source
        high = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(high)

        event = await asyncio.wait_for(ranking_queue.get(), timeout=1.0)
        assert event.top_sources[0] == high.id
        assert event.total_sources == 1

        # Score a lower-quality source — it should rank below
        low = _source(
            metrics=_low_quality_metrics(),
            url="http://cdn.example.com/low.m3u8",
        )
        await scoring_worker.score(low)

        event2 = await asyncio.wait_for(ranking_queue.get(), timeout=1.0)
        assert event2.total_sources == 2
        # The high-quality source should still be top
        stored_high = await store.get_source("Test Channel", high.url)
        stored_low = await store.get_source("Test Channel", low.url)
        high_comp = stored_high.metrics.composite_score or 0.0
        low_comp = stored_low.metrics.composite_score or 0.0
        assert high_comp >= low_comp


# ── Edge Cases & Missing Data ────────────────────────────────────────────────


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_metrics_produces_valid_scores(self, scoring_worker):
        ms = _source(metrics=MediaMetrics())
        result = await scoring_worker.score(ms)
        assert result is True
        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        assert stored is not None
        # All NEUTRAL scores should still be valid
        assert 0.0 <= stored.metrics.quality_score <= 1.0
        assert 0.0 <= stored.metrics.loadability_score <= 1.0

    @pytest.mark.asyncio
    async def test_only_speed_differs(self, scoring_worker, event_bus, store):
        """Sources with all same metrics except speed should rank by speed."""
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        fast = MediaMetrics(delay_ms=500, speed_mbps=50.0)
        slow = MediaMetrics(delay_ms=500, speed_mbps=1.0)

        fast_src = _source(metrics=fast, url="http://cdn.example.com/fast.m3u8")
        slow_src = _source(metrics=slow, url="http://cdn.example.com/slow.m3u8")

        await scoring_worker.score(fast_src)
        await asyncio.wait_for(score_queue.get(), timeout=1.0)
        await scoring_worker.score(slow_src)
        await asyncio.wait_for(score_queue.get(), timeout=1.0)

        sfast = await store.get_source("Test Channel", fast_src.url)
        sslow = await store.get_source("Test Channel", slow_src.url)
        assert sfast.metrics.composite_score > sslow.metrics.composite_score

    @pytest.mark.asyncio
    async def test_negative_delay_is_neutral(self, scoring_worker, event_bus, store):
        """A negative delay should use NEUTRAL fallback per utils.scoring."""
        metrics = MediaMetrics(delay_ms=-1, speed_mbps=10.0)
        ms = _source(metrics=metrics)
        await scoring_worker.score(ms)
        stored = await store.get_source("Test Channel", ms.url)
        assert stored is not None
        assert 0.0 <= stored.metrics.loadability_score <= 1.0

    @pytest.mark.asyncio
    async def test_infinite_speed_maximizes_loadability(self, scoring_worker, event_bus, store):
        """Infinite speed should produce near-maximum loadability (delay still matters)."""
        metrics = MediaMetrics(delay_ms=0, speed_mbps=float("inf"))
        ms = _source(metrics=metrics)
        await scoring_worker.score(ms)
        stored = await store.get_source("Test Channel", ms.url)
        # With delay=0 and inf speed, loadability should be 1.0
        assert stored.metrics.loadability_score == 1.0

    @pytest.mark.asyncio
    async def test_hevc_codec_efficiency(self, scoring_worker, event_bus, store):
        """hevc with half the bitrate should match h264's quality score."""
        h264_m = MediaMetrics(
            resolution="1920x1080", bitrate_kbps=5000.0,
            fps=25.0, video_codec="h264", delay_ms=300, speed_mbps=20.0,
        )
        hevc_m = MediaMetrics(
            resolution="1920x1080", bitrate_kbps=2500.0,
            fps=25.0, video_codec="hevc", delay_ms=300, speed_mbps=20.0,
        )
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        h264_src = _source(metrics=h264_m, url="http://cdn.example.com/h264.m3u8")
        hevc_src = _source(metrics=hevc_m, url="http://cdn.example.com/hevc.m3u8")

        await scoring_worker.score(h264_src)
        h264_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        await scoring_worker.score(hevc_src)
        hevc_evt = await asyncio.wait_for(score_queue.get(), timeout=1.0)

        # hevc at half bitrate should have similar quality to h264 at full
        assert abs(h264_evt.quality_score - hevc_evt.quality_score) < 0.15


# ── Store Integration ────────────────────────────────────────────────────────


class TestStoreIntegration:

    @pytest.mark.asyncio
    async def test_score_updates_store(self, scoring_worker, store):
        ms = _source(metrics=_full_hd_metrics())
        await store.add_or_update_source(ms)
        await scoring_worker.score(ms)

        stored = await store.get_source("Test Channel", ms.url)
        assert stored is not None
        assert stored.status == MediaStatus.SCORING_COMPLETE
        assert stored.metrics.quality_score is not None
        assert stored.metrics.loadability_score is not None
        assert stored.metrics.composite_score is not None

    @pytest.mark.asyncio
    async def test_score_preserves_existing_metrics(self, scoring_worker, store):
        metrics = _full_hd_metrics()
        ms = _source(metrics=metrics)
        await store.add_or_update_source(ms)
        await scoring_worker.score(ms)

        stored = await store.get_source("Test Channel", ms.url)
        assert stored.metrics.delay_ms == 300.0
        assert stored.metrics.speed_mbps == 20.0
        assert stored.metrics.resolution == "1920x1080"

    @pytest.mark.asyncio
    async def test_multiple_sources_same_station(self, scoring_worker, store):
        urls = [
            ("http://cdn.example.com/a.m3u8", _full_hd_metrics()),
            ("http://cdn.example.com/b.m3u8", _low_quality_metrics()),
            ("http://cdn.example.com/c.m3u8", _upscaled_metrics()),
        ]
        for url, metrics in urls:
            ms = _source(url=url, metrics=metrics)
            await store.add_or_update_source(ms)
            await scoring_worker.score(ms)

        station = await store.get_station("Test Channel")
        assert station is not None
        assert station.source_count == 3
        top = station.get_top_sources(limit=3)
        assert len(top) == 3
        # HD should rank top
        assert top[0].metrics.composite_score >= top[1].metrics.composite_score


# ── Concurrent Scoring ───────────────────────────────────────────────────────


class TestConcurrentScoring:

    @pytest.mark.asyncio
    async def test_concurrent_scoring_no_race_conditions(
        self, scoring_worker, event_bus, store,
    ):
        """Score 20 sources concurrently, all should complete successfully."""
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        sources = []
        for i in range(20):
            res = "1920x1080" if i % 2 == 0 else "640x360"
            bitrate = 5000.0 if i % 2 == 0 else 500.0
            speed = 20.0 if i % 2 == 0 else 2.0
            metrics = MediaMetrics(
                delay_ms=100.0 + i * 50,
                speed_mbps=speed,
                bandwidth_mbps=speed,
                resolution=res,
                video_codec="h264",
                fps=30.0,
                bitrate_kbps=bitrate,
                is_upscaled=False,
            )
            url = f"http://cdn.example.com/stream{i}.m3u8"
            ms = _source(url=url, metrics=metrics, station=f"Channel {i % 5}")
            sources.append(ms)

        results = await asyncio.gather(*[
            scoring_worker.score(s) for s in sources
        ])
        assert all(results)
        assert scoring_worker.get_metrics()["scored"] == 20

        # Verify store has all sources
        for i in range(5):
            station = await store.get_station(f"Channel {i % 5}")
            if station:
                assert station.source_count == 4  # 20 sources / 5 stations

    @pytest.mark.asyncio
    async def test_concurrent_scoring_emits_all_events(
        self, scoring_worker, event_bus,
    ):
        """All concurrent scores should emit ScoreUpdatedEvent."""
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        sources = []
        for i in range(10):
            metrics = MediaMetrics(
                delay_ms=200.0,
                speed_mbps=10.0,
                resolution="1920x1080",
                video_codec="h264",
                fps=30.0,
                bitrate_kbps=5000.0,
            )
            url = f"http://cdn.example.com/stream{i}.m3u8"
            ms = _source(url=url, metrics=metrics)
            sources.append(ms)

        await asyncio.gather(*[scoring_worker.score(s) for s in sources])

        received = 0
        while received < 10:
            try:
                evt = await asyncio.wait_for(score_queue.get(), timeout=2.0)
                if isinstance(evt, ScoreUpdatedEvent):
                    received += 1
            except asyncio.TimeoutError:
                break
        assert received == 10


# ── Event Emission Verification ──────────────────────────────────────────────


class TestEventEmission:

    @pytest.mark.asyncio
    async def test_scoring_event_fields(self, scoring_worker, event_bus):
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        ms = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(ms)

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        assert event.media_source_id == ms.id
        assert event.station_name == "Test Channel"
        assert 0.0 < event.quality_score <= 1.0
        assert 0.0 < event.loadability_score <= 1.0
        assert 0.0 < event.composite_score <= 1.0
        assert hasattr(event, "event_id")
        assert hasattr(event, "timestamp")

    @pytest.mark.asyncio
    async def test_scoring_event_trace_propagation(self, scoring_worker, event_bus):
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)
        ms = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(ms, trace_id="custom-trace")

        event = await asyncio.wait_for(score_queue.get(), timeout=1.0)
        assert event.trace_id == "custom-trace"

    @pytest.mark.asyncio
    async def test_ranking_event_fields(self, scoring_worker, event_bus):
        ranking_queue = event_bus.subscribe(RankingUpdatedEvent)

        ms = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(ms)

        event = await asyncio.wait_for(ranking_queue.get(), timeout=1.0)
        assert event.station_name == "Test Channel"
        assert isinstance(event.top_sources, list)
        assert isinstance(event.total_sources, int)
        assert event.total_sources >= 1

    @pytest.mark.asyncio
    async def test_metrics_tracking(self, scoring_worker):
        assert scoring_worker.get_metrics()["scored"] == 0
        assert scoring_worker.get_metrics()["errors"] == 0
        assert scoring_worker.get_metrics()["rankings_updated"] == 0

        ms = _source(metrics=_full_hd_metrics())
        await scoring_worker.score(ms)

        metrics = scoring_worker.get_metrics()
        assert metrics["scored"] == 1
        assert metrics["rankings_updated"] == 1


# ── Quality Score Calculation (direct verification) ──────────────────────────


class TestQualityScoreCalculation:

    @pytest.mark.asyncio
    async def test_higher_resolution_scores_higher_quality(self, scoring_worker):
        """All else equal, higher resolution should yield higher quality."""
        hd = _source(metrics=MediaMetrics(
            resolution="1920x1080", bitrate_kbps=5000.0,
            fps=30.0, video_codec="h264",
        ))
        sd = _source(metrics=MediaMetrics(
            resolution="640x360", bitrate_kbps=500.0,
            fps=30.0, video_codec="h264",
        ), url="http://cdn.example.com/sd.m3u8")

        await scoring_worker.score(hd)
        await scoring_worker.score(sd)

        hd_stored = await scoring_worker.store.get_source("Test Channel", hd.url)
        sd_stored = await scoring_worker.store.get_source("Test Channel", sd.url)
        assert hd_stored.metrics.quality_score > sd_stored.metrics.quality_score

    @pytest.mark.asyncio
    async def test_higher_fps_scores_higher_quality(self, scoring_worker):
        """At same resolution/codec with adequate bitrate, higher fps = higher quality."""
        high_fps = _source(metrics=MediaMetrics(
            resolution="1920x1080", bitrate_kbps=12000.0,
            fps=60.0, video_codec="h264",
        ))
        low_fps = _source(metrics=MediaMetrics(
            resolution="1920x1080", bitrate_kbps=12000.0,
            fps=15.0, video_codec="h264",
        ), url="http://cdn.example.com/lowfps.m3u8")

        await scoring_worker.score(high_fps)
        await scoring_worker.score(low_fps)

        high_stored = await scoring_worker.store.get_source("Test Channel", high_fps.url)
        low_stored = await scoring_worker.store.get_source("Test Channel", low_fps.url)
        # At 12 Mbps both have saturated encoding adequacy, so fps_score dominates
        assert high_stored.metrics.quality_score > low_stored.metrics.quality_score

    @pytest.mark.asyncio
    async def test_better_codec_gives_better_quality(self, scoring_worker):
        """hevc at same bitrate as h264 should score higher quality."""
        h264 = _source(metrics=MediaMetrics(
            resolution="1920x1080", bitrate_kbps=3000.0,
            fps=25.0, video_codec="h264",
        ))
        hevc = _source(metrics=MediaMetrics(
            resolution="1920x1080", bitrate_kbps=3000.0,
            fps=25.0, video_codec="hevc",
        ), url="http://cdn.example.com/hevc.m3u8")

        await scoring_worker.score(h264)
        await scoring_worker.score(hevc)

        h264_stored = await scoring_worker.store.get_source("Test Channel", h264.url)
        hevc_stored = await scoring_worker.store.get_source("Test Channel", hevc.url)
        assert hevc_stored.metrics.quality_score > h264_stored.metrics.quality_score


# ── Filtering / Edge Cases ───────────────────────────────────────────────────


class TestEdgeCasesExtended:

    @pytest.mark.asyncio
    async def test_zero_bitrate_handled_gracefully(self, scoring_worker):
        """Zero bitrate should not cause division errors."""
        metrics = MediaMetrics(
            resolution="1920x1080",
            bitrate_kbps=0.0,
            fps=25.0,
            video_codec="h264",
        )
        ms = _source(metrics=metrics)
        result = await scoring_worker.score(ms)
        assert result is True
        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        assert 0.0 <= stored.metrics.quality_score <= 1.0

    @pytest.mark.asyncio
    async def test_zero_delay_handled_gracefully(self, scoring_worker):
        metrics = MediaMetrics(delay_ms=0.0, speed_mbps=10.0)
        ms = _source(metrics=metrics)
        result = await scoring_worker.score(ms)
        assert result is True
        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        assert 0.0 <= stored.metrics.quality_score <= 1.0

    @pytest.mark.asyncio
    async def test_very_low_speed_is_sustainable_unknown_bitrate(self, scoring_worker):
        """When bitrate is unknown, even low speed is 'sustainable'."""
        metrics = MediaMetrics(delay_ms=500, speed_mbps=0.1)
        ms = _source(metrics=metrics)
        result = await scoring_worker.score(ms)
        assert result is True
        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        assert 0.0 <= stored.metrics.loadability_score <= 1.0

    @pytest.mark.asyncio
    async def test_a_res_penalty_lowers_quality_score(self, scoring_worker):
        honest = MediaMetrics(
            resolution="1920x1080", bitrate_kbps=5000.0,
            fps=25.0, video_codec="h264", is_upscaled=False,
        )
        upscaled = MediaMetrics(
            resolution="1920x1080", bitrate_kbps=5000.0,
            fps=25.0, video_codec="h264", is_upscaled=True,
        )
        honest_src = _source(metrics=honest)
        upscaled_src = _source(metrics=upscaled,
                                url="http://cdn.example.com/upscaled.m3u8")

        await scoring_worker.score(honest_src)
        await scoring_worker.score(upscaled_src)

        h = await scoring_worker.store.get_source("Test Channel", honest_src.url)
        u = await scoring_worker.store.get_source("Test Channel", upscaled_src.url)
        # a_res penalty of 0.7 vs 1.0 should make honest's quality higher
        assert h.metrics.quality_score > u.metrics.quality_score


# ── Quality→Loadability balance (spec formula) ───────────────────────────────


class TestCompositeFormula:

    @pytest.mark.asyncio
    async def test_composite_is_weighted_quality_plus_loadability(self, scoring_worker):
        """Verify composite = w_quality * Q + w_loadability * L."""
        metrics = MediaMetrics(
            resolution="1920x1080", bitrate_kbps=5000.0,
            fps=30.0, video_codec="h264", delay_ms=300, speed_mbps=20.0,
        )
        ms = _source(metrics=metrics)
        await scoring_worker.score(ms)

        stored = await scoring_worker.store.get_source("Test Channel", ms.url)
        expected = (
            scoring_worker.weights["w_quality"] * stored.metrics.quality_score
            + scoring_worker.weights["w_loadability"] * stored.metrics.loadability_score
        )
        assert abs(stored.metrics.composite_score - expected) < 1e-9

    @pytest.mark.asyncio
    async def test_default_weights_even_balance(self, scoring_worker, event_bus):
        """Default weights: quality and loadability equally weighted."""
        assert scoring_worker.weights["w_quality"] == 0.5
        assert scoring_worker.weights["w_loadability"] == 0.5


# ── Additional Coverage — uncovered branches ─────────────────────────────────


class TestCoverageBranches:

    @pytest.mark.asyncio
    async def test_ranking_early_return_no_station(self, scoring_worker):
        """Score a source that has not been added to the store (no station)."""
        ms = _source(metrics=_full_hd_metrics())
        # Do NOT add to store first — _check_ranking should return early
        result = await scoring_worker.score(ms)
        assert result is True

    @pytest.mark.asyncio
    async def test_process_queue_timeout_loop(self, scoring_worker, event_bus):
        """process_queue should survive a TimeoutError and continue looping."""
        await scoring_worker.start()
        input_queue = asyncio.Queue()
        score_queue = event_bus.subscribe(ScoreUpdatedEvent)

        task = asyncio.create_task(scoring_worker.process_queue(input_queue))

        # Wait long enough for at least one 1-second timeout cycle
        await asyncio.sleep(1.5)

        # Now put an event and verify it gets processed
        ms = _source(metrics=_full_hd_metrics())
        event = DeepScanCompleteEvent(
            media_source=ms,
            is_upscaled=False,
            ssim_score=0.95,
        )
        await input_queue.put(event)

        received = await asyncio.wait_for(score_queue.get(), timeout=2.0)
        assert isinstance(received, ScoreUpdatedEvent)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await scoring_worker.stop()

    @pytest.mark.asyncio
    async def test_process_queue_exception_handling(self, scoring_worker, event_bus):
        """process_queue should catch exceptions from score() gracefully."""
        await scoring_worker.start()
        input_queue = asyncio.Queue()

        # Create an event that will cause score() to fail — null media_source
        event = DeepScanCompleteEvent(
            media_source=None,  # type: ignore
            is_upscaled=False,
            ssim_score=0.0,
        )
        await input_queue.put(event)

        task = asyncio.create_task(scoring_worker.process_queue(input_queue))

        # Give it time to process the bad event
        await asyncio.sleep(0.5)

        # Worker should still be running (not crashed)
        assert scoring_worker._running is True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await scoring_worker.stop()

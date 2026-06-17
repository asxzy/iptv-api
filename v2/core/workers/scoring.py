"""
v2/core/workers/scoring.py

Scoring worker that computes quality, loadability, and composite scores
for media sources after scanning. Reuses the ranking algorithms from
utils/scoring.py, adapted to the v2 event-driven architecture.

Listens for DeepScanCompleteEvent (or FullScanCompleteEvent as fallback)
and emits ScoreUpdatedEvent with computed scores.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# Ensure the project root is on sys.path so we can import utils.*
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
sys.path.insert(0, _PROJECT_ROOT)

from ..bus import EventBus
from ..events import (
    DeepScanCompleteEvent,
    FullScanCompleteEvent,
    RankingUpdatedEvent,
    ScoreUpdatedEvent,
)
from ..store import GlobalDataStore
from ..types import MediaMetrics, MediaSource, MediaStatus

logger = logging.getLogger(__name__)

# Re-export the reference weights so callers can inspect/tune them
from utils.scoring import DEFAULT_WEIGHTS, compute_score, loadability_score, quality_score


class ScoringWorker:
    """
    Worker that computes quality, loadability, and composite scores
    for media sources after they have been scanned.

    Consumption modes:
        1. process_queue() — continuous event loop (preferred)
        2. score() — one-shot scoring for a single source

    Uses the existing scoring algorithms from utils/scoring.py, which
    handle missing data gracefully (NEUTRAL fallback).
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        weights: Optional[Dict[str, float]] = None,
        emit_ranking_events: bool = True,
    ):
        self.event_bus = event_bus
        self.store = store
        self.weights = dict(weights) if weights else DEFAULT_WEIGHTS.copy()
        self.emit_ranking_events = emit_ranking_events

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._metrics = {
            "scored": 0,
            "errors": 0,
            "rankings_updated": 0,
        }

    async def start(self):
        self._running = True
        logger.info("Scoring worker started")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scoring worker stopped")

    async def process_queue(self, input_queue: asyncio.Queue):
        """
        Main loop: consume DeepScanCompleteEvent (preferred) or
        FullScanCompleteEvent (fallback) and score each source.
        """
        self._task = asyncio.current_task()
        while self._running:
            try:
                event = await asyncio.wait_for(input_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if isinstance(event, DeepScanCompleteEvent):
                    await self.score(event.media_source, event.trace_id)
                elif isinstance(event, FullScanCompleteEvent):
                    await self.score(event.media_source, event.trace_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._metrics["errors"] += 1
                logger.error("Scoring worker error: %s", e)

    async def score(
        self,
        media_source: MediaSource,
        trace_id: Optional[str] = None,
    ) -> bool:
        """
        Compute quality, loadability, and composite scores for a media source.

        1. Convert MediaMetrics -> scoring dict (bridge)
        2. Compute scores via utils.scoring functions
        3. Update store with scored MediaSource + SCORING_COMPLETE status
        4. Emit ScoreUpdatedEvent
        5. Optionally emit RankingUpdatedEvent

        Returns True on success, False on error.
        """
        trace = trace_id or "scoring"

        try:
            result = self._metrics_to_scoring_dict(media_source.metrics)
            q = quality_score(result, self.weights)
            l = loadability_score(result, self.weights)
            c = compute_score(result, self.weights)

            new_metrics = MediaMetrics(
                **{
                    **media_source.metrics.to_dict(),
                    "quality_score": q,
                    "loadability_score": l,
                    "composite_score": c,
                }
            )

            updated = (
                media_source.with_metrics(new_metrics)
                .with_status(MediaStatus.SCORING_COMPLETE)
            )
            await self.store.add_or_update_source(updated)

            station_name = media_source.station_name
            await self.event_bus.publish(
                ScoreUpdatedEvent(
                    media_source_id=media_source.id,
                    quality_score=q,
                    loadability_score=l,
                    composite_score=c,
                    station_name=station_name,
                ).with_trace(trace),
            )

            self._metrics["scored"] += 1

            if self.emit_ranking_events:
                await self._check_ranking(station_name, media_source.id, trace)

            logger.debug(
                "Scored %s → q=%.3f l=%.3f c=%.3f",
                media_source.url, q, l, c,
            )
            return True

        except Exception as e:
            self._metrics["errors"] += 1
            logger.error("Scoring error for %s: %s", media_source.url, e)
            return False

    async def _check_ranking(
        self,
        station_name: str,
        source_id: str,
        trace: str,
    ):
        """Emit RankingUpdatedEvent when the station's top sources change."""
        station = await self.store.get_station(station_name)
        if not station:
            return

        top = station.get_top_sources(limit=5)
        top_ids = [s.id for s in top]

        await self.event_bus.publish(
            RankingUpdatedEvent(
                station_name=station_name,
                top_sources=top_ids,
                total_sources=station.source_count,
            ).with_trace(trace),
        )
        self._metrics["rankings_updated"] += 1

    @staticmethod
    def _metrics_to_scoring_dict(metrics: MediaMetrics) -> Dict[str, Any]:
        """
        Bridge between MediaMetrics and the flat-dict format expected by
        utils.scoring functions.

        Field mapping:
            metrics.resolution  → "resolution"  (e.g. "1920x1080")
            metrics.bitrate_kbps → "bitrate"    (converted to bps)
            metrics.fps         → "fps"
            metrics.video_codec → "video_codec"
            metrics.speed_mbps  → "speed"       (converted Mbps→MB/s)
            metrics.delay_ms    → "delay"
            metrics.is_upscaled → "a_res"       (derived authenticity factor)
        """
        d: Dict[str, Any] = {}

        if metrics.resolution:
            d["resolution"] = metrics.resolution

        if metrics.bitrate_kbps is not None:
            d["bitrate"] = metrics.bitrate_kbps * 1000.0

        if metrics.fps is not None:
            d["fps"] = metrics.fps

        if metrics.video_codec:
            d["video_codec"] = metrics.video_codec

        if metrics.speed_mbps is not None:
            d["speed"] = metrics.speed_mbps / 8.0

        if metrics.delay_ms is not None:
            d["delay"] = metrics.delay_ms

        if metrics.is_upscaled is True:
            d["a_res"] = 0.7
        elif metrics.is_upscaled is False:
            d["a_res"] = 1.0

        return d

    def get_metrics(self) -> Dict[str, int]:
        return dict(self._metrics)

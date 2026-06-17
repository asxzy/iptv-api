"""
v2/core/workers/scoring.py

Scoring worker that computes quality and loadability scores for media sources.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from ..bus import EventBus
from ..events import (
    ScoreUpdatedEvent,
    RankingUpdatedEvent,
    FastScanCompleteEvent,
    FullScanCompleteEvent,
    DeepScanCompleteEvent,
)
from ..types import MediaSource, MediaStatus, MediaMetrics, ScanMode
from ..store import GlobalDataStore

logger = logging.getLogger(__name__)


class ScoringWorker:
    """
    Worker that scores media sources based on scan results.
    Listens to scan complete events and updates source scores.
    """
    
    def __init__(self, event_bus: EventBus, data_store: GlobalDataStore, config: Optional[Dict] = None):
        self.event_bus = event_bus
        self.data_store = data_store
        # Weights for quality and loadability (can be made configurable via config dict)
        self.weight_quality = 0.7
        self.weight_loadability = 0.3
        
        # Resolution scores (map resolution string to a score 0-1)
        self.resolution_scores = {
            "7680x4320": 1.0,   # 8K
            "3840x2160": 0.9,   # 4K
            "2560x1440": 0.8,   # 1440p
            "1920x1080": 0.7,   # 1080p
            "1280x720": 0.6,    # 720p
            "854x480": 0.5,     # 480p
            "640x360": 0.4,     # 360p
            "426x240": 0.3,     # 240p
        }
        
        # Codec efficiency scores (higher is better)
        # Matches ffprobe "codec_name" output
        self.codec_scores = {
            "hevc": 1.0,     # HEVC (h265)
            "av01": 0.95,    # AV1
            "vp9": 0.9,      # VP9
            "h264": 0.8,     # AVC
            "mpeg4": 0.7,    # MPEG-4 Part 2
            "theora": 0.6,   # Theora
            "vp8": 0.6,      # VP8
            "mjpeg": 0.5,    # Motion JPEG
        }
        
        if config:
            self.weight_quality = config.get('weight_quality', self.weight_quality)
            self.weight_loadability = config.get('weight_loadability', self.weight_loadability)
    
    async def on_fast_scan_complete(self, event: FastScanCompleteEvent, trace_id: str = None):
        """Handle fast scan completion by updating score based on available metrics."""
        await self._update_score(event.media_source, trace_id)
    
    async def on_full_scan_complete(self, event: FullScanCompleteEvent, trace_id: str = None):
        """Handle full scan completion by updating score based on available metrics."""
        await self._update_score(event.media_source, trace_id)
    
    async def on_deep_scan_complete(self, event: DeepScanCompleteEvent, trace_id: str = None):
        """Handle deep scan completion by updating score based on available metrics."""
        await self._update_score(event.media_source, trace_id)
    
    async def _update_score(self, media_source: MediaSource, trace_id: str = None):
        """Compute and update the score for a media source."""
        # Get the latest source from the store to ensure we have the latest metrics
        station = await self.data_store.get_station(media_source.station_name)
        if station is None:
            logger.warning(f"Station not found: {media_source.station_name}")
            # We'll still score the source even if it's not in the store yet
            latest_source = media_source
        else:
            latest_source = station.sources.get(media_source.url)
            if latest_source is None:
                latest_source = media_source
        
        # Compute score based on the latest source's metrics
        quality, loadability, composite = self._compute_scores(latest_source.metrics)
        
        # If the score hasn't changed significantly, skip update
        if abs(latest_source.score - composite) < 0.001:
            return
        
        # Update the source with the new score
        updated_source = latest_source.with_score(composite)
        await self.data_store.add_or_update_source(updated_source)
        
        # Emit score updated event
        score_event = ScoreUpdatedEvent(
            media_source_id=media_source.id,
            quality_score=quality,
            loadability_score=loadability,
            composite_score=composite,
        )
        if trace_id:
            score_event = score_event.with_trace(trace_id)
        await self.event_bus.publish(score_event)
        
        # Update ranking for the station and emit ranking updated event if changed
        await self._update_and_emit_ranking(media_source.station_name, trace_id)
    
    def _compute_scores(self, metrics: MediaMetrics) -> Tuple[float, float, float]:
        """Compute quality, loadability, and composite scores."""
        quality = self._compute_quality(metrics)
        loadability = self._compute_loadability(metrics)
        composite = self.weight_quality * quality + self.weight_loadability * loadability
        return (max(0.0, min(1.0, quality)),
                max(0.0, min(1.0, loadability)),
                max(0.0, min(1.0, composite)))
    
    def _compute_quality(self, metrics: MediaMetrics) -> float:
        """Compute quality score from resolution, fps, codec, and bitrate."""
        # Resolution score
        resolution_score = self._get_resolution_score(metrics.resolution)
        
        # FPS score: normalize to 0-1, assuming 60fps is max useful
        fps_score = min(metrics.fps or 0, 60.0) / 60.0 if metrics.fps is not None else 0.5
        
        # Codec score
        codec_score = self.codec_scores.get(metrics.video_codec or "", 0.5)
        
        # Combine resolution, fps, and codec with weights
        quality = (
            0.5 * resolution_score +
            0.3 * fps_score +
            0.2 * codec_score
        )
        
        # Authenticity penalty: if upscaled, reduce quality
        if metrics.is_upscaled:
            quality *= 0.5  # 50% penalty for upscaled content
        
        return quality
    
    def _get_resolution_score(self, resolution: Optional[str]) -> float:
        """Map resolution string to a score 0-1."""
        if resolution is None:
            return 0.0
        norm_res = resolution.replace(" ", "").lower()
        if norm_res in self.resolution_scores:
            return self.resolution_scores[norm_res]
        if "x" in norm_res:
            try:
                _, height_str = norm_res.split("x")
                height = int(height_str)
                known_heights = [4320, 2160, 1440, 1080, 720, 480, 360, 240]
                closest_height = min(known_heights, key=lambda h: abs(h - height))
                height_to_score = {
                    4320: 1.0, 2160: 0.9, 1440: 0.8, 1080: 0.7,
                    720: 0.6, 480: 0.5, 360: 0.4, 240: 0.3,
                }
                return height_to_score.get(closest_height, 0.0)
            except ValueError:
                pass
        return 0.0
    
    def _compute_loadability(self, metrics: MediaMetrics) -> float:
        """Compute loadability score from speed and delay."""
        speed = metrics.speed_mbps or 0.0
        if speed >= 20.0:
            speed_score = 1.0
        elif speed >= 5.0:
            speed_score = 0.5 + 0.5 * ((speed - 5.0) / 15.0)
        else:
            speed_score = speed / 5.0 * 0.5

        delay = metrics.delay_ms
        if delay is None:
            delay_score = 0.5  # Neutral score when unmeasured
        elif delay <= 20.0:
            delay_score = 1.0
        elif delay <= 100.0:
            delay_score = 0.5 + 0.5 * ((100.0 - delay) / 80.0)
        else:
            delay_score = max(0.0, 1.0 - (delay - 100.0) / 100.0)
        
        return 0.7 * speed_score + 0.3 * delay_score
    
    async def _update_and_emit_ranking(self, station_name: str, trace_id: str = None):
        """Compute ranking for a station and emit RankingUpdatedEvent if changed."""
        station = await self.data_store.get_station(station_name)
        if station is None:
            return
        
        # Create sorted list of (url, score) for ranking
        scored_sources = sorted(
            [(url, source.score) for url, source in station.sources.items()],
            key=lambda x: x[1],
            reverse=True
        )
        top_sources = [url for url, _ in scored_sources[:10]]
        
        ranking_event = RankingUpdatedEvent(
            station_name=station_name,
            top_sources=top_sources,
            total_sources=len(scored_sources),
        )
        if trace_id:
            ranking_event = ranking_event.with_trace(trace_id)
        await self.event_bus.publish(ranking_event)

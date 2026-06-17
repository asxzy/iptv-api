"""
v2/core/types.py

Core data types for the v2 atomic streaming architecture.
All data structures are immutable and designed for concurrent access.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
import uuid
import hashlib


class ScanMode(Enum):
    """Scan depth modes for media sources."""
    FAST = auto()
    FULL = auto()
    DEEP = auto()


class MediaStatus(Enum):
    """Lifecycle status of a media source."""
    DISCOVERED = auto()
    VALIDATED = auto()
    FAST_SCANNED = auto()
    FULL_SCANNED = auto()
    DEEP_SCANNED = auto()
    FAILED = auto()
    BLACKLISTED = auto()


@dataclass(frozen=True)
class MediaMetrics:
    """Immutable container for all scan metrics."""
    delay_ms: Optional[float] = None
    content_type: Optional[str] = None
    status_code: Optional[int] = None
    speed_mbps: Optional[float] = None
    bandwidth_mbps: Optional[float] = None
    download_size_bytes: Optional[int] = None
    download_time_ms: Optional[float] = None
    resolution: Optional[str] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    fps: Optional[float] = None
    bitrate_kbps: Optional[float] = None
    duration_seconds: Optional[float] = None
    is_upscaled: Optional[bool] = None
    ssim_score: Optional[float] = None
    actual_resolution: Optional[str] = None
    quality_score: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class MediaSource:
    """Immutable representation of a single media source."""
    id: str
    url: str
    station_name: str
    source_file: str
    headers: Dict[str, str] = field(default_factory=dict, repr=False)
    metrics: MediaMetrics = field(default_factory=MediaMetrics)
    status: MediaStatus = MediaStatus.DISCOVERED
    score: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), repr=False)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), repr=False)

    def with_status(self, status: MediaStatus) -> 'MediaSource':
        return MediaSource(
            id=self.id, url=self.url, station_name=self.station_name,
            source_file=self.source_file, headers=self.headers,
            metrics=self.metrics, status=status, score=self.score,
            created_at=self.created_at, updated_at=datetime.now(timezone.utc)
        )
    
    def with_metrics(self, metrics: MediaMetrics) -> 'MediaSource':
        return MediaSource(
            id=self.id, url=self.url, station_name=self.station_name,
            source_file=self.source_file, headers=self.headers,
            metrics=metrics, status=self.status, score=self.score,
        created_at=self.created_at, updated_at=datetime.now(timezone.utc)
    )

    def with_score(self, score: float) -> 'MediaSource':
        return MediaSource(
            id=self.id, url=self.url, station_name=self.station_name,
            source_file=self.source_file, headers=self.headers,
            metrics=self.metrics, status=self.status, score=score,
            created_at=self.created_at, updated_at=datetime.now(timezone.utc)
        )


@dataclass
class Station:
    """Mutable container for a station and its media sources."""
    name: str
    sources: Dict[str, MediaSource] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow, repr=False)
    
    @property
    def source_count(self) -> int:
        return len(self.sources)
    
    def get_sources_by_status(self, status: MediaStatus) -> List[MediaSource]:
        return [s for s in self.sources.values() if s.status == status]
    
    def get_top_sources(self, limit: int = 5) -> List[MediaSource]:
        """Return top sources sorted by quality_score descending."""
        scored = [(s, s.metrics.quality_score or 0.0) for s in self.sources.values()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:limit]]


def generate_media_id(url: str, station_name: str) -> str:
    """Generate unique ID for a media source."""
    data = f"{station_name}:{url}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]

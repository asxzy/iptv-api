"""
v2/core/events.py

Event definitions for the v2 streaming architecture.
All events are immutable dataclasses with metadata.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, List
import uuid

from .types import MediaSource, Station, ScanMode, MediaStatus


@dataclass(frozen=True)
class Event:
    """Base class for all events."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    trace_id: Optional[str] = None
    
    def with_trace(self, trace_id: str) -> 'Event':
        """Create a copy with trace ID set."""
        kwargs = {
            f.name: getattr(self, f.name)
            for f in self.__dataclass_fields__.values()
            if f.name not in ('event_id', 'timestamp', 'trace_id')
        }
        return self.__class__(trace_id=trace_id, **kwargs)


# Discovery Events
@dataclass(frozen=True)
class StationDiscoveredEvent(Event):
    """Emitted when a new station is found."""
    station_name: str = ""
    source_file: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaSourceDiscoveredEvent(Event):
    """Emitted when a media URL is resolved."""
    media_source: MediaSource = field(default_factory=lambda: MediaSource(
        id="", url="", station_name="", source_file=""
    ))
    resolved_url: str = ""
    redirect_chain: List[str] = field(default_factory=list)
    nesting_depth: int = 0


@dataclass(frozen=True)
class DiscoveryErrorEvent(Event):
    """Emitted when discovery fails."""
    source_file: str = ""
    error_message: str = ""
    url: str = ""
    is_fatal: bool = False


# Validation Events
@dataclass(frozen=True)
class URLValidatedEvent(Event):
    """Emitted when a URL passes validation."""
    media_source: MediaSource = field(default_factory=lambda: MediaSource(
        id="", url="", station_name="", source_file=""
    ))
    validation_time_ms: float = 0.0


@dataclass(frozen=True)
class URLRejectedEvent(Event):
    """Emitted when a URL fails validation."""
    url: str = ""
    reason: str = ""
    is_blacklist: bool = False


@dataclass(frozen=True)
class ValidationErrorEvent(Event):
    """Emitted when validation encounters an error."""
    url: str = ""
    error_message: str = ""


# Scan Events
@dataclass(frozen=True)
class ScanStartedEvent(Event):
    """Emitted when scanning begins."""
    media_source_id: str = ""
    mode: ScanMode = ScanMode.FAST


@dataclass(frozen=True)
class FastScanCompleteEvent(Event):
    """Emitted when fast scan finishes."""
    media_source: MediaSource = field(default_factory=lambda: MediaSource(
        id="", url="", station_name="", source_file=""
    ))
    is_available: bool = False
    latency_ms: float = 0.0


@dataclass(frozen=True)
class FullScanCompleteEvent(Event):
    """Emitted when full scan finishes."""
    media_source: MediaSource = field(default_factory=lambda: MediaSource(
        id="", url="", station_name="", source_file=""
    ))
    speed_mbps: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeepScanCompleteEvent(Event):
    """Emitted when deep scan finishes."""
    media_source: MediaSource = field(default_factory=lambda: MediaSource(
        id="", url="", station_name="", source_file=""
    ))
    is_upscaled: bool = False
    ssim_score: float = 0.0


@dataclass(frozen=True)
class ScanErrorEvent(Event):
    """Emitted when scan fails."""
    media_source_id: str = ""
    mode: ScanMode = ScanMode.FAST
    error_message: str = ""


# Scoring Events
@dataclass(frozen=True)
class ScoreUpdatedEvent(Event):
    """Emitted when a source's score is updated."""
    media_source_id: str = ""
    quality_score: float = 0.0
    loadability_score: float = 0.0
    composite_score: float = 0.0
    station_name: str = ""


@dataclass(frozen=True)
class RankingUpdatedEvent(Event):
    """Emitted when a station's ranking changes."""
    station_name: str = ""
    top_sources: List[str] = field(default_factory=list)
    total_sources: int = 0


# Orchestrator Events
@dataclass(frozen=True)
class ScanJobStartedEvent(Event):
    """Emitted when a scan job begins."""
    job_id: str = ""
    mode: ScanMode = ScanMode.FULL
    total_sources: int = 0


@dataclass(frozen=True)
class ScanJobProgressEvent(Event):
    """Periodic progress update."""
    job_id: str = ""
    phase: str = ""
    completed: int = 0
    total: int = 0
    current_station: str = ""
    current_url: str = ""


@dataclass(frozen=True)
class ScanJobCompletedEvent(Event):
    """Emitted when scan job finishes."""
    job_id: str = ""
    total_sources: int = 0
    succeeded: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class ScanJobFailedEvent(Event):
    """Emitted when scan job fails."""
    job_id: str = ""
    error_message: str = ""

"""
v2/core/workers/scan.py

Scan workers for measuring media source quality at progressive depths:
- FastScanWorker: Basic connectivity check (<5s per source)
- FullScanWorker: Speed test + basic media properties (ffprobe, 10-15s)
- DeepScanWorker: Quality analysis + upscale detection (30-60s)

Each worker emits typed events as it completes per-source, enabling
progressive result availability. FFmpeg processes are globally limited
via a shared semaphore to respect system resources.
"""

import asyncio
import json
import logging
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import aiohttp

from ..bus import EventBus
from ..events import (
    DeepScanCompleteEvent,
    FastScanCompleteEvent,
    FullScanCompleteEvent,
    ScanErrorEvent,
    ScanStartedEvent,
)
from ..store import GlobalDataStore
from ..types import MediaMetrics, MediaSource, MediaStatus, ScanMode

logger = logging.getLogger(__name__)

# Module-level semaphore for global FFmpeg/ffprobe process limiting
_ffmpeg_semaphore: Optional[asyncio.Semaphore] = None


def get_ffmpeg_semaphore(max_processes: int = 3) -> asyncio.Semaphore:
    """Get or create a global semaphore for FFmpeg process limiting."""
    global _ffmpeg_semaphore
    if _ffmpeg_semaphore is None:
        _ffmpeg_semaphore = asyncio.Semaphore(max_processes)
    return _ffmpeg_semaphore


def reset_ffmpeg_semaphore():
    """Reset the global FFmpeg semaphore (useful for testing)."""
    global _ffmpeg_semaphore
    _ffmpeg_semaphore = None


class BaseScanWorker(ABC):
    """
    Abstract base class for all scan workers.

    Provides shared infrastructure:
    - HTTP session management (start/stop)
    - Semaphore-gated concurrency
    - Connectivity check (HEAD request with retries)
    - ffprobe media probing
    - Common event publishing (ScanStartedEvent, ScanErrorEvent)
    - Store integration
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        max_concurrent: int = 5,
        user_agent: Optional[str] = None,
    ):
        self.event_bus = event_bus
        self.store = store
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_concurrent = max_concurrent
        self.user_agent = user_agent or (
            "Mozilla/5.0 (compatible; IPTV-API/2.0; "
            "+https://github.com/Guovin/iptv-api)"
        )
        self.session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False

    @property
    @abstractmethod
    def scan_mode(self) -> ScanMode:
        """The ScanMode enum value for this worker."""

    @property
    @abstractmethod
    def target_status(self) -> MediaStatus:
        """The status to assign on successful scan completion."""

    async def start(self):
        """Create the aiohttp session."""
        connector = aiohttp.TCPConnector(limit=0)
        timeout_obj = aiohttp.ClientTimeout(total=self.timeout, connect=self.timeout)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout_obj,
        )
        self._running = True
        logger.info("%s started", self.__class__.__name__)

    async def stop(self):
        """Close the aiohttp session."""
        self._running = False
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("%s stopped", self.__class__.__name__)

    async def scan(
        self,
        media_source: MediaSource,
        trace_id: Optional[str] = None,
    ) -> bool:
        """
        Run the full scan pipeline on a single media source.

        Emits events at each stage. Returns True if the scan
        completed successfully (metrics collected), False on failure.
        """
        trace = trace_id or self.__class__.__name__.lower()

        try:
            await self.event_bus.publish(
                ScanStartedEvent(
                    media_source_id=media_source.id,
                    mode=self.scan_mode,
                ).with_trace(trace),
            )

            metrics = await self._run_scan(media_source, trace)

            if metrics is not None:
                updated = (
                    media_source
                    .with_status(self.target_status)
                    .with_metrics(metrics)
                )
                await self.store.add_or_update_source(updated)
                await self._emit_complete(updated, metrics, trace)
                return True

            failed = media_source.with_status(MediaStatus.FAILED)
            await self.store.add_or_update_source(failed)
            return False

        except Exception as e:
            logger.error("Scan error for %s: %s", media_source.url, e)
            await self.event_bus.publish(
                ScanErrorEvent(
                    media_source_id=media_source.id,
                    mode=self.scan_mode,
                    error_message=str(e),
                ).with_trace(trace),
            )
            failed = media_source.with_status(MediaStatus.FAILED)
            await self.store.add_or_update_source(failed)
            return False

    @abstractmethod
    async def _run_scan(
        self,
        media_source: MediaSource,
        trace: str,
    ) -> Optional[MediaMetrics]:
        """Execute the mode-specific scan. Return MediaMetrics on success, None on failure."""

    @abstractmethod
    async def _emit_complete(
        self,
        media_source: MediaSource,
        metrics: MediaMetrics,
        trace: str,
    ):
        """Emit the mode-specific completion event."""

    async def _check_connectivity(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple[bool, int, Dict[str, str], float]:
        """
        Perform a HEAD request with retries.

        Returns (is_available, status_code, response_headers, latency_ms).
        """
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)

        for attempt in range(self.max_retries + 1):
            try:
                start = time.monotonic()
                async with self.session.head(
                    url,
                    headers=request_headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(
                        total=self.timeout,
                        connect=self.timeout,
                    ),
                ) as response:
                    elapsed = (time.monotonic() - start) * 1000
                    if response.status >= 500 and attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay)
                        continue
                    is_ok = 200 <= response.status < 300
                    return is_ok, response.status, dict(response.headers), elapsed

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                    continue
                return False, 0, {"_error": str(e)}, 0.0

        return False, 0, {"_error": "Max retries exceeded"}, 0.0

    async def _probe_media(
        self,
        url: str,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Probe a media URL with ffprobe to extract stream properties.

        Uses the global FFmpeg semaphore to limit concurrent processes.
        Returns a dict with resolution, codec, fps, bitrate, etc.,
        or None on failure.
        """
        ffmpeg_sem = get_ffmpeg_semaphore()
        async with ffmpeg_sem:
            return await self._run_ffprobe(url, timeout)

    async def _run_ffprobe(
        self,
        url: str,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """Low-level ffprobe execution without the semaphore."""
        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                "-i", url,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            if proc.returncode != 0:
                logger.debug(
                    "ffprobe failed for %s: %s",
                    url,
                    stderr.decode(errors="replace")[:200],
                )
                return None
            return json.loads(stdout.decode())
        except asyncio.TimeoutError:
            logger.debug("ffprobe timed out for %s", url)
            return None
        except Exception as e:
            logger.debug("ffprobe error for %s: %s", url, e)
            return None

    @staticmethod
    def _extract_media_info(
        probe_result: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Extract resolved media properties from raw ffprobe JSON."""
        if not probe_result:
            return {}

        info: Dict[str, Any] = {}
        video_stream: Optional[Dict] = None
        audio_stream: Optional[Dict] = None

        for stream in probe_result.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        if video_stream:
            info["video_codec"] = video_stream.get("codec_name")
            w = video_stream.get("width", 0) or 0
            h = video_stream.get("height", 0) or 0
            info["resolution"] = f"{w}x{h}"
            info["width"] = w
            info["height"] = h

            r_frame_rate = video_stream.get("r_frame_rate", "0/1")
            if "/" in str(r_frame_rate):
                try:
                    num, den = r_frame_rate.split("/")
                    info["fps"] = float(num) / float(den) if float(den) > 0 else 0.0
                except (ValueError, ZeroDivisionError):
                    info["fps"] = 0.0
            else:
                try:
                    info["fps"] = float(r_frame_rate)
                except (ValueError, TypeError):
                    info["fps"] = 0.0

            stream_bitrate = video_stream.get("bit_rate")
            if stream_bitrate:
                try:
                    info["bitrate_kbps"] = float(stream_bitrate) / 1000.0
                except (ValueError, TypeError):
                    pass

        if audio_stream:
            info["audio_codec"] = audio_stream.get("codec_name")

        fmt = probe_result.get("format", {})
        if not info.get("bitrate_kbps"):
            fmt_bitrate = fmt.get("bit_rate")
            if fmt_bitrate:
                try:
                    info["bitrate_kbps"] = float(fmt_bitrate) / 1000.0
                except (ValueError, TypeError):
                    pass

        duration = fmt.get("duration")
        if duration:
            try:
                info["duration_seconds"] = float(duration)
            except (ValueError, TypeError):
                pass

        return info


class FastScanWorker(BaseScanWorker):
    """
    Fast scan: Quick connectivity check + content type validation.

    Time: < 5 seconds per source.
    Use case: Quick overview of all sources.
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        timeout: float = 5.0,
        max_retries: int = 1,
        retry_delay: float = 0.5,
        max_concurrent: int = 10,
        user_agent: Optional[str] = None,
    ):
        super().__init__(
            event_bus=event_bus,
            store=store,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            max_concurrent=max_concurrent,
            user_agent=user_agent,
        )

    @property
    def scan_mode(self) -> ScanMode:
        return ScanMode.FAST

    @property
    def target_status(self) -> MediaStatus:
        return MediaStatus.FAST_SCANNED

    async def _run_scan(
        self,
        media_source: MediaSource,
        trace: str,
    ) -> Optional[MediaMetrics]:
        url = media_source.url
        is_available, status_code, headers, latency_ms = await self._check_connectivity(
            url,
            media_source.headers or None,
        )

        if not is_available:
            error_msg = headers.get("_error", f"HTTP {status_code}")
            logger.debug("Fast scan: connectivity failed for %s: %s", url, error_msg)
            return None

        content_type = (
            headers.get("Content-Type", "").lower().split(";")[0].strip() or None
        )

        return MediaMetrics(
            delay_ms=latency_ms,
            content_type=content_type,
            status_code=status_code,
        )

    async def _emit_complete(
        self,
        media_source: MediaSource,
        metrics: MediaMetrics,
        trace: str,
    ):
        await self.event_bus.publish(
            FastScanCompleteEvent(
                media_source=media_source,
                is_available=True,
                latency_ms=metrics.delay_ms or 0.0,
            ).with_trace(trace),
        )


class FullScanWorker(BaseScanWorker):
    """
    Full scan: Speed test + basic media properties via ffprobe.

    Time: 10-15 seconds per source.
    Use case: Rank sources by speed and basic quality.
    Includes all Fast mode checks.
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        timeout: float = 15.0,
        max_retries: int = 1,
        retry_delay: float = 1.0,
        max_concurrent: int = 3,
        user_agent: Optional[str] = None,
        download_size: int = 1_048_576,  # 1 MB sample
        ffprobe_timeout: float = 10.0,
    ):
        super().__init__(
            event_bus=event_bus,
            store=store,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            max_concurrent=max_concurrent,
            user_agent=user_agent,
        )
        self.download_size = download_size
        self.ffprobe_timeout = ffprobe_timeout

    @property
    def scan_mode(self) -> ScanMode:
        return ScanMode.FULL

    @property
    def target_status(self) -> MediaStatus:
        return MediaStatus.FULL_SCANNED

    async def _run_scan(
        self,
        media_source: MediaSource,
        trace: str,
    ) -> Optional[MediaMetrics]:
        url = media_source.url
        fields: Dict[str, Any] = {}

        is_available, status_code, headers, latency_ms = await self._check_connectivity(
            url,
            media_source.headers or None,
        )
        if not is_available:
            return None

        fields["delay_ms"] = latency_ms
        fields["content_type"] = (
            headers.get("Content-Type", "").lower().split(";")[0].strip() or None
        )
        fields["status_code"] = status_code

        speed_result = await self._measure_speed(url, media_source.headers)
        if speed_result:
            speed_mbps, size_bytes, time_ms = speed_result
            fields["speed_mbps"] = speed_mbps
            fields["download_size_bytes"] = size_bytes
            fields["download_time_ms"] = time_ms
            fields["bandwidth_mbps"] = speed_mbps

        probe_result = await self._probe_media(url, self.ffprobe_timeout)
        if probe_result:
            media_info = self._extract_media_info(probe_result)
            for key in ("video_codec", "resolution", "fps", "bitrate_kbps",
                         "duration_seconds", "audio_codec"):
                if key in media_info:
                    fields[key] = media_info[key]

        return MediaMetrics(**{k: v for k, v in fields.items()
                                if k in MediaMetrics.__dataclass_fields__})

    async def _measure_speed(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[tuple[float, int, float]]:
        """
        Download a partial sample to measure bandwidth.

        Returns (speed_mbps, bytes_downloaded, elapsed_ms) or None.
        """
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)

        try:
            start = time.monotonic()
            bytes_downloaded = 0
            async with self.session.get(
                url,
                headers=request_headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status != 200:
                    return None

                async for chunk in response.content.iter_chunked(65536):
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded >= self.download_size:
                        break

            elapsed_ms = (time.monotonic() - start) * 1000
            if elapsed_ms > 0 and bytes_downloaded > 0:
                speed_mbps = (bytes_downloaded * 8) / (elapsed_ms / 1000) / 1_000_000
                return speed_mbps, bytes_downloaded, elapsed_ms
            return None
        except Exception as e:
            logger.debug("Speed measurement failed for %s: %s", url, e)
            return None

    async def _emit_complete(
        self,
        media_source: MediaSource,
        metrics: MediaMetrics,
        trace: str,
    ):
        await self.event_bus.publish(
            FullScanCompleteEvent(
                media_source=media_source,
                speed_mbps=metrics.speed_mbps or 0.0,
                metrics=metrics.to_dict(),
            ).with_trace(trace),
        )


class DeepScanWorker(BaseScanWorker):
    """
    Deep scan: Quality analysis + upscale detection.

    Time: 30-60 seconds per source.
    Use case: Ensure top-ranked sources truly have the claimed quality.
    Includes all Full mode checks plus quality scoring and upscale detection.
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        timeout: float = 60.0,
        max_retries: int = 1,
        retry_delay: float = 1.0,
        max_concurrent: int = 2,
        user_agent: Optional[str] = None,
        download_size: int = 5_242_880,  # 5 MB sample
        ffprobe_timeout: float = 15.0,
    ):
        super().__init__(
            event_bus=event_bus,
            store=store,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            max_concurrent=max_concurrent,
            user_agent=user_agent,
        )
        self.download_size = download_size
        self.ffprobe_timeout = ffprobe_timeout

    @property
    def scan_mode(self) -> ScanMode:
        return ScanMode.DEEP

    @property
    def target_status(self) -> MediaStatus:
        return MediaStatus.DEEP_SCANNED

    async def _run_scan(
        self,
        media_source: MediaSource,
        trace: str,
    ) -> Optional[MediaMetrics]:
        url = media_source.url
        fields: Dict[str, Any] = {}

        is_available, status_code, headers, latency_ms = await self._check_connectivity(
            url,
            media_source.headers or None,
        )
        if not is_available:
            return None

        fields["delay_ms"] = latency_ms
        fields["content_type"] = (
            headers.get("Content-Type", "").lower().split(";")[0].strip() or None
        )
        fields["status_code"] = status_code

        speed_result = await self._measure_speed(url, media_source.headers)
        if speed_result:
            speed_mbps, size_bytes, time_ms = speed_result
            fields["speed_mbps"] = speed_mbps
            fields["download_size_bytes"] = size_bytes
            fields["download_time_ms"] = time_ms
            fields["bandwidth_mbps"] = speed_mbps

        probe_result = await self._probe_media(url, self.ffprobe_timeout)
        if probe_result:
            media_info = self._extract_media_info(probe_result)
            for key in ("video_codec", "resolution", "fps", "bitrate_kbps",
                         "duration_seconds", "audio_codec", "width", "height"):
                if key in media_info:
                    fields[key] = media_info[key]

            quality = self._analyze_quality(media_info)
            fields.update(quality)

        return MediaMetrics(**{k: v for k, v in fields.items()
                                if k in MediaMetrics.__dataclass_fields__})

    async def _measure_speed(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[tuple[float, int, float]]:
        """Download a larger sample to measure bandwidth for deep analysis."""
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)

        try:
            start = time.monotonic()
            bytes_downloaded = 0
            async with self.session.get(
                url,
                headers=request_headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status != 200:
                    return None

                async for chunk in response.content.iter_chunked(65536):
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded >= self.download_size:
                        break

            elapsed_ms = (time.monotonic() - start) * 1000
            if elapsed_ms > 0 and bytes_downloaded > 0:
                speed_mbps = (bytes_downloaded * 8) / (elapsed_ms / 1000) / 1_000_000
                return speed_mbps, bytes_downloaded, elapsed_ms
            return None
        except Exception as e:
            logger.debug("Speed measurement failed for %s: %s", url, e)
            return None

    def _analyze_quality(self, media_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run quality analysis on extracted media info.

        Returns dict with quality_score, is_upscaled, ssim_score, actual_resolution.
        """
        result: Dict[str, Any] = {}

        height = media_info.get("height", 0) or 0
        width = media_info.get("width", 0) or 0
        bitrate_kbps = media_info.get("bitrate_kbps", 0) or 0

        result["resolution"] = f"{width}x{height}" if width and height else None

        result["quality_score"] = self._compute_quality_score(media_info)
        result["is_upscaled"] = self._detect_upscale(width, height, bitrate_kbps)
        result["actual_resolution"] = result.get("resolution")
        result["ssim_score"] = self._compute_similarity_score(
            height, bitrate_kbps,
        )

        return result

    def _compute_quality_score(
        self,
        media_info: Dict[str, Any],
    ) -> float:
        """
        Compute a quality score (0-1) based on resolution, bitrate, fps, duration.

        Components:
        - Resolution tier (0-0.3)
        - Bitrate adequacy for resolution (0-0.3)
        - FPS score (0-0.2)
        - Duration score (0-0.2)
        """
        score = 0.5  # baseline

        height = media_info.get("height", 0) or 0

        # Resolution score
        if height >= 2160:
            score += 0.3
        elif height >= 1080:
            score += 0.2
        elif height >= 720:
            score += 0.1
        elif height > 0:
            score += 0.05

        # Bitrate adequacy
        bitrate = media_info.get("bitrate_kbps", 0) or 0
        if bitrate > 0 and height > 0:
            expected_bitrate = height * 5
            ratio = min(bitrate / expected_bitrate, 1.5)
            score += 0.3 * min(ratio / 1.5, 1.0)

        # FPS score
        fps = media_info.get("fps", 0) or 0
        if fps >= 50:
            score += 0.2
        elif fps >= 25:
            score += 0.15
        elif fps > 0:
            score += 0.05

        # Duration score
        duration = media_info.get("duration_seconds", 0) or 0
        if duration > 0:
            score += 0.2 * min(duration / 300.0, 1.0)

        return min(score, 1.0)

    def _detect_upscale(
        self,
        width: int,
        height: int,
        bitrate_kbps: float,
    ) -> bool:
        """
        Detect if video is likely upscaled based on bitrate-per-pixel ratio.

        Upscaled sources typically have inflated resolution but low bitrate
        relative to what the resolution demands.
        """
        if height == 0 or width == 0 or bitrate_kbps == 0:
            return False

        pixels = width * height
        bitrate_per_pixel = bitrate_kbps / pixels

        if height >= 1080 and bitrate_per_pixel < 0.001:
            return True
        if height >= 720 and height < 1080 and bitrate_per_pixel < 0.0005:
            return True

        return False

    def _compute_similarity_score(
        self,
        height: int,
        bitrate_kbps: float,
    ) -> Optional[float]:
        """
        Compute an SSIM-like quality proxy (0-1) based on bitrate adequacy.

        This is a heuristic approximation of perceptual quality based on
        the relationship between resolution and available bitrate.
        """
        if height == 0 or bitrate_kbps == 0:
            return None

        if height >= 2160:
            expected = 15_000.0
        elif height >= 1080:
            expected = 5_000.0
        elif height >= 720:
            expected = 2_500.0
        elif height >= 480:
            expected = 1_500.0
        else:
            expected = 800.0

        ratio = bitrate_kbps / expected
        return min(ratio, 1.0)

    async def _emit_complete(
        self,
        media_source: MediaSource,
        metrics: MediaMetrics,
        trace: str,
    ):
        await self.event_bus.publish(
            DeepScanCompleteEvent(
                media_source=media_source,
                is_upscaled=metrics.is_upscaled or False,
                ssim_score=metrics.ssim_score or 0.0,
            ).with_trace(trace),
        )


class ScanOrchestrator:
    """
    Coordinates scan workers across all three modes for a batch of sources.

    Runs each mode as a phase, allowing progressive results:
    Fast → Full → Deep. Sources are processed concurrently within each mode.
    Failed sources at one mode are still attempted at deeper modes
    (they may recover or provide partial data).
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        fast_worker: Optional[FastScanWorker] = None,
        full_worker: Optional[FullScanWorker] = None,
        deep_worker: Optional[DeepScanWorker] = None,
    ):
        self.event_bus = event_bus
        self.store = store
        self.fast_worker = fast_worker
        self.full_worker = full_worker
        self.deep_worker = deep_worker

    async def start(self):
        """Start all workers."""
        for w in (self.fast_worker, self.full_worker, self.deep_worker):
            if w:
                await w.start()

    async def stop(self):
        """Stop all workers."""
        for w in (self.fast_worker, self.full_worker, self.deep_worker):
            if w:
                await w.stop()

    async def scan_all(
        self,
        sources: List[MediaSource],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Dict[ScanMode, bool]]:
        """
        Scan all sources through all configured modes.

        Each mode phase waits for all sources to complete before
        proceeding to the next (deeper) mode.

        Returns a dict mapping source ID → {mode: success_bool}.
        """
        trace = trace_id or "scan_orchestrator"
        results: Dict[str, Dict[ScanMode, bool]] = {
            s.id: {} for s in sources
        }

        if self.fast_worker:
            logger.info("Orchestrator: starting Fast scan for %d sources", len(sources))
            fast_tasks = []
            for source in sources:
                fast_tasks.append(self._scan_one(self.fast_worker, source, trace))
            for source_id, success in zip(
                [s.id for s in sources],
                await asyncio.gather(*fast_tasks, return_exceptions=True),
            ):
                results[source_id][ScanMode.FAST] = bool(success) if not isinstance(success, Exception) else False

        if self.full_worker:
            full_sources = [s for s in sources if s.id in results]
            if full_sources:
                logger.info("Orchestrator: starting Full scan for %d sources", len(full_sources))
                full_tasks = []
                for source in full_sources:
                    full_tasks.append(self._scan_one(self.full_worker, source, trace))
                for source_id, success in zip(
                    [s.id for s in full_sources],
                    await asyncio.gather(*full_tasks, return_exceptions=True),
                ):
                    if source_id in results:
                        results[source_id][ScanMode.FULL] = bool(success) if not isinstance(success, Exception) else False

        if self.deep_worker:
            deep_sources = [s for s in sources if s.id in results]
            if deep_sources:
                logger.info("Orchestrator: starting Deep scan for %d sources", len(deep_sources))
                deep_tasks = []
                for source in deep_sources:
                    deep_tasks.append(self._scan_one(self.deep_worker, source, trace))
                for source_id, success in zip(
                    [s.id for s in deep_sources],
                    await asyncio.gather(*deep_tasks, return_exceptions=True),
                ):
                    if source_id in results:
                        results[source_id][ScanMode.DEEP] = bool(success) if not isinstance(success, Exception) else False

        return results

    async def _scan_one(
        self,
        worker: BaseScanWorker,
        source: MediaSource,
        trace: str,
    ) -> bool:
        """Scan a single source with the given worker, respecting concurrency limits."""
        async with worker._semaphore:
            return await worker.scan(source, trace)

"""
v2/core/workers/result_writer.py

Result Worker that generates output result files from scored media sources.
Subscribes to ScoreUpdatedEvent (realtime updates) and ScanJobCompletedEvent (final flush).

Reuses the original write_channel_to_file() utility for output format compatibility.
All imports from the original codebase are lazy (deferred to call-time) to avoid
module-level import cascades during testing.
"""

import asyncio
import ipaddress
import logging
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
sys.path.insert(0, _PROJECT_ROOT)

from ..bus import EventBus
from ..events import (
    ScoreUpdatedEvent,
    ScanJobCompletedEvent,
    ResultUpdatedEvent,
)
from ..store import GlobalDataStore
from ..types import MediaSource, MediaStatus

logger = logging.getLogger(__name__)


def _fast_get_ipv_type(host: str | None) -> str | None:
    """Infer IPv type from a host string without DNS resolution."""
    if not host:
        return None
    normalized = host.strip().strip("[]")
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]
    try:
        return f"ipv{ipaddress.ip_address(normalized).version}"
    except ValueError:
        return "ipv4"


class ResultWorker:
    """
    Worker that generates result files from scored media sources.

    Listens to:
    - ScoreUpdatedEvent: triggers real-time result file update (debounced)
    - ScanJobCompletedEvent: triggers final result file update

    Generates:
    - result.txt / result.m3u (configurable via config.ini)
    - ipv4/result.txt, ipv6/result.txt
    - hls result files (if open_rtmp)
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        write_interval: float = 2.0,
        realtime_write: Optional[bool] = None,
    ):
        self.event_bus = event_bus
        self.store = store
        self.write_interval = write_interval

        # Defer config import to avoid module-level cascade
        self._config = None
        self._write_channel_to_file = None
        self._result_store = None

        if realtime_write is not None:
            self.realtime_write = realtime_write
        else:
            cfg = self._get_config()
            self.realtime_write = cfg.open_realtime_write

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._debounce_task: Optional[asyncio.Task] = None
        self._dirty = False
        self._write_in_progress = False

        self._metrics = {
            "events_received": 0,
            "writes_triggered": 0,
            "writes_completed": 0,
            "writes_failed": 0,
        }

    # ── Lazy import helpers ────────────────────────────────────────────────

    def _get_config(self):
        if self._config is None:
            from utils.config import config
            self._config = config
        return self._config

    def _get_write_channel_to_file(self):
        if self._write_channel_to_file is None:
            from utils.channel import write_channel_to_file
            self._write_channel_to_file = write_channel_to_file
        return self._write_channel_to_file

    def _get_result_store(self):
        if self._result_store is None:
            from utils.result_store import result_store
            self._result_store = result_store
        return self._result_store

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        logger.info(
            "Result writer started (realtime=%s, interval=%.1fs)",
            self.realtime_write, self.write_interval,
        )

    async def stop(self):
        self._running = False
        if self._dirty:
            try:
                await self._flush()
            except Exception as e:
                logger.error("Final flush failed: %s", e)
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
            self._debounce_task = None
        logger.info("Result writer stopped")

    # ── Event loop ─────────────────────────────────────────────────────────

    async def process_queue(self, input_queue: asyncio.Queue):
        self._task = asyncio.current_task()
        while self._running:
            try:
                event = await asyncio.wait_for(input_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                self._metrics["events_received"] += 1

                if isinstance(event, ScoreUpdatedEvent):
                    await self._handle_score_update(event)
                elif isinstance(event, ScanJobCompletedEvent):
                    await self._handle_scan_complete(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._metrics["writes_failed"] += 1
                logger.error("Result worker error: %s", e)

    # ── Event handlers ─────────────────────────────────────────────────────

    async def _handle_score_update(self, event: ScoreUpdatedEvent):
        self._dirty = True
        self._metrics["writes_triggered"] += 1
        if self.realtime_write:
            self._schedule_debounce_flush()

    async def _handle_scan_complete(self, event: ScanJobCompletedEvent):
        self._dirty = True
        self._metrics["writes_triggered"] += 1
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            self._debounce_task = None
        await self._flush()

    # ── Debounce ───────────────────────────────────────────────────────────

    def _schedule_debounce_flush(self):
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_loop())

    async def _debounce_loop(self):
        self._debounce_task = asyncio.current_task()
        try:
            await asyncio.sleep(self.write_interval)
            if self._dirty and not self._write_in_progress:
                await self._flush()
        except asyncio.CancelledError:
            pass
        finally:
            self._debounce_task = None

    # ── File generation ────────────────────────────────────────────────────

    async def _flush(self):
        if not self._dirty or self._write_in_progress:
            return
        self._dirty = False
        self._write_in_progress = True

        try:
            data = self._build_category_channel_data()
            stations = data if isinstance(data, dict) else {}

            if not stations:
                logger.debug("No data to write, skipping flush")
                return

            cfg = self._get_config()
            write_fn = self._get_write_channel_to_file()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                write_fn,
                data,
                cfg.ipv6_support,
                None,
                False,
                True,
            )

            try:
                store = self._get_result_store()
                store.store_data(data)
            except Exception:
                pass

            self._metrics["writes_completed"] += 1
            logger.debug("Result files written successfully (%d categories)", len(data))

            try:
                stats = await self.store.get_stats()
                await self.event_bus.publish(
                    ResultUpdatedEvent(
                        total_stations=stats.get("total_stations", 0),
                        total_sources=stats.get("total_sources", 0),
                        file_count=len(data),
                    )
                )
            except Exception:
                pass

        except Exception as e:
            self._metrics["writes_failed"] += 1
            logger.error("Failed to write result files: %s", e)
        finally:
            self._write_in_progress = False

    # ── Data conversion ────────────────────────────────────────────────────

    def _build_category_channel_data(self) -> Dict[str, Dict[str, List[Dict]]]:
        """Build CategoryChannelData from the global data store."""
        result: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))

        for station_name, station in self.store._stations.items():
            if not station or not station.sources:
                continue

            sources = self._get_sorted_sources(station)
            category = self._infer_category(station_name, sources)

            for i, source in enumerate(sources):
                channel_data = self._media_source_to_channel_data(source, i)
                result[category][station_name].append(channel_data)

        return result

    def _get_sorted_sources(self, station) -> List[MediaSource]:
        sources = list(station.sources.values())

        def sort_key(s: MediaSource) -> tuple:
            composite = (
                s.metrics.composite_score
                if s.metrics.composite_score is not None
                else -1.0
            )
            return (-composite, s.url or "")

        sources.sort(key=sort_key)
        return sources

    def _infer_category(
        self, station_name: str, sources: List[MediaSource]
    ) -> str:
        for s in sources:
            sf = s.source_file or ""
            if "subscribe" in sf.lower():
                return "subscribe"
            elif "local" in sf.lower():
                return "local"
            elif "whitelist" in sf.lower():
                return "whitelist"
        return "list"

    def _media_source_to_channel_data(
        self, source: MediaSource, index: int
    ) -> Dict:
        url = source.url
        host = urlparse(url).netloc if url else ""
        origin = self._get_origin(source)
        ipv_type = _fast_get_ipv_type(host) if host else "ipv4"

        return {
            "id": hash(source.id) % (2**31),
            "url": url,
            "host": host,
            "resolution": source.metrics.resolution,
            "video_codec": source.metrics.video_codec,
            "audio_codec": source.metrics.audio_codec,
            "fps": source.metrics.fps,
            "origin": origin,
            "ipv_type": ipv_type,
            "headers": source.headers or None,
            "extra_info": "",
        }

    @staticmethod
    def _get_origin(source: MediaSource) -> str:
        sf = source.source_file or ""
        if "local" in sf.lower():
            return "local"
        elif "whitelist" in sf.lower():
            return "whitelist"
        elif "hls" in sf.lower() or "rtmp" in sf.lower():
            return "hls"
        return "subscribe"

    def get_metrics(self) -> Dict[str, int]:
        return dict(self._metrics)

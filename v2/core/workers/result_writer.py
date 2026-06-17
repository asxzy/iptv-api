"""
v2/core/workers/result_writer.py

Result writer that generates output files from the global data store.
Supports TXT and M3U formats with IPv4/IPv6 splitting.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from ..bus import EventBus
from ..events import (
    ResultWriterStartedEvent,
    ResultWriterCompletedEvent,
    ResultWriterErrorEvent,
    ScoreUpdatedEvent,
    RankingUpdatedEvent,
)
from ..types import MediaSource, MediaStatus
from ..store import GlobalDataStore

logger = logging.getLogger(__name__)


class ResultWriter:
    """
    Generates output result files from the global data store.
    Supports TXT, M3U formats with the best source per station.
    """

    def __init__(
        self,
        event_bus: EventBus,
        data_store: GlobalDataStore,
        output_dir: str = "output/v2",
    ):
        self.event_bus = event_bus
        self.data_store = data_store
        self.output_dir = output_dir

    async def write_all(self, formats: Optional[List[str]] = None) -> Dict[str, str]:
        """Write all output files. Returns dict of format -> filepath."""
        if formats is None:
            formats = ["txt", "m3u"]

        os.makedirs(self.output_dir, exist_ok=True)

        await self.event_bus.publish(
            ResultWriterStartedEvent(
                output_dir=self.output_dir,
                formats=formats,
            )
        )

        try:
            # Get all stations and their best sources
            stations = await self.data_store.get_all_stations()
            best_sources: List[MediaSource] = []

            for station in stations.values():
                if not station.sources:
                    continue
                # Pick the source with the highest score
                best = max(
                    station.sources.values(),
                    key=lambda s: s.score,
                )
                best_sources.append(best)

            paths = {}

            if "txt" in formats:
                paths["txt"] = await self._write_txt(best_sources)
            if "m3u" in formats:
                paths["m3u"] = await self._write_m3u(best_sources)

            await self.event_bus.publish(
                ResultWriterCompletedEvent(
                    output_dir=self.output_dir,
                    formats=formats,
                    total_stations=len(best_sources),
                    total_sources=len(stations),
                )
            )

            return paths

        except Exception as e:
            logger.error(f"Result writer error: {e}")
            await self.event_bus.publish(
                ResultWriterErrorEvent(
                    output_dir=self.output_dir,
                    error_message=str(e),
                )
            )
            raise

    async def _write_txt(self, sources: List[MediaSource]) -> str:
        """Write TXT format: station_name,url"""
        path = os.path.join(self.output_dir, "result.txt")
        with open(path, "w") as f:
            for src in sources:
                f.write(f"{src.station_name},{src.url}\n")
        return path

    async def _write_m3u(self, sources: List[MediaSource]) -> str:
        """Write M3U format with EXTINF tags."""
        path = os.path.join(self.output_dir, "result.m3u")
        with open(path, "w") as f:
            f.write("#EXTM3U\n")
            for src in sources:
                f.write(f"#EXTINF:-1,{src.station_name}\n")
                f.write(f"{src.url}\n")
        return path
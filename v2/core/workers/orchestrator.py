"""
v2/core/workers/orchestrator.py

Main orchestrator coordinating the full pipeline:
Discovery → Validation → Scan → Scoring → Result Writer.
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Dict, List, Optional, Any

from ..bus import EventBus
from ..store import GlobalDataStore
from ..events import (
    ScanJobStartedEvent,
    ScanJobProgressEvent,
    ScanJobCompletedEvent,
    ScanJobFailedEvent,
)
from ..types import MediaSource, MediaStatus, ScanMode

from .discovery import DiscoveryWorker
from .validation import ValidationWorker
from .scan import FastScanWorker, FullScanWorker, DeepScanWorker, ScanOrchestrator
from .scoring import ScoringWorker
from .result_writer import ResultWriter

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Coordinates the full pipeline: Discovery → Validation → Scan → Scoring → Result Writer.
    """

    def __init__(
        self,
        event_bus: EventBus,
        data_store: GlobalDataStore,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.event_bus = event_bus
        self.data_store = data_store
        self.config = config or {}

        # Pipeline stage switches
        self._open_discovery = self.config.get("open_discovery", True)
        self._open_validation = self.config.get("open_validation", True)
        self._open_scoring = self.config.get("open_scoring", True)

        # Scan mode configuration
        scan_modes = self.config.get("scan_modes", ["fast", "full", "deep"])
        self._scan_modes = [ScanMode[m.upper()] for m in scan_modes if m.upper() in ScanMode.__members__]

        # Concurrency limits
        self._concurrency_fast = self.config.get("concurrency_fast", 10)
        self._concurrency_full = self.config.get("concurrency_full", 3)
        self._concurrency_deep = self.config.get("concurrency_deep", 2)

        # Output directory
        self._output_dir = self.config.get("output_dir", "output/v2")

        # Workers (created lazily or injected)
        self.discovery_worker: Optional[DiscoveryWorker] = None
        self.validation_worker: Optional[ValidationWorker] = None
        self.scan_orchestrator: Optional[ScanOrchestrator] = None
        self.scoring_worker: Optional[ScoringWorker] = None
        self.result_writer: Optional[ResultWriter] = None

        # Job state
        self._job_id: Optional[str] = None
        self._start_time: Optional[float] = None
        self._phase: str = ""

    async def run(self) -> Dict[str, Any]:
        """
        Run the full pipeline. Returns a dict with job_id, success, and stats.
        """
        self._job_id = str(uuid.uuid4())
        self._start_time = time.time()
        self._phase = ""
        result = {
            "job_id": self._job_id,
            "success": False,
        }

        # Emit job started event
        await self.event_bus.publish(
            ScanJobStartedEvent(
                job_id=self._job_id,
                mode=ScanMode.FULL,
                total_sources=0,
            )
        )

        try:
            # Initialize workers
            await self._init_workers()

            # Phase 1: Discovery
            total_sources = await self._run_discovery()
            self._publish_progress("discovery", total_sources)

            # Phase 2: Validation
            validated_sources = await self._run_validation()
            self._publish_progress("validation", validated_sources)

            # Phase 3: Scan
            scan_results = await self._run_scan()
            self._publish_progress("scan", len(scan_results))

            # Phase 4: Scoring
            if self._open_scoring:
                await self._run_scoring()
            self._publish_progress("scoring", 0)

            # Phase 5: Result Writer
            output_paths = await self._run_write()
            self._publish_progress("writing", len(output_paths))

            # Emit completed event
            elapsed = time.time() - self._start_time
            await self.event_bus.publish(
                ScanJobCompletedEvent(
                    job_id=self._job_id,
                    total_sources=total_sources,
                    succeeded=validated_sources,
                    failed=total_sources - validated_sources,
                    elapsed_seconds=elapsed,
                )
            )

            result["success"] = True
            result["total_sources"] = total_sources
            result["validated_sources"] = validated_sources
            result["elapsed_seconds"] = elapsed
            result["output_paths"] = output_paths

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            await self.event_bus.publish(
                ScanJobFailedEvent(
                    job_id=self._job_id,
                    error_message=str(e),
                )
            )
            result["success"] = False
            result["error"] = str(e)

        return result

    async def _init_workers(self):
        """Initialize all workers if not already injected."""
        if self.discovery_worker is None:
            self.discovery_worker = DiscoveryWorker(
                event_bus=self.event_bus,
            )

        if self.validation_worker is None:
            self.validation_worker = ValidationWorker(
                event_bus=self.event_bus,
                store=self.data_store,
            )

        if self.scan_orchestrator is None:
            fast_worker = None
            full_worker = None
            deep_worker = None

            if ScanMode.FAST in self._scan_modes:
                fast_worker = FastScanWorker(
                    event_bus=self.event_bus,
                    store=self.data_store,
                    timeout=self.config.get("timeout_fast", 5),
                    max_concurrent=self._concurrency_fast,
                )

            if ScanMode.FULL in self._scan_modes:
                full_worker = FullScanWorker(
                    event_bus=self.event_bus,
                    store=self.data_store,
                    timeout=self.config.get("timeout_full", 15),
                    max_concurrent=self._concurrency_full,
                )

            if ScanMode.DEEP in self._scan_modes:
                deep_worker = DeepScanWorker(
                    event_bus=self.event_bus,
                    store=self.data_store,
                    timeout=self.config.get("timeout_deep", 60),
                    max_concurrent=self._concurrency_deep,
                )

            self.scan_orchestrator = ScanOrchestrator(
                event_bus=self.event_bus,
                store=self.data_store,
                fast_worker=fast_worker,
                full_worker=full_worker,
                deep_worker=deep_worker,
            )

        if self.scoring_worker is None and self._open_scoring:
            self.scoring_worker = ScoringWorker(
                event_bus=self.event_bus,
                data_store=self.data_store,
                config={
                    "weight_quality": self.config.get("weight_quality", 0.7),
                    "weight_loadability": self.config.get("weight_loadability", 0.3),
                },
            )

        if self.result_writer is None:
            self.result_writer = ResultWriter(
                event_bus=self.event_bus,
                data_store=self.data_store,
                output_dir=self._output_dir,
            )

    async def _run_discovery(self) -> int:
        """Run discovery stage. Returns number of sources discovered."""
        if not self._open_discovery:
            logger.info("Discovery disabled, skipping")
            return 0

        self._phase = "discovery"
        logger.info("Starting discovery phase")

        source_files = self.config.get("source_files", ["subscribe.txt"])
        total = 0
        for src_file in source_files:
            # Check if file exists
            if os.path.exists(src_file):
                count = await self.discovery_worker.process_file(src_file)
                total += count
                logger.info(f"Discovered {count} sources from {src_file}")

        return total

    async def _run_validation(self) -> int:
        """Run validation stage. Returns number of validated sources."""
        if not self._open_validation:
            logger.info("Validation disabled, skipping")
            return 0

        self._phase = "validation"
        logger.info("Starting validation phase")

        # Process validation queue
        await self.validation_worker.start()
        try:
            await self.validation_worker.process_queue()
        finally:
            await self.validation_worker.stop()

        # Count validated sources
        stats = await self.data_store.get_stats()
        return stats.get("total_sources", 0)

    async def _run_scan(self) -> Dict[str, Dict[ScanMode, bool]]:
        """Run scan stage. Returns per-source scan results."""
        if not self._scan_modes:
            logger.info("No scan modes configured, skipping scan")
            return {}

        self._phase = "scan"
        logger.info(f"Starting scan phase with modes: {self._scan_modes}")

        # Get all discovered/validated sources from the store
        stations = await self.data_store.get_all_stations()
        sources: List[MediaSource] = []
        for station in stations.values():
            sources.extend(station.sources.values())

        if not sources:
            logger.info("No sources to scan")
            return {}

        # Run scan
        await self.scan_orchestrator.start()
        try:
            results = await self.scan_orchestrator.scan_all(sources)
            return results
        finally:
            await self.scan_orchestrator.stop()

    async def _run_scoring(self):
        """Run scoring stage (workers process events reactively)."""
        self._phase = "scoring"
        logger.info("Scoring phase (reactive via events)")
        # Scoring happens reactively as workers emit events
        # We wait a brief moment for pending events to process
        await asyncio.sleep(0.1)

    async def _run_write(self) -> Dict[str, str]:
        """Run result writer stage. Returns dict of format -> filepath."""
        self._phase = "writing"
        logger.info("Starting result writer phase")

        return await self.result_writer.write_all()

    def _publish_progress(self, phase: str, completed: int):
        """Publish a progress event (non-blocking)."""
        asyncio.ensure_future(
            self.event_bus.publish(
                ScanJobProgressEvent(
                    job_id=self._job_id or "",
                    phase=phase,
                    completed=completed,
                    total=completed,
                )
            )
        )
"""
v2/core/tests/test_orchestrator.py

Tests for the Orchestrator implementation.
"""

import pytest
import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from core.workers.orchestrator import Orchestrator
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    ScanJobStartedEvent,
    ScanJobProgressEvent,
    ScanJobCompletedEvent,
    ScanJobFailedEvent,
)
from core.types import MediaSource, MediaStatus, MediaMetrics, ScanMode


def make_source(
    url="http://example.com/stream.m3u8",
    station_name="Test Station",
    status=MediaStatus.DISCOVERED,
):
    return MediaSource(
        id="test-id",
        url=url,
        station_name=station_name,
        source_file="test.txt",
        headers={},
        metrics=MediaMetrics(),
        status=status,
        score=0.0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def data_store():
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def orchestrator(event_bus, data_store, temp_dir):
    """Create an Orchestrator with real bus/store and mocked workers."""
    return Orchestrator(
        event_bus=event_bus,
        data_store=data_store,
        config={
            "output_dir": temp_dir,
            "scan_modes": ["fast"],
        },
    )


class TestOrchestrator:
    """Test the Orchestrator functionality."""

    @pytest.mark.asyncio
    async def test_run_with_mocked_workers(self, event_bus, data_store, temp_dir):
        """Test that run executes all stages with mocked workers."""
        orch = Orchestrator(
            event_bus=event_bus,
            data_store=data_store,
            config={
                "output_dir": temp_dir,
                "scan_modes": [],
                "open_discovery": False,
                "open_validation": False,
                "open_scoring": False,
            },
        )
        orch.discovery_worker = AsyncMock()
        orch.discovery_worker.discover_from_file = AsyncMock(return_value=1)
        orch.validation_worker = AsyncMock()
        orch.validation_worker.process_queue = AsyncMock()
        orch.scan_orchestrator = AsyncMock()
        orch.scan_orchestrator.scan_all = AsyncMock(return_value={})
        orch.scoring_worker = AsyncMock()
        orch.scoring_worker.on_deep_scan_complete = AsyncMock()
        orch.result_writer = AsyncMock()
        orch.result_writer.write_all = AsyncMock(return_value={})

        # Act
        result = await orch.run()

        # Assert
        assert result["job_id"] is not None
        assert result["success"] is True
        # With open_discovery=False, discover_from_file should not be called
        orch.discovery_worker.discover_from_file.assert_not_called()
        orch.scan_orchestrator.scan_all.assert_not_called()
        orch.result_writer.write_all.assert_called()

    @pytest.mark.asyncio
    async def test_job_started_event_emitted(self, event_bus, data_store, temp_dir):
        """Test that ScanJobStartedEvent is emitted when run starts."""
        queue = event_bus.subscribe(ScanJobStartedEvent)

        orch = Orchestrator(
            event_bus=event_bus,
            data_store=data_store,
            config={
                "output_dir": temp_dir,
                "open_discovery": False,
                "open_validation": False,
                "open_scoring": False,
                "scan_modes": [],
            },
        )
        orch.discovery_worker = AsyncMock()
        orch.discovery_worker.discover_from_file = AsyncMock(return_value=0)
        orch.validation_worker = AsyncMock()
        orch.validation_worker.process_queue = AsyncMock()
        orch.scan_orchestrator = AsyncMock()
        orch.scan_orchestrator.scan_all = AsyncMock(return_value={})
        orch.scoring_worker = AsyncMock()
        orch.result_writer = AsyncMock()
        orch.result_writer.write_all = AsyncMock(return_value={})

        # Act
        await orch.run()

        # Assert
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, ScanJobStartedEvent)
        assert event.job_id != ""
        assert event.job_id is not None

    @pytest.mark.asyncio
    async def test_job_completed_event_emitted(self, orchestrator, event_bus):
        """Test that ScanJobCompletedEvent is emitted on success."""
        queue = event_bus.subscribe(ScanJobCompletedEvent)

        orchestrator.discovery_worker = AsyncMock()
        orchestrator.discovery_worker.discover_from_file = AsyncMock(return_value=0)
        orchestrator.validation_worker = AsyncMock()
        orchestrator.validation_worker.process_queue = AsyncMock()
        orchestrator.scan_orchestrator = AsyncMock()
        orchestrator.scan_orchestrator.scan_all = AsyncMock(return_value={})
        orchestrator.scoring_worker = AsyncMock()
        orchestrator.result_writer = AsyncMock()
        orchestrator.result_writer.write_all = AsyncMock(return_value={})

        await orchestrator.run()

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, ScanJobCompletedEvent)
        assert event.total_sources >= 0

    @pytest.mark.asyncio
    async def test_job_failed_event_on_error(self, event_bus, data_store, temp_dir):
        """Test that ScanJobFailedEvent is emitted on error."""
        queue = event_bus.subscribe(ScanJobFailedEvent)

        orch = Orchestrator(
            event_bus=event_bus,
            data_store=data_store,
            config={
                "output_dir": temp_dir,
                "open_discovery": True,
                "open_validation": False,
                "open_scoring": False,
                "scan_modes": [],
                "source_files": [],  # No files to process
            },
        )
        # Override _run_discovery to throw an error directly
        original_run_discovery = orch._run_discovery
        async def failing_discovery():
            raise RuntimeError("Discovery failed")
        orch._run_discovery = failing_discovery

        await orch.run()

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, ScanJobFailedEvent)
        assert "Discovery failed" in event.error_message

    @pytest.mark.asyncio
    async def test_configurable_scan_modes(self, event_bus, data_store, temp_dir):
        """Test that config.scan_modes controls which scan workers run."""
        store = data_store
        # Pre-populate store with a source so scan has work to do
        source = make_source(
            url="http://example.com/stream.m3u8",
            station_name="Test Channel",
            status=MediaStatus.VALIDATED,
        )
        await store.add_or_update_source(source)

        orch = Orchestrator(
            event_bus=event_bus,
            data_store=store,
            config={
                "output_dir": temp_dir,
                "scan_modes": ["fast"],
                "open_discovery": False,
                "open_validation": False,
                "open_scoring": False,
            },
        )

        assert orch.config["scan_modes"] == ["fast"]
        orch.discovery_worker = AsyncMock()
        orch.discovery_worker.discover_from_file = AsyncMock(return_value=0)
        orch.validation_worker = AsyncMock()
        orch.validation_worker.process_queue = AsyncMock()
        orch.scan_orchestrator = AsyncMock()
        orch.scan_orchestrator.scan_all = AsyncMock(return_value={})
        orch.scoring_worker = AsyncMock()
        orch.result_writer = AsyncMock()
        orch.result_writer.write_all = AsyncMock(return_value={})

        await orch.run()
        orch.scan_orchestrator.scan_all.assert_called()

    @pytest.mark.asyncio
    async def test_configurable_concurrency(self, event_bus, data_store, temp_dir):
        """Test concurrency limits are passed to workers."""
        orch = Orchestrator(
            event_bus=event_bus,
            data_store=data_store,
            config={
                "output_dir": temp_dir,
                "scan_modes": ["fast", "full", "deep"],
                "concurrency_fast": 5,
                "concurrency_full": 3,
                "concurrency_deep": 2,
            },
        )
        assert orch.config["concurrency_fast"] == 5
        assert orch.config["concurrency_full"] == 3
        assert orch.config["concurrency_deep"] == 2

    @pytest.mark.asyncio
    async def test_progress_tracking(self, orchestrator, event_bus, data_store):
        """Test that progress is tracked during the run."""
        progress_queue = event_bus.subscribe(ScanJobProgressEvent)

        orchestrator.discovery_worker = AsyncMock()
        orchestrator.discovery_worker.discover_from_file = AsyncMock(return_value=2)
        orchestrator.validation_worker = AsyncMock()
        orchestrator.validation_worker.process_queue = AsyncMock()
        orchestrator.scan_orchestrator = AsyncMock()
        orchestrator.scan_orchestrator.scan_all = AsyncMock(return_value={})
        orchestrator.scoring_worker = AsyncMock()
        orchestrator.result_writer = AsyncMock()
        orchestrator.result_writer.write_all = AsyncMock(return_value={})

        await orchestrator.run()

        # Should get at least one progress event
        events = []
        try:
            while True:
                ev = await asyncio.wait_for(progress_queue.get(), timeout=0.3)
                events.append(ev)
        except asyncio.TimeoutError:
            pass

        assert len(events) > 0
        # One of the events should be from the discovery phase or later
        phases = [e.phase for e in events]
        assert "discovery" in phases or "validation" in phases

    @pytest.mark.asyncio
    async def test_run_with_real_result_writer(self, event_bus, data_store, temp_dir):
        """Test that run produces output files when using real result writer."""
        store = data_store
        source = make_source(
            url="http://example.com/stream.m3u8",
            station_name="Test Channel",
            status=MediaStatus.DEEP_SCANNED,
        )
        await store.add_or_update_source(source)

        orch = Orchestrator(
            event_bus=event_bus,
            data_store=data_store,
            config={
                "output_dir": temp_dir,
                "open_discovery": False,
                "open_validation": False,
                "open_scoring": False,
                "scan_modes": [],
            },
        )

        # Mock only the parts we don't need
        orch.discovery_worker = AsyncMock()
        orch.discovery_worker.discover_from_file = AsyncMock(return_value=0)
        orch.validation_worker = AsyncMock()
        orch.validation_worker.process_queue = AsyncMock()

        result = await orch.run()

        assert result["success"] is True
        # Result writer should have been used
        txt_path = os.path.join(temp_dir, "result.txt")
        m3u_path = os.path.join(temp_dir, "result.m3u")
        assert os.path.exists(txt_path)
        assert os.path.exists(m3u_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
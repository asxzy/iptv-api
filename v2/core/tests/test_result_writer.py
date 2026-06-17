"""
v2/core/tests/test_result_writer.py

Tests for the ResultWriter implementation.
"""

import pytest
import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import sys
sys.path.insert(0, '/Users/asxzy/src/iptv-api/v2')

from core.workers.result_writer import ResultWriter
from core.bus import EventBus
from core.store import GlobalDataStore
from core.events import (
    ResultWriterStartedEvent,
    ResultWriterCompletedEvent,
    ResultWriterErrorEvent,
    ScoreUpdatedEvent,
    RankingUpdatedEvent,
)
from core.types import MediaSource, MediaStatus, MediaMetrics, Station


def make_media_source(
    url="http://example.com/stream.m3u8",
    station_name="Test Station",
    source_file="test.txt",
    metrics=None,
    status=MediaStatus.DEEP_SCANNED,
    score=0.85,
):
    """Helper to create a MediaSource with given attributes."""
    if metrics is None:
        metrics = MediaMetrics(
            resolution="1920x1080",
            fps=30.0,
            video_codec="h264",
            speed_mbps=6.0,
            delay_ms=50.0,
        )
    return MediaSource(
        id="test-id",
        url=url,
        station_name=station_name,
        source_file=source_file,
        headers={},
        metrics=metrics,
        status=status,
        score=score,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def make_ipv4_source(url="http://93.184.216.34/stream.m3u8", **kwargs):
    """Create a source with IPv4 URL."""
    return make_media_source(url=url, **kwargs)


def make_ipv6_source(url="http://[2001:db8::1]/stream.m3u8", **kwargs):
    """Create a source with IPv6 URL."""
    return make_media_source(url=url, **kwargs)


@pytest.fixture
def event_bus():
    """Create a mock event bus."""
    bus = AsyncMock(spec=EventBus)
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def data_store():
    """Create a real GlobalDataStore."""
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def temp_output_dir():
    """Create a temporary directory for output files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


@pytest.fixture
def result_writer(event_bus, data_store, temp_output_dir):
    """Create a ResultWriter instance."""
    writer = ResultWriter(
        event_bus=event_bus,
        data_store=data_store,
        output_dir=temp_output_dir,
    )
    return writer


class TestResultWriter:
    """Test the ResultWriter functionality."""

    @pytest.mark.asyncio
    async def test_txt_output_generation(self, result_writer, data_store, temp_output_dir):
        """Test that TXT output is generated correctly."""
        # Arrange: add sources to the store
        source1 = make_media_source(
            url="http://example.com/ch1.m3u8",
            station_name="Channel One",
            score=0.9,
        )
        source2 = make_media_source(
            url="http://example.com/ch2.m3u8",
            station_name="Channel Two",
            score=0.8,
        )
        await data_store.add_or_update_source(source1)
        await data_store.add_or_update_source(source2)

        # Act: write output
        await result_writer.write_all()

        # Assert: TXT file exists and contains correct content
        txt_path = os.path.join(temp_output_dir, "result.txt")
        assert os.path.exists(txt_path)
        with open(txt_path, "r") as f:
            content = f.read()
        assert "Channel One,http://example.com/ch1.m3u8" in content
        assert "Channel Two,http://example.com/ch2.m3u8" in content

    @pytest.mark.asyncio
    async def test_m3u_output_generation(self, result_writer, data_store, temp_output_dir):
        """Test that M3U output is generated correctly."""
        # Arrange
        source1 = make_media_source(
            url="http://example.com/ch1.m3u8",
            station_name="Channel One",
            score=0.9,
        )
        await data_store.add_or_update_source(source1)

        # Act
        await result_writer.write_all()

        # Assert
        m3u_path = os.path.join(temp_output_dir, "result.m3u")
        assert os.path.exists(m3u_path)
        with open(m3u_path, "r") as f:
            content = f.read()
        assert "#EXTM3U" in content
        assert "#EXTINF:-1,Channel One" in content
        assert "http://example.com/ch1.m3u8" in content

    @pytest.mark.asyncio
    async def test_best_source_per_station(self, result_writer, data_store, temp_output_dir):
        """Test that only the highest-scored source per station is included."""
        # Arrange: add two sources for same station with different scores
        best = make_media_source(
            url="http://example.com/best.m3u8",
            station_name="Test Channel",
            score=0.95,
        )
        worst = make_media_source(
            url="http://example.com/worst.m3u8",
            station_name="Test Channel",
            score=0.5,
        )
        await data_store.add_or_update_source(best)
        await data_store.add_or_update_source(worst)

        # Act
        await result_writer.write_all()

        # Assert: only the best source is included
        txt_path = os.path.join(temp_output_dir, "result.txt")
        with open(txt_path, "r") as f:
            content = f.read()
        assert "best.m3u8" in content
        assert "worst.m3u8" not in content

    @pytest.mark.asyncio
    async def test_ipv4_ipv6_splitting(self, result_writer, data_store, temp_output_dir):
        """Test that IPv4 and IPv6 sources are split into separate output files."""
        # Arrange
        source_ipv4 = make_ipv4_source(
            url="http://93.184.216.34/stream.m3u8",
            station_name="IPv4 Channel",
            score=0.9,
        )
        source_ipv6 = make_ipv6_source(
            url="http://[2001:db8::1]/stream.m3u8",
            station_name="IPv6 Channel",
            score=0.85,
        )
        await data_store.add_or_update_source(source_ipv4)
        await data_store.add_or_update_source(source_ipv6)

        # Act
        await result_writer.write_all()

        # Assert
        # TXT should include both
        txt_path = os.path.join(temp_output_dir, "result.txt")
        with open(txt_path, "r") as f:
            content = f.read()
        assert "IPv4 Channel" in content
        assert "IPv6 Channel" in content

        # M3U should include both
        m3u_path = os.path.join(temp_output_dir, "result.m3u")
        assert os.path.exists(m3u_path)
        with open(m3u_path, "r") as f:
            content = f.read()
        assert "#EXTINF:-1,IPv4 Channel" in content
        assert "#EXTINF:-1,IPv6 Channel" in content

    @pytest.mark.asyncio
    async def test_event_emission(self, result_writer, event_bus, data_store, temp_output_dir):
        """Test that events are emitted during write cycles."""
        source = make_media_source()
        await data_store.add_or_update_source(source)

        # Act
        await result_writer.write_all()

        # Assert: both started and completed events are published
        event_bus.publish.assert_awaited()
        started_call = None
        completed_call = None
        for call in event_bus.publish.await_args_list:
            args, kwargs = call
            if isinstance(args[0], ResultWriterStartedEvent):
                started_call = args[0]
            elif isinstance(args[0], ResultWriterCompletedEvent):
                completed_call = args[0]
        assert started_call is not None, "ResultWriterStartedEvent not published"
        assert completed_call is not None, "ResultWriterCompletedEvent not published"
        assert completed_call.total_stations == 1
        assert completed_call.total_sources == 1

    @pytest.mark.asyncio
    async def test_empty_store_produces_empty_output(self, result_writer, temp_output_dir):
        """Test that empty store produces empty output files."""
        # Act
        await result_writer.write_all()

        # Assert: files are created but empty
        txt_path = os.path.join(temp_output_dir, "result.txt")
        m3u_path = os.path.join(temp_output_dir, "result.m3u")
        assert os.path.exists(txt_path)
        assert os.path.exists(m3u_path)
        with open(txt_path, "r") as f:
            assert f.read() == ""
        with open(m3u_path, "r") as f:
            assert f.read() == "#EXTM3U\n"

    @pytest.mark.asyncio
    async def test_multiple_sources_same_station_best_selected(self, result_writer, data_store, temp_output_dir):
        """Test best source selection when station has multiple sources."""
        sources = [
            make_media_source(
                url=f"http://example.com/src{i}.m3u8",
                station_name="Multi Source",
                score=score,
            )
            for i, score in enumerate([0.1, 0.5, 0.9, 0.3, 0.7])
        ]
        for src in sources:
            await data_store.add_or_update_source(src)

        await result_writer.write_all()

        txt_path = os.path.join(temp_output_dir, "result.txt")
        with open(txt_path, "r") as f:
            content = f.read()
        # Best score is 0.9 at index 2
        assert "src2.m3u8" in content
        assert "src0.m3u8" not in content
        assert "src1.m3u8" not in content
        assert "src3.m3u8" not in content
        assert "src4.m3u8" not in content

    @pytest.mark.asyncio
    async def test_write_formats_control(self, result_writer, data_store, temp_output_dir):
        """Test that formats parameter controls which output files are written."""
        source = make_media_source()
        await data_store.add_or_update_source(source)

        # Act: write only TXT
        await result_writer.write_all(formats=["txt"])

        # Assert
        txt_path = os.path.join(temp_output_dir, "result.txt")
        m3u_path = os.path.join(temp_output_dir, "result.m3u")
        assert os.path.exists(txt_path)
        # M3U should NOT exist since we only asked for txt
        assert not os.path.exists(m3u_path)

    @pytest.mark.asyncio
    async def test_write_nonexistent_format(self, result_writer, data_store, temp_output_dir):
        """Test that requesting a nonexistent format doesn't crash."""
        source = make_media_source()
        await data_store.add_or_update_source(source)

        # Act: should not raise
        await result_writer.write_all(formats=["txt", "nonexistent"])

        # Assert: txt file was written
        txt_path = os.path.join(temp_output_dir, "result.txt")
        assert os.path.exists(txt_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
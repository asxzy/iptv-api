"""
v2/core/tests/test_data_store.py

Comprehensive tests for the GlobalDataStore implementation.
Run with: python -m pytest core/tests/test_data_store.py -v
"""

import pytest
import asyncio
from core.store import GlobalDataStore
from core.types import MediaSource, MediaMetrics, generate_media_id


@pytest.fixture
def fresh_store():
    """Create a fresh GlobalDataStore for each test."""
    GlobalDataStore.reset_instance()
    return GlobalDataStore()


@pytest.fixture
def sample_media_source():
    """Create a sample media source."""
    return MediaSource(
        id=generate_media_id("http://example.com/stream.m3u8", "CCTV1"),
        url="http://example.com/stream.m3u8",
        station_name="CCTV1",
        source_file="config/subscribe.txt"
    )


class TestGlobalDataStore:
    """Test suite for GlobalDataStore."""
    
    @pytest.mark.asyncio
    async def test_create_station(self, fresh_store):
        """Test creating a station."""
        station = await fresh_store.get_or_create_station("CCTV1")
        assert station.name == "CCTV1"
        assert station.source_count == 0
    
    @pytest.mark.asyncio
    async def test_add_source(self, fresh_store, sample_media_source):
        """Test adding a media source."""
        store = fresh_store
        await store.add_or_update_source(sample_media_source)
        
        # Verify station was created
        station = await store.get_station("CCTV1")
        assert station is not None
        assert station.source_count == 1
        
        # Verify source exists
        source = await store.get_source("CCTV1", "http://example.com/stream.m3u8")
        assert source is not None
        assert source.url == "http://example.com/stream.m3u8"
    
    @pytest.mark.asyncio
    async def test_update_source(self, fresh_store, sample_media_source):
        """Test updating an existing media source."""
        store = fresh_store
        # Add initial source
        await store.add_or_update_source(sample_media_source)
        
        # Update with new metrics
        updated = sample_media_source.with_metrics(MediaMetrics(
            resolution="1920x1080",
            fps=30.0
        ))
        await store.add_or_update_source(updated)
        
        # Verify updated
        source = await store.get_source("CCTV1", "http://example.com/stream.m3u8")
        assert source.metrics.resolution == "1920x1080"
        assert source.metrics.fps == 30.0
    
    @pytest.mark.asyncio
    async def test_concurrent_writes_same_station(self, fresh_store):
        """Test concurrent writes to the same station."""
        store = fresh_store
        sources = [
            MediaSource(
                id=generate_media_id(f"http://example.com/stream{i}.m3u8", "CCTV1"),
                url=f"http://example.com/stream{i}.m3u8",
                station_name="CCTV1",
                source_file="config/subscribe.txt"
            )
            for i in range(10)
        ]
        
        # Add all sources concurrently
        await asyncio.gather(*[store.add_or_update_source(s) for s in sources])
        
        # Verify all were added
        station = await store.get_station("CCTV1")
        assert station.source_count == 10
    
    @pytest.mark.asyncio
    async def test_concurrent_writes_different_stations(self, fresh_store):
        """Test concurrent writes to different stations."""
        store = fresh_store
        sources = [
            MediaSource(
                id=generate_media_id(f"http://example.com/stream{i}.m3u8", f"CCTV{i}"),
                url=f"http://example.com/stream{i}.m3u8",
                station_name=f"CCTV{i}",
                source_file="config/subscribe.txt"
            )
            for i in range(5)
        ]
        
        # Add all sources concurrently
        await asyncio.gather(*[store.add_or_update_source(s) for s in sources])
        
        # Verify all stations created
        assert len(store) == 5
        for i in range(5):
            station = await store.get_station(f"CCTV{i}")
            assert station is not None
            assert station.source_count == 1
    
    @pytest.mark.asyncio
    async def test_get_all_stations(self, fresh_store, sample_media_source):
        """Test getting all stations."""
        store = fresh_store
        await store.add_or_update_source(sample_media_source)
        
        # Add another station
        source2 = MediaSource(
            id=generate_media_id("http://example.com/stream2.m3u8", "CCTV2"),
            url="http://example.com/stream2.m3u8",
            station_name="CCTV2",
            source_file="config/subscribe.txt"
        )
        await store.add_or_update_source(source2)
        
        all_stations = await store.get_all_stations()
        assert len(all_stations) == 2
        assert "CCTV1" in all_stations
        assert "CCTV2" in all_stations
    
    @pytest.mark.asyncio
    async def test_stats(self, fresh_store, sample_media_source):
        """Test statistics tracking."""
        store = fresh_store
        # Initially empty
        stats = await store.get_stats()
        assert stats['total_stations'] == 0
        assert stats['total_sources'] == 0
        
        # Add source
        await store.add_or_update_source(sample_media_source)
        
        stats = await store.get_stats()
        assert stats['total_stations'] == 1
        assert stats['total_sources'] == 1
        assert stats['sources_added'] == 1
    
    @pytest.mark.asyncio
    async def test_snapshot(self, fresh_store, sample_media_source):
        """Test snapshot functionality."""
        store = fresh_store
        await store.add_or_update_source(sample_media_source)
        
        # Take snapshot
        snapshot = await store.snapshot()
        
        # Verify snapshot is independent
        assert len(snapshot) == 1
        assert "CCTV1" in snapshot
        
        # Add another source
        source2 = MediaSource(
            id=generate_media_id("http://example.com/stream2.m3u8", "CCTV2"),
            url="http://example.com/stream2.m3u8",
            station_name="CCTV2",
            source_file="config/subscribe.txt"
        )
        await store.add_or_update_source(source2)
        
        # Snapshot should not include new station
        assert len(snapshot) == 1
    
    @pytest.mark.asyncio
    async def test_clear(self, fresh_store, sample_media_source):
        """Test clearing all data."""
        store = fresh_store
        await store.add_or_update_source(sample_media_source)
        assert len(store) == 1
        
        await store.clear()
        
        assert len(store) == 0
        all_stations = await store.get_all_stations()
        assert len(all_stations) == 0
    
    @pytest.mark.asyncio
    async def test_multiple_sources_same_station(self, fresh_store):
        """Test adding multiple sources to the same station."""
        store = fresh_store
        for i in range(5):
            source = MediaSource(
                id=generate_media_id(f"http://example.com/stream{i}.m3u8", "CCTV1"),
                url=f"http://example.com/stream{i}.m3u8",
                station_name="CCTV1",
                source_file="config/subscribe.txt"
            )
            await store.add_or_update_source(source)
        
        station = await store.get_station("CCTV1")
        assert station.source_count == 5


class TestGlobalDataStoreEdgeCases:
    """Edge case tests for GlobalDataStore."""
    
    @pytest.mark.asyncio
    async def test_empty_station_name(self, fresh_store):
        """Test handling of empty station names."""
        store = fresh_store
        source = MediaSource(
            id=generate_media_id("http://example.com/stream.m3u8", ""),
            url="http://example.com/stream.m3u8",
            station_name="",
            source_file="config/subscribe.txt"
        )
        await store.add_or_update_source(source)
        
        station = await store.get_station("")
        assert station is not None
    
    @pytest.mark.asyncio
    async def test_very_long_station_name(self, fresh_store):
        """Test handling of very long station names."""
        store = fresh_store
        long_name = "A" * 1000
        source = MediaSource(
            id=generate_media_id("http://example.com/stream.m3u8", long_name),
            url="http://example.com/stream.m3u8",
            station_name=long_name,
            source_file="config/subscribe.txt"
        )
        await store.add_or_update_source(source)
        
        station = await store.get_station(long_name)
        assert station is not None
        assert station.name == long_name
    
    @pytest.mark.asyncio
    async def test_special_characters_in_station_name(self, fresh_store):
        """Test handling of special characters in station names."""
        store = fresh_store
        special_name = "CCTV-1/HD 测试 (Backup)"
        source = MediaSource(
            id=generate_media_id("http://example.com/stream.m3u8", special_name),
            url="http://example.com/stream.m3u8",
            station_name=special_name,
            source_file="config/subscribe.txt"
        )
        await store.add_or_update_source(source)
        
        station = await store.get_station(special_name)
        assert station is not None
        assert station.name == special_name
    
    @pytest.mark.asyncio
    async def test_concurrent_reads_and_writes(self, fresh_store):
        """Test concurrent reads and writes."""
        store = fresh_store
        
        # Writer task
        async def writer():
            for i in range(50):
                source = MediaSource(
                    id=generate_media_id(f"http://example.com/stream{i}.m3u8", "CCTV1"),
                    url=f"http://example.com/stream{i}.m3u8",
                    station_name="CCTV1",
                    source_file="config/subscribe.txt"
                )
                await store.add_or_update_source(source)
        
        # Reader task
        async def reader():
            for _ in range(50):
                await store.get_station("CCTV1")
                await store.get_stats()
        
        # Run concurrently
        await asyncio.gather(writer(), reader())
        
        # Verify consistency
        station = await store.get_station("CCTV1")
        assert station.source_count == 50
    
    @pytest.mark.asyncio
    async def test_source_with_all_metrics(self, fresh_store):
        """Test source with all metrics populated."""
        store = fresh_store
        source = MediaSource(
            id=generate_media_id("http://example.com/stream.m3u8", "CCTV1"),
            url="http://example.com/stream.m3u8",
            station_name="CCTV1",
            source_file="config/subscribe.txt",
            metrics=MediaMetrics(
                delay_ms=50.0,
                content_type="application/x-mpegurl",
                status_code=200,
                speed_mbps=5.5,
                bandwidth_mbps=10.0,
                download_size_bytes=1024,
                download_time_ms=100.0,
                resolution="1920x1080",
                video_codec="h264",
                audio_codec="aac",
                fps=30.0,
                bitrate_kbps=5000.0,
                duration_seconds=3600.0,
                is_upscaled=False,
                ssim_score=0.95,
                actual_resolution="1920x1080",
                quality_score=0.85
            )
        )
        await store.add_or_update_source(source)
        
        retrieved = await store.get_source("CCTV1", "http://example.com/stream.m3u8")
        assert retrieved is not None
        assert retrieved.metrics.resolution == "1920x1080"
        assert retrieved.metrics.quality_score == 0.85

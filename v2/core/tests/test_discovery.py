"""
v2/core/tests/test_discovery.py

Tests for the DiscoveryWorker implementation.
Run with: python -m pytest core/tests/test_discovery.py -v
"""

import pytest
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock

import sys
sys.path.insert(0, '/Users/asxzy/src/iptv-api/v2')

from core.workers.discovery import DiscoveryWorker
from core.bus import EventBus
from core.events import MediaSourceDiscoveredEvent, StationDiscoveredEvent, DiscoveryErrorEvent
from core.types import MediaSource


class TestDiscoveryWorker:
    """Test suite for DiscoveryWorker."""
    
    @pytest.fixture
    def event_bus(self):
        """Create a fresh EventBus for each test."""
        return EventBus()
    
    @pytest.fixture
    def discovery_worker(self, event_bus):
        """Create a DiscoveryWorker for each test."""
        return DiscoveryWorker(event_bus, max_concurrent=2, max_redirect_depth=2, max_nesting_depth=2)
    
    @pytest.mark.asyncio
    async def test_worker_initialization(self, discovery_worker):
        """Test that the worker initializes with correct parameters."""
        assert discovery_worker.max_concurrent == 2
        assert discovery_worker.max_redirect_depth == 2
        assert discovery_worker.max_nesting_depth == 2
        assert discovery_worker.request_timeout == 10
        assert discovery_worker.session is None
    
    @pytest.mark.asyncio
    async def test_start_stop(self, discovery_worker):
        """Test starting and stopping the worker."""
        # Initially not started
        assert discovery_worker.session is None
        
        # Start worker
        await discovery_worker.start()
        assert discovery_worker.session is not None
        assert not discovery_worker.session.closed
        
        # Stop worker
        await discovery_worker.stop()
        assert discovery_worker.session is None or discovery_worker.session.closed
    
    @pytest.mark.asyncio
    async def test_process_plain_text_file(self, discovery_worker, event_bus):
        """Test processing a plain text subscription file."""
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("# Comment line\n")
            f.write("CCTV1,http://example.com/cctv1.m3u8\n")
            f.write("http://example.com/cctv2.m3u8\n")
            f.write("\n")  # Empty line
            f.write("CTV News,http://example.com/ctvnews.m3u8#extra\n")
            temp_path = f.name
        
        try:
            # Subscribe to events
            station_queue = event_bus.subscribe(StationDiscoveredEvent)
            media_queue = event_bus.subscribe(MediaSourceDiscoveredEvent)
            
            # Process the file
            await discovery_worker.discover_from_file(temp_path, "test_source")
            
            # Wait for events (with timeout)
            try:
                # Should get at least one station event
                station_event = await asyncio.wait_for(station_queue.get(), timeout=2.0)
                assert isinstance(station_event, StationDiscoveredEvent)
                assert "test_source" in station_event.station_name or "Station from" in station_event.station_name
                
                # Should get media events
                media_count = 0
                while not media_queue.empty() and media_count < 5:
                    media_event = await asyncio.wait_for(media_queue.get(), timeout=0.5)
                    assert isinstance(media_event, MediaSourceDiscoveredEvent)
                    assert media_event.media_source.station_name  # Should have a station name
                    media_count += 1
                
                # Should have discovered at least 3 media sources
                assert media_count >= 3
                
            except asyncio.TimeoutError:
                pytest.fail("Timeout waiting for discovery events")
                
        finally:
            # Clean up
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_process_m3u_file(self, discovery_worker, event_bus):
        """Test processing an M3U subscription file."""
        m3u_content = """#EXTM3U
#EXTINF:-1,tvg-id="CCTV1" tvg-name="CCTV1",CCTV1 News
http://example.com/cctv1.m3u8
#EXTINF:-1,tvg-id="CCTV2" tvg-name="CCTV2",CCTV2 Sports
http://example.com/cctv2.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1280000
http://example.com/high/stream.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=256000
http://example.com/low/stream.m3u8
"""
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False) as f:
            f.write(m3u_content)
            temp_path = f.name
        
        try:
            # Subscribe to events
            station_queue = event_bus.subscribe(StationDiscoveredEvent)
            media_queue = event_bus.subscribe(MediaSourceDiscoveredEvent)
            
            # Process the file
            await discovery_worker.discover_from_file(temp_path, "test_m3u")
            
            # Wait for events
            try:
                # Station event
                station_event = await asyncio.wait_for(station_queue.get(), timeout=2.0)
                assert isinstance(station_event, StationDiscoveredEvent)
                
                # Media events - should have at least 4 (2 regular + 2 stream variants)
                media_count = 0
                media_urls = []
                while not media_queue.empty() and media_count < 10:
                    try:
                        media_event = await asyncio.wait_for(media_queue.get(), timeout=0.5)
                        assert isinstance(media_event, MediaSourceDiscoveredEvent)
                        media_urls.append(media_event.media_source.url)
                        media_count += 1
                    except asyncio.TimeoutError:
                        break
                
                # Should have found the URLs from the M3U
                expected_urls = [
                    "http://example.com/cctv1.m3u8",
                    "http://example.com/cctv2.m3u8", 
                    "http://example.com/high/stream.m3u8",
                    "http://example.com/low/stream.m3u8"
                ]
                for expected in expected_urls:
                    assert any(expected in url for url in media_urls), f"Expected URL {expected} not found in {media_urls}"
                
            except asyncio.TimeoutError:
                pytest.fail("Timeout waiting for discovery events")
                
        finally:
            # Clean up
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_is_valid_url(self, discovery_worker):
        """Test URL validation helper."""
        # Valid URLs
        assert discovery_worker._is_valid_url("http://example.com/video.m3u8")
        assert discovery_worker._is_valid_url("https://stream.example.com/live/index.m3u8")
        assert discovery_worker._is_valid_url("http://192.168.1.100:8080/stream.ts")
        assert discovery_worker._is_valid_url("http://localhost:8080/stream.m3u8")
        assert discovery_worker._is_valid_url("http://localhost/stream.m3u8")
        
        # Invalid URLs
        assert not discovery_worker._is_valid_url("")
        assert not discovery_worker._is_valid_url("not-a-url")
        assert not discovery_worker._is_valid_url("http://")
        assert not discovery_worker._is_valid_url("https://")
        assert not discovery_worker._is_valid_url("http://.com")
        assert not discovery_worker._is_valid_url("http://..com")
        assert not discovery_worker._is_valid_url(None)
        assert not discovery_worker._is_valid_url(123)
    
    @pytest.mark.asyncio
    async def test_extract_station_name(self, discovery_worker):
        """Test station name extraction."""
        from m3u8 import M3U8
        
        # Test with playlist name
        playlist = M3U8()
        playlist.name = "My Channel"
        name = discovery_worker._extract_station_name(playlist, "source")
        assert name == "My Channel"
        
        # Test with segment title
        playlist = M3U8()
        from m3u8 import Segment
        playlist.segments = [Segment(uri="test.ts", title="News Segment")]
        name = discovery_worker._extract_station_name(playlist, "source")
        assert name == "News Segment"
        
        # Test fallback to source name
        playlist = M3U8()
        name = discovery_worker._extract_station_name(playlist, "My Source")
        assert name == "My Source"
    
    @pytest.mark.asyncio
    async def test_worker_without_session(self, discovery_worker):
        """Test that calling discover_from_file before start handles gracefully."""
        # Don't start the worker
        # This should either auto-start or handle gracefully
        # For now, we'll test that it doesn't crash horribly
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("http://example.com/test.m3u8\n")
            temp_path = f.name
        
        try:
            # This might fail due to no session, but shouldn't corrupt state
            await discovery_worker.discover_from_file(temp_path, "test")
            # If we get here without exception, that's good
        except Exception as e:
            # If it fails, it should be a reasonable error, not a crash
            assert "session" in str(e).lower() or "Connector" in str(e) or "ClientSession" in str(e)
        finally:
            os.unlink(temp_path)

"""
v2/core/tests/test_event_bus.py

Comprehensive tests for the EventBus implementation.
Run with: python -m pytest core/tests/test_event_bus.py -v
"""

import pytest
import asyncio
from datetime import datetime

from core.bus import EventBus
from core.events import (
    Event, MediaSourceDiscoveredEvent, URLValidatedEvent,
    ScanStartedEvent, DiscoveryErrorEvent
)


@pytest.fixture
def bus():
    """Create a fresh EventBus for each test."""
    return EventBus(max_queue_size=100)


class TestEventBus:
    """Test suite for EventBus."""
    
    @pytest.mark.asyncio
    async def test_basic_publish_subscribe(self, bus):
        """Test basic pub/sub functionality."""
        # Subscribe to MediaSourceDiscoveredEvent
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        
        # Create and publish an event
        event = MediaSourceDiscoveredEvent(
            media_source=None,  # type: ignore
            resolved_url="http://example.com/stream.m3u8"
        )
        
        await bus.publish(event)
        
        # Wait for event with timeout
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(received, MediaSourceDiscoveredEvent)
        assert received.resolved_url == "http://example.com/stream.m3u8"
    
    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_type(self, bus):
        """Test that multiple subscribers receive the same event."""
        # Create 3 subscribers
        queues = [bus.subscribe(MediaSourceDiscoveredEvent) for _ in range(3)]
        
        event = MediaSourceDiscoveredEvent(
            media_source=None,  # type: ignore
            resolved_url="http://example.com/stream.m3u8"
        )
        await bus.publish(event)
        
        # All 3 should receive the event
        for queue in queues:
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert received.resolved_url == "http://example.com/stream.m3u8"
    
    @pytest.mark.asyncio
    async def test_event_ordering(self, bus):
        """Test that events are received in publication order."""
        queue = bus.subscribe(URLValidatedEvent)
        
        # Publish 10 events rapidly
        for i in range(10):
            event = URLValidatedEvent(
                media_source=None,  # type: ignore
                validation_time_ms=float(i)
            )
            await bus.publish(event)
        
        # Verify order
        for i in range(10):
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert received.validation_time_ms == float(i)
    
    @pytest.mark.asyncio
    async def test_different_event_types_isolated(self, bus):
        """Test that different event types don't interfere."""
        queue1 = bus.subscribe(MediaSourceDiscoveredEvent)
        queue2 = bus.subscribe(URLValidatedEvent)
        
        # Publish to each type
        await bus.publish(MediaSourceDiscoveredEvent())
        await bus.publish(URLValidatedEvent())
        
        # Verify isolation
        received1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        received2 = await asyncio.wait_for(queue2.get(), timeout=1.0)
        
        assert isinstance(received1, MediaSourceDiscoveredEvent)
        assert isinstance(received2, URLValidatedEvent)
    
    @pytest.mark.asyncio
    async def test_subscribe_all(self, bus):
        """Test catch-all subscription."""
        queue = bus.subscribe_all()
        
        # Publish different event types
        await bus.publish(MediaSourceDiscoveredEvent())
        await bus.publish(URLValidatedEvent())
        
        # Catch-all should receive both
        received1 = await asyncio.wait_for(queue.get(), timeout=1.0)
        received2 = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert isinstance(received1, MediaSourceDiscoveredEvent)
        assert isinstance(received2, URLValidatedEvent)
    
    @pytest.mark.asyncio
    async def test_queue_backpressure(self, bus):
        """Test that full queues drop events gracefully."""
        # Queue size of 2
        bus = EventBus(max_queue_size=2)
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        
        # Don't consume from queue - fill it up
        for i in range(5):
            await bus.publish(MediaSourceDiscoveredEvent())
        
        # Metrics should show dropped events
        metrics = bus.get_metrics()
        assert metrics['dropped'] > 0
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        """Test unsubscribing from events."""
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        
        # Publish and receive
        await bus.publish(MediaSourceDiscoveredEvent())
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received is not None
        
        # Unsubscribe
        bus.unsubscribe(MediaSourceDiscoveredEvent, queue)
        
        # Publish again - should not receive
        await bus.publish(MediaSourceDiscoveredEvent())
        
        # Queue should be empty (or have old event)
        assert queue.qsize() == 0
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, bus):
        """Test graceful shutdown with pending events."""
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        
        # Publish some events
        for _ in range(3):
            await bus.publish(MediaSourceDiscoveredEvent())
        
        # Shutdown
        await bus.shutdown(timeout=2.0)
        
        # Consume remaining events
        received_count = 0
        while not queue.empty():
            await queue.get()
            received_count += 1
        
        assert received_count == 3
    
    @pytest.mark.asyncio
    async def test_metrics_tracking(self, bus):
        """Test that metrics are tracked correctly."""
        # Initially empty
        metrics = bus.get_metrics()
        assert metrics['published'] == 0
        assert metrics['delivered'] == 0
        
        # Publish events
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        for _ in range(5):
            await bus.publish(MediaSourceDiscoveredEvent())
        
        # Check metrics
        metrics = bus.get_metrics()
        assert metrics['published'] == 5
        assert metrics['delivered'] == 5
    
    @pytest.mark.asyncio
    async def test_event_id_uniqueness(self, bus):
        """Test that each event gets a unique ID."""
        queue = bus.subscribe(MediaSourceDiscoveredEvent)
        
        # Publish 2 events
        event1 = MediaSourceDiscoveredEvent()
        event2 = MediaSourceDiscoveredEvent()
        
        await bus.publish(event1)
        await bus.publish(event2)
        
        # Get events
        received1 = await asyncio.wait_for(queue.get(), timeout=1.0)
        received2 = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        # IDs should be different
        assert received1.event_id != received2.event_id
        
        # IDs should be UUID-like
        assert len(received1.event_id) == 36
        assert len(received2.event_id) == 36
    
    @pytest.mark.asyncio
    async def test_concurrent_publishers(self, bus):
        """Test multiple publishers sending concurrently."""
        queue = bus.subscribe_all()
        
        # Multiple publishers
        async def publisher(n):
            for i in range(10):
                await bus.publish(MediaSourceDiscoveredEvent())
        
        # Run 5 publishers concurrently
        await asyncio.gather(*[publisher(i) for i in range(5)])
        
        # Should have 50 events
        count = 0
        while not queue.empty() and count < 50:
            await queue.get()
            count += 1
        
        assert count == 50


class TestEventBusEdgeCases:
    """Edge case tests for EventBus."""
    
    @pytest.mark.asyncio
    async def test_publish_after_shutdown(self, bus):
        """Test that publishing after shutdown is handled gracefully."""
        await bus.shutdown()
        
        # Should not raise
        event = MediaSourceDiscoveredEvent()
        await bus.publish(event)  # Should log warning, not raise
    
    @pytest.mark.asyncio
    async def test_empty_event_publication(self, bus):
        """Test publishing events with minimal data."""
        queue = bus.subscribe(Event)
        
        # Publish minimal event
        event = Event()
        await bus.publish(event)
        
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received is not None
        assert received.event_id is not None
        assert received.timestamp is not None

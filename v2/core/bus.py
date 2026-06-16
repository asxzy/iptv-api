"""
v2/core/bus.py

Async event bus implementation using asyncio queues.
Supports typed events, multiple subscribers, and graceful shutdown.
"""

import asyncio
import logging
from typing import Dict, List, Callable, Type, Any, Optional
from collections import defaultdict
from .events import Event

logger = logging.getLogger(__name__)


class EventBus:
    """
    Async event bus with typed pub/sub support.
    
    Each event type has its own queue to prevent one slow subscriber
    from blocking other event types.
    """
    
    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: Dict[Type[Event], List[asyncio.Queue]] = defaultdict(list)
        self._all_subscribers: List[asyncio.Queue] = []
        self._max_queue_size = max_queue_size
        self._running = True
        self._metrics = {
            'published': 0,
            'delivered': 0,
            'dropped': 0,
        }
    
    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        if not self._running:
            logger.warning("Event bus is shut down, dropping event %s", event.event_id)
            return
        
        self._metrics['published'] += 1
        
        # Get all subscriber queues for this event type
        event_type = type(event)
        queues = self._subscribers.get(event_type, [])
        
        # Also notify catch-all subscribers
        all_queues = self._all_subscribers
        
        # Deliver to all queues
        for queue in list(queues) + list(all_queues):
            try:
                if queue.qsize() < self._max_queue_size:
                    await queue.put(event)
                    self._metrics['delivered'] += 1
                else:
                    self._metrics['dropped'] += 1
                    logger.warning("Queue full, dropping event %s", event.event_id)
            except Exception:
                self._metrics['dropped'] += 1
    
    def subscribe(self, event_type: Type[Event]) -> asyncio.Queue:
        """Subscribe to a specific event type. Returns a queue to consume from."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers[event_type].append(queue)
        return queue
    
    def subscribe_all(self) -> asyncio.Queue:
        """Subscribe to all events. Returns a queue to consume from."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._all_subscribers.append(queue)
        return queue
    
    def unsubscribe(self, event_type: Type[Event], queue: asyncio.Queue) -> None:
        """Unsubscribe from an event type."""
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(queue)
            except ValueError:
                pass
    
    def get_metrics(self) -> Dict[str, int]:
        """Return current bus metrics."""
        return dict(self._metrics)
    
    async def shutdown(self, timeout: float = 5.0) -> None:
        """Gracefully shutdown the event bus."""
        self._running = False
        
        # Wait for existing events to be processed
        if self._subscribers:
            queues = []
            for q_list in self._subscribers.values():
                queues.extend(q_list)
            queues.extend(self._all_subscribers)
            
            # Wait for all queues to drain or timeout
            start = asyncio.get_event_loop().time()
            while any(not q.empty() for q in queues):
                if asyncio.get_event_loop().time() - start > timeout:
                    break
                await asyncio.sleep(0.1)

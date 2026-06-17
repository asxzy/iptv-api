"""
v2/conftest.py

Pytest configuration and shared fixtures for v2 tests."""

import pytest
from core.bus import EventBus
from core.store import GlobalDataStore


@pytest.fixture
async def event_bus():
    """Provide a fresh EventBus instance."""
    bus = EventBus(max_queue_size=100)
    yield bus
    await bus.shutdown()


@pytest.fixture
def fresh_store():
    """Provide a fresh GlobalDataStore instance."""
    GlobalDataStore.reset_instance()
    return GlobalDataStore()
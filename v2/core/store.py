"""
v2/core/store.py

Thread-safe global data store with atomic updates.
Supports in-memory storage with copy-on-read for consistency.
"""

import asyncio
import logging
from typing import Dict, Optional
from copy import deepcopy

from .types import Station, MediaSource, MediaStatus

logger = logging.getLogger(__name__)


class GlobalDataStore:
    """
    Thread-safe singleton data store for all scan results.
    Uses fine-grained locking per station for concurrency.
    """
    
    _instance = None
    _lock = asyncio.Lock()
    
    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_store()
        return cls._instance
    
    def _init_store(self):
        self._stations: dict[str, Station] = {}
        self._station_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._total_sources = 0
        self._metrics = {
            'sources_added': 0,
            'sources_updated': 0,
            'stations_created': 0,
        }
    
    async def get_or_create_station(self, name: str) -> Station:
        """Get existing station or create new one with lock."""
        if name not in self._station_locks:
            async with self._global_lock:
                if name not in self._station_locks:
                    self._station_locks[name] = asyncio.Lock()
        
        async with self._station_locks[name]:
            if name not in self._stations:
                self._stations[name] = Station(name=name)
                self._metrics['stations_created'] += 1
            return self._stations[name]
    
    async def add_or_update_source(self, media_source):
        """Add or update a media source in its station."""
        station = await self.get_or_create_station(media_source.station_name)

        # get_or_create_station already holds the station lock
        is_new = media_source.url not in station.sources
        station.sources[media_source.url] = media_source

        if is_new:
            async with self._global_lock:
                self._total_sources += 1
            self._metrics['sources_added'] += 1
        else:
            self._metrics['sources_updated'] += 1
    
    async def get_station(self, name: str) -> Optional[Station]:
        """Get a station by name (returns a copy for thread safety)."""
        async with self._global_lock:
            station = self._stations.get(name)
            if station is None:
                return None
            return deepcopy(station)
    
    async def get_source(self, station_name: str, url: str):
        """Get a specific media source."""
        station = await self.get_station(station_name)
        if station:
            return station.sources.get(url)
        return None
    
    async def get_all_stations(self):
        """Get all stations (shallow copy for safety)."""
        return dict(self._stations)
    
    async def get_stations_by_status(self, status: MediaStatus):
        """Get all stations that have at least one source with given status."""
        result = []
        for station in self._stations.values():
            if station and any(s.status == status for s in station.sources.values()):
                result.append(station)
        return result
    
    async def get_stats(self):
        """Get store statistics."""
        return {
            'total_stations': len(self._stations),
            'total_sources': self._total_sources,
            **self._metrics,
        }
    
    async def snapshot(self):
        """Create a consistent snapshot of all stations."""
        async with self._global_lock:
            return deepcopy(dict(self._stations))
    
    async def clear(self):
        """Clear all data from the store."""
        async with self._global_lock:
            self._stations.clear()
            self._station_locks.clear()
            self._total_sources = 0
            self._metrics.clear()
    
    def __len__(self):
        return len(self._stations)

"""
v2/core/workers/discovery.py

Discovery worker for resolving M3U sources and emitting media source events.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
import aiohttp
import m3u8

from ..bus import EventBus
from ..events import (
    MediaSourceDiscoveredEvent,
    StationDiscoveredEvent,
    DiscoveryErrorEvent
)
from ..types import MediaSource, generate_media_id

logger = logging.getLogger(__name__)


class DiscoveryWorker:
    """
    Worker that discovers media sources from subscription files.
    Handles M3U parsing, redirect following, and nested playlist resolution.
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        max_concurrent: int = 10,
        max_redirect_depth: int = 5,
        max_nesting_depth: int = 3,
        request_timeout: int = 10
    ):
        self.event_bus = event_bus
        self.max_concurrent = max_concurrent
        self.max_redirect_depth = max_redirect_depth
        self.max_nesting_depth = max_nesting_depth
        self.request_timeout = request_timeout
        self.session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._visited_urls: Set[str] = set()
        
    async def start(self):
        """Start the discovery worker."""
        connector = aiohttp.TCPConnector(limit=0)  # No limit, we use semaphore
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.request_timeout)
        )
        logger.info("Discovery worker started")
    
    async def stop(self):
        """Stop the discovery worker."""
        if self.session:
            await self.session.close()
        logger.info("Discovery worker stopped")
    
    async def discover_from_file(self, file_path: str, source_name: str = "unknown"):
        """
        Discover media sources from a subscription file.
        
        Args:
            file_path: Path to the subscription file
            source_name: Name/identifier of the source (for logging)
        """
        logger.info(f"Starting discovery from {source_name}: {file_path}")
        
        try:
            # Read the file
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Determine file type and parse
            if content.strip().startswith('#EXTM3U'):
                await self._process_m3u(content, source_name, file_path)
            else:
                await self._process_plain_text(content, source_name, file_path)
                
        except Exception as e:
            logger.error(f"Failed to read source file {file_path}: {e}")
            await self.event_bus.publish(
                DiscoveryErrorEvent(
                    source_file=file_path,
                    error_message=str(e),
                    is_fatal=True
                ).with_trace("discovery")
            )
    
    async def _process_m3u(self, content: str, source_name: str, file_path: str):
        """Process an M3U/M3U8 playlist."""
        try:
            playlist = m3u8.loads(content)
            
            # Emit station discovered event
            station_name = self._extract_station_name(playlist, source_name)
            await self.event_bus.publish(
                StationDiscoveredEvent(
                    station_name=station_name,
                    source_file=file_path,
                    raw_data={"type": "m3u", "version": playlist.version}
                ).with_trace("discovery")
            )
            
            # Process variant streams (nested M3Us)
            if playlist.playlists:
                tasks = []
                for playlist_item in playlist.playlists:
                    if playlist_item.uri:
                        absolute_url = urljoin(file_path, playlist_item.uri)
                        tasks.append(self._resolve_nested_m3u(
                            absolute_url, source_name, file_path, nesting_depth=1
                        ))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process media segments (for VOD/live streams)
            if playlist.segments:
                await self._process_segments(playlist.segments, station_name, source_name, file_path)
                
        except Exception as e:
            logger.error(f"Failed to parse M3U file {file_path}: {e}")
            await self.event_bus.publish(
                DiscoveryErrorEvent(
                    source_file=file_path,
                    error_message=str(e),
                    is_fatal=False
                ).with_trace("discovery")
            )
    
    async def _process_plain_text(self, content: str, source_name: str, file_path: str):
        """Process a plain text file (name,url format or URL-per-line)."""
        lines = content.strip().split('\n')
        station_name = f"Station from {source_name}"
        
        # Emit station discovered event
        await self.event_bus.publish(
            StationDiscoveredEvent(
                station_name=station_name,
                source_file=file_path,
                raw_data={"type": "plain_text", "line_count": len(lines)}
            ).with_trace("discovery")
        )
        
        # Process each line
        tasks = []
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Parse name,url format
            if ',' in line and not re.match(r'^https?://', line):
                parts = line.split(',', 1)
                if len(parts) == 2:
                    name, url = parts[0].strip(), parts[1].strip()
                    # For plain text, we treat each URL as its own station
                    station_name = name or f"Source {line_num}"
                else:
                    url = line
            else:
                url = line
            
            if url and self._is_valid_url(url):
                absolute_url = urljoin(file_path, url)
                tasks.append(self._process_media_url(
                    absolute_url, station_name, source_name, file_path
                ))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _resolve_nested_m3u(
        self, 
        url: str, 
        source_name: str, 
        original_file: str,
        nesting_depth: int = 0
    ):
        """Resolve a nested M3U playlist with recursion limit."""
        if nesting_depth > self.max_nesting_depth:
            logger.warning(f"Max nesting depth exceeded for {url}")
            await self.event_bus.publish(
                DiscoveryErrorEvent(
                    source_file=original_file,
                    error_message=f"Max nesting depth exceeded: {url}",
                    url=url
                ).with_trace("discovery")
            )
            return
        
        if url in self._visited_urls:
            # Avoid cycles
            return
        
        self._visited_urls.add(url)
        
        try:
            async with self._semaphore:
                async with self.session.get(url) as response:
                    if response.status != 200:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"HTTP {response.status}"
                        )
                    
                    content = await response.text()
                    
                    # Check if it's an M3U file
                    if content.strip().startswith('#EXTM3U'):
                        await self._process_m3u(content, source_name, url)
                        
                        # Also process any nested playlists within this one
                        playlist = m3u8.loads(content)
                        if playlist.playlists:
                            tasks = []
                            for playlist_item in playlist.playlists:
                                if playlist_item.uri:
                                    nested_url = urljoin(url, playlist_item.uri)
                                    tasks.append(self._resolve_nested_m3u(
                                        nested_url, source_name, original_file, nesting_depth + 1
                                    ))
                            if tasks:
                                await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        # Not an M3U, treat as potential media URL
                        await self._process_media_url(url, "Unknown Station", source_name, original_file)
                        
        except Exception as e:
            logger.debug(f"Failed to fetch nested M3U {url}: {e}")
            # Don't treat as error if it's just not an M3U - might be a media URL
            if "Max nesting depth" not in str(e):
                await self._process_media_url(url, "Unknown Station", source_name, original_file)
    
    async def _process_segments(
        self, 
        segments: List[m3u8.Segment], 
        station_name: str,
        source_name: str,
        file_path: str
    ):
        """Process media segments in an M3U playlist."""
        # Process each segment as a potential media URL
        tasks = []
        for segment in segments:
            if segment.uri:
                absolute_url = urljoin(file_path, segment.uri)
                tasks.append(self._process_media_url(
                    absolute_url, station_name, source_name, file_path
                ))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _process_media_url(
        self, 
        url: str, 
        station_name: str, 
        source_name: str,
        source_file: str
    ):
        """Process a potential media URL - validate and emit discovery event."""
        if not self._is_valid_url(url):
            return
        
        # Generate unique ID for this media source
        media_id = generate_media_id(url, station_name)
        
        # Create media source object
        media_source = MediaSource(
            id=media_id,
            url=url,
            station_name=station_name,
            source_file=source_file
        )
        
        # Emit discovery event
        await self.event_bus.publish(
            MediaSourceDiscoveredEvent(
                media_source=media_source,
                resolved_url=url,  # Will be updated after redirect following
                redirect_chain=[],
                nesting_depth=0
            ).with_trace("discovery")
        )
        
        logger.debug(f"Discovered media source: {station_name} -> {url}")
    
    def _extract_station_name(self, playlist: m3u8.M3U8, source_name: str) -> str:
        """Extract a station name from an M3U playlist."""
        # Try to get name from playlist attributes
        if hasattr(playlist, 'name') and playlist.name:
            return playlist.name
        
        # Try to get from first segment if it has a title
        if playlist.segments and len(playlist.segments) > 0:
            first_segment = playlist.segments[0]
            if hasattr(first_segment, 'title') and first_segment.title:
                return first_segment.title
        
        # Fallback to source name or "Unknown Station"
        return source_name or "Unknown Station"
    
    def _is_valid_url(self, url: str) -> bool:
        """Basic URL validation."""
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        if not url:
            return False
        
        try:
            result = urlparse(url)
            # Must have scheme and netloc
            if not result.scheme or not result.netloc:
                return False
            # Reject URLs where domain starts or ends with dot
            if result.netloc.startswith('.') or result.netloc.endswith('.'):
                return False
            # Reject empty domain or just dots
            if not result.netloc or all(c == '.' for c in result.netloc):
                return False
            # Must have a valid scheme
            if result.scheme not in ('http', 'https'):
                return False
            return True
        except Exception:
            return False

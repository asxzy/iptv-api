"""
v2/core/workers/validation.py

Validation worker that filters discovered media sources against
whitelist/blacklist rules, checks connectivity, and validates content types.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Pattern
from urllib.parse import urlparse
import aiohttp

from ..bus import EventBus
from ..events import (
    URLValidatedEvent,
    URLRejectedEvent,
    ValidationErrorEvent,
)
from ..types import MediaSource, MediaStatus, MediaMetrics, generate_media_id
from ..store import GlobalDataStore

logger = logging.getLogger(__name__)

VALID_MEDIA_CONTENT_TYPES = {
    'video/mp4',
    'video/mpeg',
    'video/quicktime',
    'video/x-msvideo',
    'video/x-matroska',
    'video/webm',
    'video/ogg',
    'video/3gpp',
    'video/mp2t',
    'application/x-mpegurl',
    'application/vnd.apple.mpegurl',
    'audio/mpeg',
    'audio/mp4',
    'audio/ogg',
    'audio/webm',
    'application/dash+xml',
    'application/vnd.ms-sstr+xml',
    'video/ts',
    'video/x-mpegts',
    'application/octet-stream',
}


class ValidationWorker:
    """
    Worker that validates discovered media URLs through a pipeline:
    1. Whitelist check (bypasses all other checks)
    2. Blacklist check (keyword + regex)
    3. Connectivity check (HEAD request)
    4. Content-Type validation

    Emits URLValidatedEvent on success, URLRejectedEvent on failure,
    ValidationErrorEvent on unexpected errors.
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: GlobalDataStore,
        timeout: float = 5.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        max_concurrent: int = 10,
        whitelist_keywords: Optional[List[str]] = None,
        whitelist_patterns: Optional[List[str]] = None,
        blacklist_keywords: Optional[List[str]] = None,
        blacklist_patterns: Optional[List[str]] = None,
        require_content_type: bool = True,
    ):
        self.event_bus = event_bus
        self.store = store
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_concurrent = max_concurrent
        self.require_content_type = require_content_type

        self._whitelist_keywords: List[str] = whitelist_keywords or []
        self._whitelist_patterns: List[Pattern] = [
            re.compile(p) for p in (whitelist_patterns or [])
        ]
        self._blacklist_keywords: List[str] = blacklist_keywords or []
        self._blacklist_patterns: List[Pattern] = [
            re.compile(p) for p in (blacklist_patterns or [])
        ]

        self.session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._metrics = {
            'checked': 0,
            'validated': 0,
            'rejected': 0,
            'errors': 0,
            'whitelist_passed': 0,
            'blacklist_rejected': 0,
            'connectivity_failed': 0,
            'content_type_rejected': 0,
        }

    async def start(self):
        """Start the validation worker and create HTTP session."""
        connector = aiohttp.TCPConnector(limit=0)
        timeout_obj = aiohttp.ClientTimeout(total=self.timeout, connect=self.timeout)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout_obj,
        )
        self._running = True
        logger.info("Validation worker started")

    async def stop(self):
        """Stop the validation worker and close HTTP session."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("Validation worker stopped")

    async def process_queue(self, input_queue: asyncio.Queue):
        """
        Main loop: consume MediaSourceDiscoveredEvent from the queue
        and validate each discovered source.

        Args:
            input_queue: Queue containing MediaSourceDiscoveredEvent objects.
        """
        self._task = asyncio.current_task()
        while self._running:
            try:
                event = await asyncio.wait_for(input_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                media_source = event.media_source
                async with self._semaphore:
                    await self.validate(media_source, event.trace_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Validation worker error: {e}")

    def _find_matching_keyword(self, url: str, keywords: List[str]) -> Optional[str]:
        """Check if a URL contains any of the given keywords."""
        url_lower = url.lower()
        for kw in keywords:
            if kw.lower() in url_lower:
                return kw
        return None

    def _find_matching_pattern(self, url: str, patterns: List[Pattern]) -> Optional[Pattern]:
        """Check if a URL matches any of the given regex patterns."""
        for pattern in patterns:
            if pattern.search(url):
                return pattern
        return None

    async def _check_whitelist(self, url: str) -> bool:
        """Check if the URL is in the whitelist (keyword or regex match)."""
        if self._find_matching_keyword(url, self._whitelist_keywords):
            self._metrics['whitelist_passed'] += 1
            return True
        if self._find_matching_pattern(url, self._whitelist_patterns):
            self._metrics['whitelist_passed'] += 1
            return True
        return False

    async def _check_blacklist(self, url: str) -> Optional[str]:
        """Check if the URL matches a blacklist rule. Returns the reason if rejected."""
        kw = self._find_matching_keyword(url, self._blacklist_keywords)
        if kw:
            return f"Blacklisted keyword: {kw}"

        pattern = self._find_matching_pattern(url, self._blacklist_patterns)
        if pattern:
            return f"Blacklisted pattern: {pattern.pattern}"

        return None

    async def _check_connectivity(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple[bool, int, Dict[str, str]]:
        """
        Perform a HEAD request to verify connectivity.
        Returns (is_available, status_code, response_headers).
        """
        request_headers = dict(headers or {})
        if 'User-Agent' not in request_headers:
            request_headers['User-Agent'] = (
                'Mozilla/5.0 (compatible; IPTV-API/2.0; +https://github.com/Guovin/iptv-api)'
            )

        for attempt in range(self.max_retries + 1):
            try:
                async with self.session.head(
                    url,
                    headers=request_headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(
                        total=self.timeout,
                        connect=self.timeout,
                    ),
                ) as response:
                    if response.status >= 500:
                        if attempt < self.max_retries:
                            await asyncio.sleep(self.retry_delay)
                            continue
                    status = response.status
                    resp_headers = dict(response.headers)
                    is_ok = 200 <= status < 300
                    return is_ok, status, resp_headers

            except (asyncio.TimeoutError, aiohttp.ClientError,
                    aiohttp.ClientConnectorError) as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                    continue
                return False, 0, {'_error': str(e)}

        return False, 0, {'_error': 'Max retries exceeded'}

    async def _validate_content_type(
        self,
        url: str,
        response_headers: Dict[str, str],
    ) -> Optional[str]:
        """
        Validate the Content-Type header.
        Returns None if valid, or a rejection reason string if invalid.
        """
        content_type = response_headers.get('Content-Type', '').lower().split(';')[0].strip()

        if not content_type:
            if self.require_content_type:
                return "Missing Content-Type header"
            return None

        if content_type in VALID_MEDIA_CONTENT_TYPES:
            return None

        if content_type.startswith('video/'):
            return None

        if content_type.startswith('audio/'):
            return None

        if content_type == 'application/octet-stream':
            return None

        return f"Invalid Content-Type: {content_type}"

    async def validate(self, media_source: MediaSource, trace_id: Optional[str] = None) -> bool:
        """
        Run the full validation pipeline on a media source.
        Returns True if validated, False if rejected or error.

        Pipeline:
        1. Whitelist check — if whitelisted, accept immediately
        2. Blacklist check — if blacklisted, reject
        3. Connectivity check — HEAD request
        4. Content-Type validation
        """
        self._metrics['checked'] += 1
        url = media_source.url
        trace = trace_id or "validation"

        try:
            # Step 1: Whitelist check
            if await self._check_whitelist(url):
                validated = media_source.with_status(MediaStatus.VALIDATED)
                await self.store.add_or_update_source(validated)
                await self.event_bus.publish(
                    URLValidatedEvent(
                        media_source=validated,
                        validation_time_ms=0.0,
                    ).with_trace(trace)
                )
                self._metrics['validated'] += 1
                logger.debug(f"Whitelist passed: {url}")
                return True

            # Step 2: Blacklist check
            blacklist_reason = await self._check_blacklist(url)
            if blacklist_reason:
                rejected = media_source.with_status(MediaStatus.BLACKLISTED)
                await self.store.add_or_update_source(rejected)
                await self.event_bus.publish(
                    URLRejectedEvent(
                        url=url,
                        reason=blacklist_reason,
                        is_blacklist=True,
                    ).with_trace(trace)
                )
                self._metrics['blacklist_rejected'] += 1
                self._metrics['rejected'] += 1
                logger.debug(f"Blacklist rejected: {url} - {blacklist_reason}")
                return False

            # Step 3: Connectivity check
            is_available, status_code, resp_headers = await self._check_connectivity(
                url, media_source.headers or None
            )

            if not is_available:
                rejected = media_source.with_status(MediaStatus.FAILED)
                error_msg = resp_headers.get('_error', f'HTTP {status_code}')
                await self.store.add_or_update_source(rejected)
                await self.event_bus.publish(
                    URLRejectedEvent(
                        url=url,
                        reason=f"Connectivity check failed: {error_msg}",
                        is_blacklist=False,
                    ).with_trace(trace)
                )
                self._metrics['connectivity_failed'] += 1
                self._metrics['rejected'] += 1
                logger.debug(f"Connectivity failed: {url} - {error_msg}")
                return False

            # Step 4: Content-Type validation
            ct_result = await self._validate_content_type(url, resp_headers)
            if ct_result is not None:
                rejected = media_source.with_status(MediaStatus.FAILED)
                await self.store.add_or_update_source(rejected)
                await self.event_bus.publish(
                    URLRejectedEvent(
                        url=url,
                        reason=ct_result,
                        is_blacklist=False,
                    ).with_trace(trace)
                )
                self._metrics['content_type_rejected'] += 1
                self._metrics['rejected'] += 1
                logger.debug(f"Content-Type rejected: {url} - {ct_result}")
                return False

            # All checks passed
            validated = media_source.with_status(MediaStatus.VALIDATED)
            await self.store.add_or_update_source(validated)
            await self.event_bus.publish(
                URLValidatedEvent(
                    media_source=validated,
                    validation_time_ms=0.0,
                ).with_trace(trace)
            )
            self._metrics['validated'] += 1
            logger.debug(f"Validation passed: {url}")
            return True

        except Exception as e:
            self._metrics['errors'] += 1
            logger.error(f"Validation error for {url}: {e}")
            await self.event_bus.publish(
                ValidationErrorEvent(
                    url=url,
                    error_message=str(e),
                ).with_trace(trace)
            )
            return False

    def get_metrics(self) -> Dict[str, int]:
        """Return current validation metrics."""
        return dict(self._metrics)

    def add_whitelist_keyword(self, keyword: str):
        """Add a keyword to the whitelist."""
        if keyword not in self._whitelist_keywords:
            self._whitelist_keywords.append(keyword)

    def add_whitelist_pattern(self, pattern: str):
        """Add a regex pattern to the whitelist."""
        compiled = re.compile(pattern)
        self._whitelist_patterns.append(compiled)

    def add_blacklist_keyword(self, keyword: str):
        """Add a keyword to the blacklist."""
        if keyword not in self._blacklist_keywords:
            self._blacklist_keywords.append(keyword)

    def add_blacklist_pattern(self, pattern: str):
        """Add a regex pattern to the blacklist."""
        compiled = re.compile(pattern)
        self._blacklist_patterns.append(compiled)

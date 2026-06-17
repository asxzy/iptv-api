"""
v2/core/workers/proxy.py

ProxyWorker — handles proxy requests with whitelist/blacklist checking
and an optional upscaler interface for future quality-improvement algorithms.

Designed to be used by the web service endpoint (/proxy) for ad-filtering
and access control.
"""

import asyncio
import logging
from typing import (
    Callable,
    Dict,
    Optional,
    Set,
    Tuple,
    Awaitable,
)
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

from ..store import GlobalDataStore
from ..bus import EventBus
from ..events import Event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProxyAccessEvent(Event):
    """Emitted for each proxy request (allowed or blocked)."""
    url: str = ""
    allowed: bool = False
    reason: str = ""
    status_code: int = 0


UpscalerFunc = Callable[
    [str, Dict[str, str]],
    Awaitable[Tuple[str, Dict[str, str]]],
]


class ProxyWorker:
    """
    Proxy worker that inspects URLs against whitelist/blacklist sets
    stored in the Global Data Store, optionally invokes an upscaler,
    and forwards allowed requests to the upstream.

    Default behavior: URLs not in either list are allowed (default_allow=True).
    Whitelist entries always take precedence over blacklist entries.
    """

    def __init__(
        self,
        store: GlobalDataStore,
        event_bus: Optional[EventBus] = None,
        timeout: float = 10.0,
        max_redirects: int = 5,
        default_allow: bool = True,
        upscaler: Optional[UpscalerFunc] = None,
    ):
        self.store = store
        self.event_bus = event_bus
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.default_allow = default_allow
        self.upscaler = upscaler

        self._session: Optional[aiohttp.ClientSession] = None
        self._metrics = {
            'total_requests': 0,
            'allowed': 0,
            'blocked': 0,
            'errors': 0,
            'timeouts': 0,
        }

    async def start(self):
        """Create the HTTP client session."""
        connector = aiohttp.TCPConnector(limit=0)
        timeout_obj = aiohttp.ClientTimeout(
            total=self.timeout,
            connect=self.timeout,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout_obj,
        )
        logger.info("Proxy worker started")

    async def stop(self):
        """Close the HTTP client session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        logger.info("Proxy worker stopped")

    async def is_whitelisted(self, url: str) -> bool:
        """Check if a URL is in the whitelist set (substring match)."""
        whitelist = await self.store.get_whitelist()
        for entry in whitelist:
            if entry.lower() in url.lower():
                return True
        return False

    async def is_blacklisted(self, url: str) -> bool:
        """Check if a URL is in the blacklist set (substring match)."""
        blacklist = await self.store.get_blacklist()
        for entry in blacklist:
            if entry.lower() in url.lower():
                return True
        return False

    async def check_access(self, url: str) -> Tuple[bool, str]:
        """
        Check whether a URL is allowed or blocked.

        Returns:
            (allowed: bool, reason: str)
        """
        whitelisted = await self.is_whitelisted(url)
        if whitelisted:
            return True, "whitelist match"

        blacklisted = await self.is_blacklisted(url)
        if blacklisted:
            return False, "blacklist match"

        if self.default_allow:
            return True, "default allow"
        return False, "default deny"

    async def _apply_upscaler(
        self,
        url: str,
        headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, str]]:
        """Invoke the optional upscaler to modify the request."""
        if self.upscaler is not None:
            try:
                url, headers = await self.upscaler(url, headers)
                logger.debug(f"Upscaler modified URL: {url}")
            except Exception as e:
                logger.error(f"Upscaler error: {e}")
        return url, headers

    async def handle_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """
        Handle a proxy request: check access, optionally upscale,
        forward to upstream, return (status_code, response_headers, body).

        Returns:
            Tuple of (status_code, response_headers_dict, response_body_bytes)

        The caller (web endpoint) is responsible for converting this into
        an HTTP response.
        """
        self._metrics['total_requests'] += 1
        request_headers = dict(headers or {})

        # Step 1: Access control
        allowed, reason = await self.check_access(url)
        status_code = 0

        if not allowed:
            self._metrics['blocked'] += 1
            logger.info(f"Blocked: {url} ({reason})")
            await self._emit_access_event(url, allowed=False, reason=reason)
            return 403, {"X-Proxy-Reason": reason}, b"Blocked by proxy policy"

        # Step 2: Optional upscaler
        try:
            url, request_headers = await self._apply_upscaler(url, request_headers)
        except Exception as e:
            logger.error(f"Upscaler failed: {e}")
            self._metrics['errors'] += 1
            return 502, {"X-Proxy-Error": str(e)}, b"Upscaler error"

        # Step 3: Forward request
        try:
            if 'User-Agent' not in request_headers:
                request_headers['User-Agent'] = (
                    'Mozilla/5.0 (compatible; IPTV-API/2.0 Proxy; '
                    '+https://github.com/Guovin/iptv-api)'
                )

            async with self._session.get(
                url,
                headers=request_headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(
                    total=self.timeout,
                    connect=self.timeout,
                ),
            ) as response:
                status = response.status
                resp_headers = dict(response.headers)
                body = await response.read()

                self._metrics['allowed'] += 1
                logger.info(f"Proxied: {url} -> {status}")

                await self._emit_access_event(
                    url, allowed=True, reason=reason, status_code=status,
                )
                return status, resp_headers, body

        except asyncio.TimeoutError:
            self._metrics['timeouts'] += 1
            self._metrics['errors'] += 1
            logger.warning(f"Timeout proxying: {url}")
            await self._emit_access_event(url, allowed=False, reason="timeout")
            return 504, {"X-Proxy-Error": "timeout"}, b"Upstream timeout"

        except aiohttp.ClientError as e:
            self._metrics['errors'] += 1
            logger.warning(f"Client error proxying {url}: {e}")
            await self._emit_access_event(
                url, allowed=False, reason=f"client error: {e}",
            )
            return 502, {"X-Proxy-Error": str(e)}, b"Upstream fetch error"

        except Exception as e:
            self._metrics['errors'] += 1
            logger.error(f"Unexpected proxy error for {url}: {e}")
            await self._emit_access_event(
                url, allowed=False, reason=f"internal error: {e}",
            )
            return 500, {"X-Proxy-Error": str(e)}, b"Internal proxy error"

    async def _emit_access_event(
        self,
        url: str,
        allowed: bool,
        reason: str = "",
        status_code: int = 0,
    ):
        """Emit a ProxyAccessEvent if an event bus is available."""
        if self.event_bus is not None:
            await self.event_bus.publish(
                ProxyAccessEvent(
                    url=url,
                    allowed=allowed,
                    reason=reason,
                    status_code=status_code,
                )
            )

    def get_metrics(self) -> Dict[str, int]:
        """Return current proxy metrics."""
        return dict(self._metrics)

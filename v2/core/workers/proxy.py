"""
v2/core/workers/proxy.py

Proxy worker: URL inspection, ad filtering, playlist rewriting, and upscaler interface.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Pattern, Tuple
from urllib.parse import urljoin, urlparse, quote, unquote
from abc import ABC, abstractmethod

from ..bus import EventBus
from ..events import ProxyRequestEvent, ProxyFilteredEvent, ProxyBlockedEvent
from ..store import GlobalDataStore

logger = logging.getLogger(__name__)


class ProxyInspector:
    """
    Inspects URIs against whitelist/blacklist rules.
    """

    def __init__(
        self,
        whitelist_keywords: Optional[List[str]] = None,
        blacklist_keywords: Optional[List[str]] = None,
        whitelist_regexes: Optional[List[str]] = None,
        blacklist_regexes: Optional[List[str]] = None,
    ):
        self.whitelist_keywords = whitelist_keywords or []
        self.blacklist_keywords = blacklist_keywords or []
        self.whitelist_patterns = [re.compile(r) for r in (whitelist_regexes or [])]
        self.blacklist_patterns = [re.compile(r) for r in (blacklist_regexes or [])]

    def inspect(self, url: str) -> Dict:
        """
        Inspect a URL against whitelist/blacklist rules.
        Returns dict with allowed (bool), matched_rule (str), rule_type (str).
        """
        # Check blacklist first (keywords)
        for kw in self.blacklist_keywords:
            if kw in url:
                return {"allowed": False, "matched_rule": kw, "rule_type": "keyword"}

        # Check blacklist regex patterns
        for pattern in self.blacklist_patterns:
            if pattern.search(url):
                return {
                    "allowed": False,
                    "matched_rule": pattern.pattern,
                    "rule_type": "regex",
                }

        # Check whitelist keywords
        for kw in self.whitelist_keywords:
            if kw in url:
                return {"allowed": True, "matched_rule": kw, "rule_type": "keyword"}

        # Check whitelist regex patterns
        for pattern in self.whitelist_patterns:
            if pattern.search(url):
                return {
                    "allowed": True,
                    "matched_rule": pattern.pattern,
                    "rule_type": "regex",
                }

        # Default: allowed
        return {"allowed": True, "matched_rule": "", "rule_type": "default"}


class AdFilter:
    """
    Filters ad segments from HLS playlists.
    Supports keyword/regex matching, CUE markers, and discontinuity blocks.
    """

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        regexes: Optional[List[str]] = None,
        drop_cue_ads: bool = True,
        drop_discontinuity_ads: bool = True,
    ):
        self.keywords = keywords or []
        self.patterns = [re.compile(r) for r in (regexes or [])]
        self.drop_cue_ads = drop_cue_ads
        self.drop_discontinuity_ads = drop_discontinuity_ads

    def _is_ad_uri(self, uri: str) -> bool:
        """Check if a URI matches ad filter rules."""
        for kw in self.keywords:
            if kw in uri:
                return True
        for pattern in self.patterns:
            if pattern.search(uri):
                return True
        return False

    def filter_media_playlist(
        self,
        content: str,
        base_url: str,
        proxy_base: Optional[str] = None,
    ) -> str:
        """
        Filter a media playlist, removing ad segments and rewriting URIs.
        """
        lines = content.strip().split("\n")
        output: List[str] = []
        buffer: List[str] = []
        in_cue_block = False
        discontinuity_block: List[str] = []
        in_discontinuity_block = False

        for line in lines:
            stripped = line.strip()

            # Handle CUE-OUT markers (exact match or with attributes like DURATION=30)
            if stripped.startswith("#EXT-X-CUE-OUT"):
                if self.drop_cue_ads:
                    in_cue_block = True
                    # Don't add the CUE-OUT to output
                    if buffer:
                        output.extend(buffer)
                        buffer = []
                else:
                    output.append(line)
                continue

            if stripped.startswith("#EXT-X-CUE-IN"):
                if self.drop_cue_ads:
                    in_cue_block = False
                    # Drop all buffered content from ad block
                    buffer = []
                else:
                    output.append(line)
                continue

            # If inside a cue block, skip the line
            if in_cue_block:
                continue

            # Handle DISCONTINUITY (exact match or with attributes)
            if stripped.startswith("#EXT-X-DISCONTINUITY") and not stripped.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
                if self.drop_discontinuity_ads and in_discontinuity_block:
                    # Check if the current discontinuity block has ads
                    block_uris = [
                        ln
                        for ln in discontinuity_block
                        if not ln.startswith("#") and ln.strip()
                    ]
                    has_ad = any(self._is_ad_uri(uri) for uri in block_uris)
                    if has_ad:
                        # Drop the entire block
                        discontinuity_block = []
                    else:
                        # Keep the block
                        output.extend(discontinuity_block)
                        output.append(line)
                        discontinuity_block = []
                elif in_discontinuity_block:
                    output.extend(discontinuity_block)
                    output.append(line)
                    discontinuity_block = []
                else:
                    in_discontinuity_block = True
                    discontinuity_block.append(line)
                continue

            if in_discontinuity_block:
                discontinuity_block.append(line)
                continue

            # Handle URI lines (non-comment, non-tag)
            if not stripped.startswith("#") and stripped:
                uri = stripped
                # Resolve relative URIs
                if not uri.startswith("http://") and not uri.startswith("https://"):
                    uri = urljoin(base_url, uri)
                # Check if this is an ad
                if self._is_ad_uri(uri):
                    # If we were buffering tags, drop them too
                    buffer = []
                    continue
                else:
                    # Add any buffered tags, then the URI
                    if buffer:
                        output.extend(buffer)
                        buffer = []
                    if proxy_base:
                        # Rewrite URI to proxy URL
                        output.append(self._build_proxy_url(proxy_base, uri))
                    else:
                        output.append(uri)
                continue

            # Handle tag lines or comments (starting with #)
            if stripped.startswith("#"):
                # Check if it's an EXTINF or other segment-related tag
                if stripped.startswith("#EXTINF") or stripped.startswith(
                    "#EXT-X-KEY"
                ) or stripped.startswith("#EXT-X-MAP"):
                    buffer.append(line)
                else:
                    # If we have buffered content and encounter a non-segment tag,
                    # flush the buffer, then add this tag
                    if buffer:
                        output.extend(buffer)
                        buffer = []
                    output.append(line)
                continue

            # Plain non-URI line
            output.append(line)

        # Flush remaining discontinuity block
        if in_discontinuity_block and discontinuity_block:
            block_uris = [
                ln
                for ln in discontinuity_block
                if not ln.startswith("#") and ln.strip()
            ]
            has_ad = any(self._is_ad_uri(uri) for uri in block_uris)
            if self.drop_discontinuity_ads and has_ad:
                pass  # Drop block
            else:
                output.extend(discontinuity_block)

        # Flush remaining buffer
        if buffer:
            output.extend(buffer)

        return "\n".join(output) + "\n"

    def filter_master_playlist(
        self,
        content: str,
        base_url: str,
        proxy_base: str,
    ) -> str:
        """
        Filter a master playlist, rewriting variant URIs to proxy.
        """
        lines = content.strip().split("\n")
        output: List[str] = []

        for line in lines:
            stripped = line.strip()

            # Rewrite URI attributes in EXT-X-MEDIA, EXT-X-I-FRAME-STREAM-INF
            if 'URI="' in stripped:
                # Replace URI="..." with rewritten version
                def _replace_uri(match):
                    uri = match.group(1)
                    if not uri.startswith("http://") and not uri.startswith(
                        "https://"
                    ):
                        uri = urljoin(base_url, uri)
                    proxy_uri = self._build_proxy_url(proxy_base, uri)
                    # Ensure the URI is properly quoted
                    return f'URI="{proxy_uri}"'

                stripped = re.sub(r'URI="([^"]+)"', _replace_uri, stripped)
                output.append(stripped)
                continue

            # If this is a bare URI line (following STREAM-INF), rewrite it
            if not stripped.startswith("#") and stripped:
                uri = stripped
                if not uri.startswith("http://") and not uri.startswith("https://"):
                    uri = urljoin(base_url, uri)
                output.append(self._build_proxy_url(proxy_base, uri))
                continue

            output.append(stripped)

        return "\n".join(output) + "\n"

    @staticmethod
    def _build_proxy_url(proxy_base: str, target_url: str) -> str:
        """Build a proxy URL that points to the proxy handler."""
        separator = "&" if "?" in proxy_base else "?"
        return f"{proxy_base}{separator}url={quote(target_url, safe='')}"


class PlaylistFilter:
    """
    Dispatches playlist filtering to the appropriate handler.
    """

    def __init__(self, ad_filter: AdFilter):
        self.ad_filter = ad_filter

    @staticmethod
    def is_master_playlist(content: str) -> bool:
        return "#EXT-X-STREAM-INF" in content

    @staticmethod
    def is_media_playlist(content: str) -> bool:
        return "#EXTINF" in content or "#EXT-X-TARGETDURATION" in content

    def filter(
        self,
        content: str,
        base_url: str,
        proxy_base: str,
    ) -> Tuple[str, str]:
        """
        Filter a playlist. Returns (filtered_content, kind).
        Kind is 'master', 'media', or 'passthrough'.
        """
        if self.is_master_playlist(content):
            return (
                self.ad_filter.filter_master_playlist(content, base_url, proxy_base),
                "master",
            )
        elif self.is_media_playlist(content):
            return (
                self.ad_filter.filter_media_playlist(content, base_url, proxy_base),
                "media",
            )
        else:
            return (content, "passthrough")


class UpscalerInterface(ABC):
    """
    Abstract base class for upscaler detection algorithms.
    Subclasses implement analyze() to detect upscaled video.
    """

    @abstractmethod
    def analyze(self, url: str) -> Dict:
        """
        Analyze a URL for upscale detection.
        Returns dict with at least:
          - url: the analyzed URL
          - is_upscaled: bool
          - confidence: float (0.0 to 1.0)
        """
        ...


class ProxyWorker:
    """
    Main proxy worker that coordinates inspection, filtering, and events.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        data_store: Optional[GlobalDataStore] = None,
        inspector: Optional[ProxyInspector] = None,
        playlist_filter: Optional[PlaylistFilter] = None,
    ):
        self.event_bus = event_bus
        self.data_store = data_store
        self.inspector = inspector or ProxyInspector()
        self.playlist_filter = playlist_filter or PlaylistFilter(AdFilter())

    async def inspect_url(self, url: str) -> Dict:
        """Inspect a URL and emit events if blocked."""
        result = self.inspector.inspect(url)
        if not result["allowed"] and self.event_bus:
            await self.event_bus.publish(
                ProxyBlockedEvent(
                    url=url,
                    rule=result["matched_rule"],
                    rule_type=result["rule_type"],
                )
            )
        return result

    async def filter_playlist(
        self,
        content: str,
        base_url: str,
        proxy_base: str,
    ) -> Tuple[str, str]:
        """Filter a playlist and emit events."""
        result, kind = self.playlist_filter.filter(content, base_url, proxy_base)
        if self.event_bus:
            await self.event_bus.publish(
                ProxyFilteredEvent(
                    url=base_url,
                    kind=kind,
                )
            )
        return result, kind
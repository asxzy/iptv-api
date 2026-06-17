"""
v2/core/tests/test_proxy.py

Tests for the ProxyWorker implementation.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.workers.proxy import (
    ProxyInspector,
    AdFilter,
    PlaylistFilter,
    UpscalerInterface,
)

# ── ProxyInspector Tests ──────────────────────────────────────────────

class TestProxyInspector:
    """Test the ProxyInspector URL inspection."""

    def test_keyword_whitelist_match(self):
        inspector = ProxyInspector(
            whitelist_keywords=["cdn.example.com", "premium-cdn"],
            blacklist_keywords=[],
        )
        result = inspector.inspect("http://cdn.example.com/stream.m3u8")
        assert result["allowed"] is True
        assert "cdn.example.com" in result["matched_rule"]

    def test_keyword_blacklist_reject(self):
        inspector = ProxyInspector(
            whitelist_keywords=[],
            blacklist_keywords=["bad-stream", "evil"],
        )
        result = inspector.inspect("http://cdn.example.com/bad-stream/playlist.m3u8")
        assert result["allowed"] is False
        assert "bad-stream" in result["matched_rule"]

    def test_regex_whitelist_match(self):
        inspector = ProxyInspector(
            whitelist_regexes=[r"googlevideo\.com"],
            blacklist_regexes=[],
        )
        result = inspector.inspect("http://rr1---sn-abc.googlevideo.com/videoplayback")
        assert result["allowed"] is True
        assert "googlevideo\\.com" in result["matched_rule"]

    def test_regex_blacklist_reject(self):
        inspector = ProxyInspector(
            whitelist_regexes=[],
            blacklist_regexes=[r"evil\.spam"],
        )
        result = inspector.inspect("http://evil.spam.org/stream.m3u8")
        assert result["allowed"] is False
        assert "evil\\.spam" in result["matched_rule"]

    def test_no_match_default_allowed(self):
        inspector = ProxyInspector(
            whitelist_keywords=["cdn.example.com"],
            blacklist_keywords=["bad-stream"],
        )
        result = inspector.inspect("http://unknown.example.com/stream.m3u8")
        # Not in whitelist and not in blacklist -> default depends on config
        # Default: allowed (if no blacklist match, treat as allowed)
        assert result["allowed"] is True

    def test_blacklist_trumps_whitelist(self):
        inspector = ProxyInspector(
            whitelist_keywords=["cdn.example.com"],
            blacklist_keywords=["bad-stream"],
        )
        result = inspector.inspect("http://cdn.example.com/bad-stream/playlist.m3u8")
        assert result["allowed"] is False

    def test_empty_lists(self):
        inspector = ProxyInspector()
        result = inspector.inspect("http://example.com/stream.m3u8")
        assert result["allowed"] is True


# ── AdFilter Tests ────────────────────────────────────────────────────

class TestAdFilter:
    """Test the AdFilter ad segment filtering."""

    def test_ad_segment_keyword_removal(self):
        filter_obj = AdFilter(keywords=["doubleclick.net", "adserver"])
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/segment1.ts\n"
            "#EXTINF:10,\n"
            "http://doubleclick.net/ad1.ts\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/segment3.ts\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        assert "ad1.ts" not in result
        assert "segment1.ts" in result
        assert "segment3.ts" in result
        # The EXTINF for the ad segment should also be removed
        lines = result.strip().split("\n")
        assert lines.count("#EXTINF:10,") == 2  # Only two EXTINFs for non-ad segments

    def test_ad_segment_regex_removal(self):
        filter_obj = AdFilter(regexes=[r"ads?\d*\.ts"])
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg1.ts\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/ad2.ts\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        assert "ad2.ts" not in result
        assert "seg1.ts" in result

    def test_cue_out_in_removal(self):
        filter_obj = AdFilter(drop_cue_ads=True)
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg1.ts\n"
            "#EXT-X-CUE-OUT\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/ad1.ts\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/ad2.ts\n"
            "#EXT-X-CUE-IN\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg2.ts\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        assert "ad1.ts" not in result
        assert "ad2.ts" not in result
        assert "#EXT-X-CUE-OUT" not in result
        assert "#EXT-X-CUE-IN" not in result
        assert "seg1.ts" in result
        assert "seg2.ts" in result

    def test_cue_disabled_does_not_drop(self):
        filter_obj = AdFilter(drop_cue_ads=False)
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-CUE-OUT\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/ad1.ts\n"
            "#EXT-X-CUE-IN\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        # When cue dropping is disabled, the ad segment should remain
        assert "ad1.ts" in result

    def test_discontinuity_ad_block_removal(self):
        filter_obj = AdFilter(
            keywords=["adserver"],
            drop_discontinuity_ads=True,
        )
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg1.ts\n"
            "#EXT-X-DISCONTINUITY\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg2.ts\n"
            "#EXTINF:10,\n"
            "http://adserver.example.com/ad1.ts\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg3.ts\n"
            "#EXT-X-DISCONTINUITY\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg4.ts\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        # The discontinuity block with adserver should be removed entirely
        assert "seg2.ts" not in result  # In the removed block
        assert "ad1.ts" not in result
        assert "seg3.ts" not in result
        assert "seg1.ts" in result
        assert "seg4.ts" in result

    def test_discontinuity_disabled_does_not_drop(self):
        filter_obj = AdFilter(
            keywords=["adserver"],
            drop_discontinuity_ads=False,
        )
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/seg1.ts\n"
            "#EXT-X-DISCONTINUITY\n"
            "#EXTINF:10,\n"
            "http://adserver.example.com/ad1.ts\n"
            "#EXT-X-DISCONTINUITY\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        assert "ad1.ts" in result  # Not removed when disabled

    def test_no_ad_keyword_no_removal(self):
        filter_obj = AdFilter(keywords=["adserver"])
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/clean1.ts\n"
            "#EXTINF:10,\n"
            "http://cdn.example.com/clean2.ts\n"
        )
        result = filter_obj.filter_media_playlist(playlist, "http://cdn.example.com/playlist.m3u8")
        assert "clean1.ts" in result
        assert "clean2.ts" in result

    def test_master_playlist_rewriting(self):
        filter_obj = AdFilter()
        proxy_base = "/proxy"
        base_url = "http://origin.example.com/live/"
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
            "low.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2560000\n"
            "mid.m3u8\n"
            "#EXT-X-MEDIA:TYPE=VIDEO,URI=\"high.m3u8\"\n"
        )
        result = filter_obj.filter_master_playlist(playlist, base_url, proxy_base)
        # Variant URIs should be rewritten to proxy URLs
        assert "/proxy?url=" in result
        assert "origin.example.com" in result
        # The bare relative URIs should be replaced with proxy-wrapped URLs
        # The original relative URI "low.m3u8" should not appear as a bare line
        lines = result.strip().split("\n")
        assert not any(line.strip() == "low.m3u8" for line in lines)
        assert not any(line.strip() == "mid.m3u8" for line in lines)
        # The proxy URL should contain the resolved URI
        assert "/proxy?url=http%3A%2F%2Forigin.example.com%2Flive%2Flow.m3u8" in result

    def test_relative_uri_resolution(self):
        filter_obj = AdFilter()
        playlist = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "relative/path/seg1.ts\n"
        )
        base_url = "http://cdn.example.com/live/playlist.m3u8"
        result = filter_obj.filter_media_playlist(playlist, base_url)
        # Relative URI should be resolved to absolute
        assert "http://cdn.example.com/live/relative/path/seg1.ts" in result
        # The bare relative path should not appear as a standalone URI line
        lines = result.strip().split("\n")
        assert "relative/path/seg1.ts" not in lines


# ── PlaylistFilter Tests ──────────────────────────────────────────────

class TestPlaylistFilter:
    """Test the PlaylistFilter dispatcher."""

    def test_dispatch_master_playlist(self):
        filter_obj = AdFilter()
        pf = PlaylistFilter(filter_obj)
        content = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
            "low.m3u8\n"
        )
        result, kind = pf.filter(content, "http://origin.example.com/playlist.m3u8", "/proxy")
        assert kind == "master"
        assert "/proxy?url=" in result

    def test_dispatch_media_playlist(self):
        filter_obj = AdFilter(keywords=["ad"])
        pf = PlaylistFilter(filter_obj)
        content = (
            "#EXTM3U\n"
            "#EXTINF:10,\n"
            "seg1.ts\n"
        )
        result, kind = pf.filter(content, "http://cdn.example.com/playlist.m3u8", "/proxy")
        assert kind == "media"
        assert "seg1.ts" in result

    def test_dispatch_unknown_passthrough(self):
        filter_obj = AdFilter()
        pf = PlaylistFilter(filter_obj)
        content = "plain text content"
        result, kind = pf.filter(content, "http://cdn.example.com/file.txt", "/proxy")
        assert kind == "passthrough"
        assert result == content


# ── UpscalerInterface Tests ───────────────────────────────────────────

class TestUpscalerInterface:
    """Test the UpscalerInterface."""

    def test_concrete_implementation(self):
        class TestUpscaler(UpscalerInterface):
            def analyze(self, url: str) -> dict:
                return {"url": url, "is_upscaled": False, "confidence": 0.0}

        upscaler = TestUpscaler()
        result = upscaler.analyze("http://example.com/stream.m3u8")
        assert result["is_upscaled"] is False
        assert result["url"] == "http://example.com/stream.m3u8"
        assert result["confidence"] == 0.0

    def test_abstract_class_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            UpscalerInterface()  # Should fail because analyze is abstract


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
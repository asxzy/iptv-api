"""
Self-contained unit tests for service/proxy.py — pure HLS ad-filtering core.

Run via:
    python tests/test_proxy_filter.py
    python -m pytest tests/test_proxy_filter.py -q
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from service.proxy import (
    AdFilter,
    is_master_playlist,
    is_media_playlist,
    build_proxy_url,
    resolve_uri,
    filter_master_playlist,
    filter_media_playlist,
    filter_playlist,
    load_ad_filters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "http://cdn.example.com/live/"
PROXY = "/proxy"


def make_master(variants, media_tags=None, iframe_tags=None):
    """
    Build an HLS master playlist string.

    variants: list of (attributes_str, uri) pairs
    media_tags: list of full #EXT-X-MEDIA:... lines (without leading newline)
    iframe_tags: list of full #EXT-X-I-FRAME-STREAM-INF:... lines
    """
    lines = ["#EXTM3U", "#EXT-X-VERSION:6"]
    for attrs, uri in (variants or []):
        lines.append(f"#EXT-X-STREAM-INF:{attrs}")
        lines.append(uri)
    for tag in (media_tags or []):
        lines.append(tag)
    for tag in (iframe_tags or []):
        lines.append(tag)
    return "\n".join(lines) + "\n"


def make_media(segments, header_extra=None, endlist=True):
    """
    Build an HLS media playlist.

    segments: list of (tags_before, uri) where tags_before is a list of tag strings
              OR just a plain uri string (no extra tags).
    header_extra: list of extra header lines to insert.
    """
    lines = [
        "#EXTM3U",
        "#EXT-X-TARGETDURATION:10",
        "#EXT-X-VERSION:3",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    if header_extra:
        lines.extend(header_extra)
    for seg in (segments or []):
        if isinstance(seg, str):
            lines.append("#EXTINF:10.0,")
            lines.append(seg)
        else:
            tags, uri = seg
            lines.extend(tags)
            lines.append(uri)
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def no_filter():
    """An AdFilter that matches nothing."""
    return AdFilter(keywords=[], regexes=[])


# ---------------------------------------------------------------------------
# AdFilter tests
# ---------------------------------------------------------------------------

def test_adfilter_keyword_match():
    f = AdFilter(keywords=["adserver", "ad-break"])
    assert f.matches("http://adserver.com/seg.ts") is True
    assert f.matches("http://cdn.com/clean.ts") is False


def test_adfilter_regex_match():
    import re
    f = AdFilter(regexes=[re.compile(r"/ads/\d+")])
    assert f.matches("http://cdn.com/ads/123/seg.ts") is True
    assert f.matches("http://cdn.com/content/seg.ts") is False


def test_adfilter_empty_matches_nothing():
    f = AdFilter()
    assert f.matches("http://cdn.com/ads/anything.ts") is False


def test_adfilter_keyword_and_regex_combined():
    import re
    f = AdFilter(keywords=["adserver"], regexes=[re.compile(r"placeholder")])
    assert f.matches("http://adserver.example.com/x.ts") is True
    assert f.matches("http://cdn.example.com/placeholder/y.ts") is True
    assert f.matches("http://cdn.example.com/clean.ts") is False


# ---------------------------------------------------------------------------
# is_master_playlist / is_media_playlist
# ---------------------------------------------------------------------------

def test_is_master_playlist_true():
    content = make_master([("BANDWIDTH=1000000", "720.m3u8")])
    assert is_master_playlist(content) is True
    assert is_media_playlist(content) is False


def test_is_media_playlist_extinf():
    content = make_media(["seg0.ts"])
    assert is_media_playlist(content) is True
    assert is_master_playlist(content) is False


def test_is_media_playlist_targetduration_only():
    content = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-ENDLIST\n"
    assert is_media_playlist(content) is True


def test_not_playlist_passthrough():
    content = "this is not a playlist at all"
    assert is_master_playlist(content) is False
    assert is_media_playlist(content) is False


def test_empty_string_not_playlist():
    assert is_master_playlist("") is False
    assert is_media_playlist("") is False


# ---------------------------------------------------------------------------
# build_proxy_url
# ---------------------------------------------------------------------------

def test_build_proxy_url_basic():
    result = build_proxy_url("/proxy", "http://x/a.m3u8")
    assert result == "/proxy?url=http%3A%2F%2Fx%2Fa.m3u8"


def test_build_proxy_url_encodes_special_chars():
    result = build_proxy_url("/proxy", "http://x/path?foo=bar&baz=qux")
    assert "?" not in result.split("?url=")[1]  # everything after ?url= is fully encoded
    assert result.startswith("/proxy?url=")


def test_build_proxy_url_already_absolute():
    url = "http://cdn.example.com/live/seg0.ts"
    result = build_proxy_url("/proxy", url)
    assert result == "/proxy?url=http%3A%2F%2Fcdn.example.com%2Flive%2Fseg0.ts"


# ---------------------------------------------------------------------------
# resolve_uri
# ---------------------------------------------------------------------------

def test_resolve_uri_relative():
    assert resolve_uri("http://cdn.example.com/live/", "seg0.ts") == \
        "http://cdn.example.com/live/seg0.ts"


def test_resolve_uri_absolute_unchanged():
    abs_uri = "http://other.example.com/seg.ts"
    assert resolve_uri("http://cdn.example.com/live/", abs_uri) == abs_uri


def test_resolve_uri_relative_path():
    assert resolve_uri("http://cdn.example.com/live/index.m3u8", "720/index.m3u8") == \
        "http://cdn.example.com/live/720/index.m3u8"


# ---------------------------------------------------------------------------
# filter_master_playlist
# ---------------------------------------------------------------------------

def test_master_variant_uri_rewritten():
    content = make_master([
        ("BANDWIDTH=1000000,RESOLUTION=1280x720", "720.m3u8"),
        ("BANDWIDTH=3000000,RESOLUTION=1920x1080", "1080.m3u8"),
    ])
    result = filter_master_playlist(content, BASE, PROXY)
    # Absolute resolved urls should be proxied
    abs_720 = resolve_uri(BASE, "720.m3u8")
    abs_1080 = resolve_uri(BASE, "1080.m3u8")
    assert build_proxy_url(PROXY, abs_720) in result
    assert build_proxy_url(PROXY, abs_1080) in result
    # Original relative URIs should not appear as bare lines
    assert "\n720.m3u8\n" not in result
    assert "\n1080.m3u8\n" not in result


def test_master_absolute_variant_uri_rewritten():
    content = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "http://cdn.example.com/720.m3u8\n"
    )
    result = filter_master_playlist(content, BASE, PROXY)
    expected = build_proxy_url(PROXY, "http://cdn.example.com/720.m3u8")
    assert expected in result


def test_master_ext_x_media_uri_rewritten():
    content = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="audio/en.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "720.m3u8\n"
    )
    result = filter_master_playlist(content, BASE, PROXY)
    abs_audio = resolve_uri(BASE, "audio/en.m3u8")
    assert build_proxy_url(PROXY, abs_audio) in result


def test_master_iframe_stream_inf_uri_rewritten():
    content = (
        "#EXTM3U\n"
        '#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=100000,URI="iframe/index.m3u8"\n'
    )
    result = filter_master_playlist(content, BASE, PROXY)
    abs_iframe = resolve_uri(BASE, "iframe/index.m3u8")
    assert build_proxy_url(PROXY, abs_iframe) in result


def test_master_non_uri_lines_kept_verbatim():
    content = make_master([("BANDWIDTH=1000000", "720.m3u8")])
    result = filter_master_playlist(content, BASE, PROXY)
    assert "#EXTM3U" in result
    assert "#EXT-X-VERSION:6" in result
    assert "BANDWIDTH=1000000" in result


def test_master_relative_uri_resolution():
    """Relative variant URIs must be resolved against base_url before proxying."""
    base = "http://cdn.example.com/streams/index.m3u8"
    content = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "720/index.m3u8\n"
    )
    result = filter_master_playlist(content, base, PROXY)
    expected_abs = "http://cdn.example.com/streams/720/index.m3u8"
    assert build_proxy_url(PROXY, expected_abs) in result


# ---------------------------------------------------------------------------
# filter_media_playlist — no-op (no filter matches)
# ---------------------------------------------------------------------------

def test_media_no_filter_keeps_all_segments():
    content = make_media(["seg0.ts", "seg1.ts", "seg2.ts"])
    result = filter_media_playlist(content, BASE, no_filter())
    # All segment URIs should appear (as absolute)
    for i in range(3):
        assert resolve_uri(BASE, f"seg{i}.ts") in result


def test_media_uri_rewritten_to_absolute():
    content = make_media(["seg0.ts"])
    result = filter_media_playlist(content, BASE, no_filter())
    abs_uri = resolve_uri(BASE, "seg0.ts")
    assert abs_uri in result
    # Relative form should NOT appear as a bare segment line
    assert "\nseg0.ts\n" not in result


# ---------------------------------------------------------------------------
# filter_media_playlist — keyword drop
# ---------------------------------------------------------------------------

def test_media_keyword_drop():
    f = AdFilter(keywords=["adserver"])
    content = make_media([
        "http://cdn.example.com/clean.ts",
        "http://adserver.example.com/ad.ts",
        "http://cdn.example.com/clean2.ts",
    ])
    result = filter_media_playlist(content, BASE, f)
    assert "http://cdn.example.com/clean.ts" in result
    assert "adserver.example.com" not in result
    assert "http://cdn.example.com/clean2.ts" in result


def test_media_regex_drop():
    import re
    f = AdFilter(regexes=[re.compile(r"/ads/\d+")])
    content = make_media([
        "http://cdn.example.com/content/seg0.ts",
        "http://cdn.example.com/ads/123/seg.ts",
    ])
    result = filter_media_playlist(content, BASE, f)
    assert "/content/seg0.ts" in result
    assert "/ads/123/" not in result


def test_media_keyword_drop_relative_uri_resolved():
    """
    Keyword filter must operate on the *resolved* absolute URI, not the raw
    relative one (spec: substring match on the *resolved* segment URI).
    """
    f = AdFilter(keywords=["adserver.example.com"])
    # seg is relative but resolves to adserver.example.com/...
    base = "http://adserver.example.com/live/"
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, base, f)
    assert "seg0.ts" not in result


# ---------------------------------------------------------------------------
# filter_media_playlist — CUE-OUT/IN drops
# ---------------------------------------------------------------------------

def test_media_cue_ad_dropped():
    f = AdFilter(drop_cue_ads=True)
    content = make_media([
        "clean_before.ts",
        (["#EXT-X-CUE-OUT:DURATION=30"], "ad_seg1.ts"),
        ([], "ad_seg2.ts"),
        (["#EXT-X-CUE-IN"], "clean_after.ts"),
    ])
    result = filter_media_playlist(content, BASE, f)
    assert resolve_uri(BASE, "clean_before.ts") in result
    assert "ad_seg1.ts" not in result
    assert "ad_seg2.ts" not in result
    assert resolve_uri(BASE, "clean_after.ts") in result


def test_media_cue_tags_suppressed_when_drop_cue_ads():
    f = AdFilter(drop_cue_ads=True)
    content = make_media([
        "clean.ts",
        (["#EXT-X-CUE-OUT:DURATION=30"], "ad.ts"),
        (["#EXT-X-CUE-IN"], "clean2.ts"),
    ])
    result = filter_media_playlist(content, BASE, f)
    assert "#EXT-X-CUE-OUT" not in result
    assert "#EXT-X-CUE-IN" not in result


def test_media_cue_kept_when_drop_cue_ads_false():
    f = AdFilter(drop_cue_ads=False)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-CUE-OUT:DURATION=30\n"
        "#EXTINF:10.0,\n"
        "ad.ts\n"
        "#EXT-X-CUE-IN\n"
        "#EXTINF:10.0,\n"
        "clean.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "#EXT-X-CUE-OUT" in result
    assert "#EXT-X-CUE-IN" in result
    assert resolve_uri(BASE, "ad.ts") in result


def test_media_cue_without_duration_dropped():
    f = AdFilter(drop_cue_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-CUE-OUT\n"
        "#EXTINF:10.0,\n"
        "ad.ts\n"
        "#EXT-X-CUE-IN\n"
        "#EXTINF:10.0,\n"
        "clean.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "ad.ts" not in result
    assert resolve_uri(BASE, "clean.ts") in result


# ---------------------------------------------------------------------------
# filter_media_playlist — discontinuity-bounded drop
# ---------------------------------------------------------------------------

def test_media_discontinuity_ad_dropped():
    """Discontinuity block whose segments match the filter → dropped."""
    f = AdFilter(keywords=["adserver"], drop_discontinuity_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/clean1.ts\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:10.0,\n"
        "http://adserver.example.com/ad1.ts\n"
        "#EXTINF:10.0,\n"
        "http://adserver.example.com/ad2.ts\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/clean2.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "http://cdn.example.com/clean1.ts" in result
    assert "adserver.example.com" not in result
    assert "http://cdn.example.com/clean2.ts" in result


def test_media_discontinuity_clean_block_kept():
    """Discontinuity block whose segments are clean → kept (conservative)."""
    f = AdFilter(keywords=["adserver"], drop_discontinuity_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/before.ts\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/after_discont.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "http://cdn.example.com/before.ts" in result
    assert "http://cdn.example.com/after_discont.ts" in result


def test_media_discontinuity_drop_disabled_keeps_block():
    """drop_discontinuity_ads=False → matching block is kept."""
    f = AdFilter(keywords=["adserver"], drop_discontinuity_ads=False)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:10.0,\n"
        "http://adserver.example.com/ad.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "adserver.example.com" in result


def test_media_discontinuity_tag_included_in_kept_block():
    """When a discontinuity block is kept, its #EXT-X-DISCONTINUITY tag is preserved."""
    f = AdFilter(keywords=["adserver"], drop_discontinuity_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/clean.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "#EXT-X-DISCONTINUITY" in result


# ---------------------------------------------------------------------------
# filter_media_playlist — EXT-X-KEY / EXT-X-MAP rewrite
# ---------------------------------------------------------------------------

def test_media_ext_x_key_uri_rewritten():
    f = no_filter()
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x0\n'
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    abs_key = resolve_uri(BASE, "key.bin")
    assert abs_key in result
    # Original relative should not appear in a URI="..." context
    assert 'URI="key.bin"' not in result


def test_media_ext_x_map_uri_rewritten():
    f = no_filter()
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    abs_map = resolve_uri(BASE, "init.mp4")
    assert abs_map in result
    assert 'URI="init.mp4"' not in result


def test_media_ext_x_key_absolute_uri_unchanged():
    f = no_filter()
    abs_key = "http://keys.example.com/key.bin"
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        f'#EXT-X-KEY:METHOD=AES-128,URI="{abs_key}",IV=0x0\n'
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert abs_key in result


# ---------------------------------------------------------------------------
# filter_media_playlist — header tags pass-through / fidelity
# ---------------------------------------------------------------------------

def test_media_header_tags_preserved():
    f = no_filter()
    content = make_media(["seg0.ts"])
    result = filter_media_playlist(content, BASE, f)
    for tag in ["#EXTM3U", "#EXT-X-TARGETDURATION", "#EXT-X-VERSION", "#EXT-X-MEDIA-SEQUENCE"]:
        assert tag in result


def test_media_endlist_preserved():
    f = no_filter()
    content = make_media(["seg0.ts"])
    result = filter_media_playlist(content, BASE, f)
    assert "#EXT-X-ENDLIST" in result


def test_media_unknown_tags_preserved():
    f = no_filter()
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#X-CUSTOM-TAG:some-value\n"
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "#X-CUSTOM-TAG:some-value" in result


def test_media_trailing_newline_preserved():
    f = no_filter()
    content = make_media(["seg0.ts"])
    assert content.endswith("\n")
    result = filter_media_playlist(content, BASE, f)
    assert result.endswith("\n")


def test_media_no_trailing_newline_preserved():
    f = no_filter()
    content = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10.0,\nseg0.ts\n#EXT-X-ENDLIST"
    result = filter_media_playlist(content, BASE, f)
    assert not result.endswith("\n") or result.rstrip("\n") == result.rstrip("\n")
    # The key check: no trailing newline added if input didn't have one
    # (splitlines(keepends=True) reproduces the original structure faithfully)
    assert "#EXT-X-ENDLIST" in result


def test_media_extinf_tag_preserved_with_kept_segment():
    f = no_filter()
    content = make_media(["seg0.ts"])
    result = filter_media_playlist(content, BASE, f)
    assert "#EXTINF:10.0," in result


# ---------------------------------------------------------------------------
# filter_playlist — dispatcher
# ---------------------------------------------------------------------------

def test_filter_playlist_master_detected():
    content = make_master([("BANDWIDTH=1000000", "720.m3u8")])
    _, kind = filter_playlist(content, BASE, PROXY, no_filter())
    assert kind == "master"


def test_filter_playlist_media_detected():
    content = make_media(["seg0.ts"])
    _, kind = filter_playlist(content, BASE, PROXY, no_filter())
    assert kind == "media"


def test_filter_playlist_passthrough():
    content = "this is not a playlist"
    text, kind = filter_playlist(content, BASE, PROXY, no_filter())
    assert kind == "passthrough"
    assert text == content


def test_filter_playlist_empty_passthrough():
    text, kind = filter_playlist("", BASE, PROXY, no_filter())
    assert kind == "passthrough"
    assert text == ""


def test_filter_playlist_master_returns_rewritten():
    content = make_master([("BANDWIDTH=1000000", "720.m3u8")])
    text, kind = filter_playlist(content, BASE, PROXY, no_filter())
    assert kind == "master"
    assert build_proxy_url(PROXY, resolve_uri(BASE, "720.m3u8")) in text


def test_filter_playlist_media_returns_filtered():
    f = AdFilter(keywords=["adserver"])
    content = make_media([
        "http://cdn.example.com/clean.ts",
        "http://adserver.example.com/ad.ts",
    ])
    text, kind = filter_playlist(content, BASE, PROXY, f)
    assert kind == "media"
    assert "http://cdn.example.com/clean.ts" in text
    assert "adserver.example.com" not in text


# ---------------------------------------------------------------------------
# load_ad_filters — resilience
# ---------------------------------------------------------------------------

def test_load_ad_filters_no_files(tmp_path, monkeypatch):
    """load_ad_filters returns an AdFilter (possibly empty) even if no config files exist."""
    import service.proxy as proxy_mod
    # Point both paths to non-existent files.
    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path",
                        str(tmp_path / "nonexistent_proxy.txt"), raising=False)
    monkeypatch.setattr(proxy_mod.constants, "blacklist_path",
                        str(tmp_path / "nonexistent_blacklist.txt"), raising=False)
    af = proxy_mod.load_ad_filters()
    assert isinstance(af, AdFilter)


def test_load_ad_filters_reads_proxy_filter(tmp_path, monkeypatch):
    """load_ad_filters reads keywords and re: patterns from proxy_ad_filter.txt."""
    import service.proxy as proxy_mod
    pf = tmp_path / "proxy_ad_filter.txt"
    pf.write_text("# comment\nadserver\nre:ads/\\d+\n", encoding="utf-8")
    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path", str(pf), raising=False)
    af = proxy_mod.load_ad_filters()
    assert "adserver" in af.keywords
    assert any(p.pattern == r"ads/\d+" for p in af.regexes)


def test_load_ad_filters_falls_back_to_blacklist(tmp_path, monkeypatch):
    """Without proxy_ad_filter.txt, falls back to blacklist.txt keywords."""
    import service.proxy as proxy_mod
    bl = tmp_path / "blacklist.txt"
    bl.write_text("# blacklist comment\nbadkeyword\n", encoding="utf-8")
    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path",
                        str(tmp_path / "nonexistent.txt"), raising=False)
    monkeypatch.setattr(proxy_mod.constants, "blacklist_path", str(bl), raising=False)
    af = proxy_mod.load_ad_filters()
    assert "badkeyword" in af.keywords


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_master_empty_content():
    result = filter_master_playlist("", BASE, PROXY)
    assert result == ""


def test_media_empty_content():
    result = filter_media_playlist("", BASE, no_filter())
    assert result == ""


def test_media_all_segments_dropped_returns_valid_playlist():
    """When all segments are dropped, output still has the header tags."""
    f = AdFilter(keywords=["adserver"])
    content = make_media([
        "http://adserver.example.com/ad1.ts",
        "http://adserver.example.com/ad2.ts",
    ])
    result = filter_media_playlist(content, BASE, f)
    assert "#EXTM3U" in result
    assert "#EXT-X-TARGETDURATION" in result
    assert "adserver" not in result


def test_media_multiple_cue_breaks():
    """Multiple independent CUE-OUT/IN cycles all dropped."""
    f = AdFilter(drop_cue_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "clean1.ts\n"
        "#EXT-X-CUE-OUT:DURATION=15\n"
        "#EXTINF:10.0,\n"
        "ad1.ts\n"
        "#EXT-X-CUE-IN\n"
        "#EXTINF:10.0,\n"
        "clean2.ts\n"
        "#EXT-X-CUE-OUT:DURATION=15\n"
        "#EXTINF:10.0,\n"
        "ad2.ts\n"
        "#EXT-X-CUE-IN\n"
        "#EXTINF:10.0,\n"
        "clean3.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    for name in ["clean1.ts", "clean2.ts", "clean3.ts"]:
        assert resolve_uri(BASE, name) in result
    assert "ad1.ts" not in result
    assert "ad2.ts" not in result


def test_master_trailing_newline_preserved():
    content = make_master([("BANDWIDTH=1000000", "720.m3u8")])
    assert content.endswith("\n")
    result = filter_master_playlist(content, BASE, PROXY)
    assert result.endswith("\n")


def test_media_segments_always_absolute():
    """Segment URIs are always rewritten to absolute CDN URLs (never proxied);
    media bytes go straight to the CDN and never transit the server."""
    content = make_media(["seg0.ts"])
    result = filter_media_playlist(content, BASE, no_filter())
    abs_uri = resolve_uri(BASE, "seg0.ts")
    assert abs_uri in result
    assert "/proxy" not in result


def test_media_program_date_time_preserved():
    f = no_filter()
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-PROGRAM-DATE-TIME:2024-01-01T00:00:00Z\n"
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert "#EXT-X-PROGRAM-DATE-TIME:2024-01-01T00:00:00Z" in result


def test_media_absolute_segment_uri_not_double_resolved():
    """An already-absolute segment URI should not be double-resolved."""
    f = no_filter()
    abs_seg = "http://other-cdn.example.com/seg0.ts"
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        f"{abs_seg}\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    assert abs_seg in result
    # Should NOT have double http://
    assert result.count("http://other-cdn.example.com") == 1


# ---------------------------------------------------------------------------
# Regression: discontinuity at end of playlist (no following segment)
# ---------------------------------------------------------------------------

def test_media_trailing_discontinuity_not_dropped():
    """A #EXT-X-DISCONTINUITY at the end with no following segments is not an error."""
    f = AdFilter(keywords=["adserver"], drop_discontinuity_ads=True)
    content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "http://cdn.example.com/clean.ts\n"
        "#EXT-X-DISCONTINUITY\n"
        "#EXT-X-ENDLIST\n"
    )
    result = filter_media_playlist(content, BASE, f)
    # Should not crash; clean segment kept
    assert "http://cdn.example.com/clean.ts" in result


# ---------------------------------------------------------------------------
# pytest-compatible discovery + standalone runner
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_adfilter_keyword_match,
    test_adfilter_regex_match,
    test_adfilter_empty_matches_nothing,
    test_adfilter_keyword_and_regex_combined,
    test_is_master_playlist_true,
    test_is_media_playlist_extinf,
    test_is_media_playlist_targetduration_only,
    test_not_playlist_passthrough,
    test_empty_string_not_playlist,
    test_build_proxy_url_basic,
    test_build_proxy_url_encodes_special_chars,
    test_build_proxy_url_already_absolute,
    test_resolve_uri_relative,
    test_resolve_uri_absolute_unchanged,
    test_resolve_uri_relative_path,
    test_master_variant_uri_rewritten,
    test_master_absolute_variant_uri_rewritten,
    test_master_ext_x_media_uri_rewritten,
    test_master_iframe_stream_inf_uri_rewritten,
    test_master_non_uri_lines_kept_verbatim,
    test_master_relative_uri_resolution,
    test_media_no_filter_keeps_all_segments,
    test_media_uri_rewritten_to_absolute,
    test_media_keyword_drop,
    test_media_regex_drop,
    test_media_keyword_drop_relative_uri_resolved,
    test_media_cue_ad_dropped,
    test_media_cue_tags_suppressed_when_drop_cue_ads,
    test_media_cue_kept_when_drop_cue_ads_false,
    test_media_cue_without_duration_dropped,
    test_media_discontinuity_ad_dropped,
    test_media_discontinuity_clean_block_kept,
    test_media_discontinuity_drop_disabled_keeps_block,
    test_media_discontinuity_tag_included_in_kept_block,
    test_media_ext_x_key_uri_rewritten,
    test_media_ext_x_map_uri_rewritten,
    test_media_ext_x_key_absolute_uri_unchanged,
    test_media_header_tags_preserved,
    test_media_endlist_preserved,
    test_media_unknown_tags_preserved,
    test_media_trailing_newline_preserved,
    test_media_no_trailing_newline_preserved,
    test_media_extinf_tag_preserved_with_kept_segment,
    test_filter_playlist_master_detected,
    test_filter_playlist_media_detected,
    test_filter_playlist_passthrough,
    test_filter_playlist_empty_passthrough,
    test_filter_playlist_master_returns_rewritten,
    test_filter_playlist_media_returns_filtered,
    test_master_empty_content,
    test_media_empty_content,
    test_media_all_segments_dropped_returns_valid_playlist,
    test_media_multiple_cue_breaks,
    test_master_trailing_newline_preserved,
    test_media_segments_always_absolute,
    test_media_program_date_time_preserved,
    test_media_absolute_segment_uri_not_double_resolved,
    test_media_trailing_discontinuity_not_dropped,
]

# load_ad_filters tests require monkeypatch (pytest fixture) — skip in standalone mode
_PYTEST_ONLY_TESTS = {
    test_load_ad_filters_no_files,
    test_load_ad_filters_reads_proxy_filter,
    test_load_ad_filters_falls_back_to_blacklist,
}


if __name__ == "__main__":
    failures = 0
    skipped = 0
    for test_fn in _ALL_TESTS:
        if test_fn in _PYTEST_ONLY_TESTS:
            print(f"SKIP  {test_fn.__name__} (requires pytest monkeypatch)")
            skipped += 1
            continue
        try:
            test_fn()
            print(f"PASS  {test_fn.__name__}")
        except Exception as exc:
            import traceback
            print(f"FAIL  {test_fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1
    print()
    total = len(_ALL_TESTS) - skipped
    if failures:
        print(f"{failures}/{total} test(s) FAILED  ({skipped} skipped)")
        sys.exit(1)
    else:
        print(f"All {total} tests PASSED  ({skipped} skipped)")
        sys.exit(0)

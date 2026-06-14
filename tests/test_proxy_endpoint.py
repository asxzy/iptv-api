"""
Flask endpoint tests for GET /proxy.

Run via:
    python -m pytest tests/test_proxy_endpoint.py
    python tests/test_proxy_endpoint.py

All upstream network calls are monkeypatched — no real network required.
"""
import sys
import os
import unittest.mock as mock

# Insert repo root so imports work whether run from tests/ or repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest

# ---------------------------------------------------------------------------
# Fixture / helpers
# ---------------------------------------------------------------------------

# Canned HLS content used across tests
_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
    "720p.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080\n"
    "1080p.m3u8\n"
)

_MEDIA_CLEAN = (
    "#EXTM3U\n"
    "#EXT-X-TARGETDURATION:10\n"
    "#EXT-X-VERSION:3\n"
    "#EXTINF:10.0,\n"
    "http://cdn.example.com/seg1.ts\n"
    "#EXTINF:10.0,\n"
    "http://cdn.example.com/seg2.ts\n"
    "#EXT-X-ENDLIST\n"
)

_MEDIA_WITH_AD = (
    "#EXTM3U\n"
    "#EXT-X-TARGETDURATION:10\n"
    "#EXT-X-VERSION:3\n"
    "#EXTINF:10.0,\n"
    "http://cdn.example.com/seg1.ts\n"
    "#EXTINF:10.0,\n"
    "http://adserver.example.com/ad/break.ts\n"
    "#EXTINF:10.0,\n"
    "http://cdn.example.com/seg2.ts\n"
    "#EXT-X-ENDLIST\n"
)

_BASE_URL = "http://upstream.example.com/live/stream.m3u8"


@pytest.fixture
def client():
    """Return a Flask test client with test mode on."""
    # Import app after path is set up
    from service.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _patch_fetch(chain, content):
    """Return a context manager that patches get_redirect_chain_content in service.app."""
    return mock.patch(
        "service.app.get_redirect_chain_content",
        return_value=(chain, content),
    )


def _patch_open_proxy(value):
    """Return a context manager that patches config.open_proxy via the class."""
    from utils.config import ConfigManager
    return mock.patch.object(
        ConfigManager,
        "open_proxy",
        new_callable=mock.PropertyMock,
        return_value=value,
    )


# ---------------------------------------------------------------------------
# Test: /proxy with master playlist — variant URIs rewritten to /proxy?url=...
# ---------------------------------------------------------------------------

def test_proxy_master_rewrites_variants(client):
    chain = [_BASE_URL]
    with _patch_fetch(chain, _MASTER):
        resp = client.get("/proxy?url=http%3A%2F%2Fupstream.example.com%2Flive%2Fstream.m3u8")

    assert resp.status_code == 200
    assert "application/vnd.apple.mpegurl" in resp.content_type
    body = resp.data.decode("utf-8")
    # Both variant URIs should be rewritten to /proxy?url=...
    assert "/proxy?url=" in body, f"Expected rewritten variant URIs in:\n{body}"
    # The bare relative URIs must not appear as standalone lines
    # (they are encoded inside the proxy URL query string instead)
    lines = body.splitlines()
    bare_variant_lines = [l for l in lines if l in ("720p.m3u8", "1080p.m3u8")]
    assert not bare_variant_lines, (
        f"Bare variant URI lines found — should be rewritten:\n{body}"
    )
    # The encoded absolute URLs should appear inside the proxy query strings
    assert "720p.m3u8" in body  # encoded inside /proxy?url=...720p.m3u8
    assert "1080p.m3u8" in body


# ---------------------------------------------------------------------------
# Test: /proxy with media playlist — ad segment dropped, clean segments kept
# ---------------------------------------------------------------------------

def test_proxy_media_filters_ads(client):
    from service.proxy import AdFilter
    chain = [_BASE_URL]
    # Patch fetch and use an ad filter that recognises /ad/ as ad keyword
    with _patch_fetch(chain, _MEDIA_WITH_AD):
        with mock.patch("service.app._ad_filter", AdFilter(keywords=["/ad/"])):
            resp = client.get("/proxy?url=http%3A%2F%2Fupstream.example.com%2Flive%2Fstream.m3u8")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # The ad segment URI must be absent
    assert "adserver.example.com/ad/break.ts" not in body, (
        f"Ad segment should have been stripped:\n{body}"
    )
    # Clean segments must be present (as absolute URLs)
    assert "cdn.example.com/seg1.ts" in body
    assert "cdn.example.com/seg2.ts" in body


# ---------------------------------------------------------------------------
# Test: /proxy missing url param → 400
# ---------------------------------------------------------------------------

def test_proxy_missing_url_returns_400(client):
    resp = client.get("/proxy")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data is not None
    assert "error" in data


def test_proxy_blank_url_returns_400(client):
    resp = client.get("/proxy?url=")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data is not None
    assert "error" in data


# ---------------------------------------------------------------------------
# Test: upstream returns empty / no content → 502
# ---------------------------------------------------------------------------

def test_proxy_upstream_empty_returns_502(client):
    with _patch_fetch([], ""):
        resp = client.get("/proxy?url=http%3A%2F%2Fupstream.example.com%2Flive%2Fstream.m3u8")
    assert resp.status_code == 502
    data = resp.get_json()
    assert data is not None
    assert "error" in data


def test_proxy_upstream_whitespace_returns_502(client):
    with _patch_fetch(["http://upstream.example.com/live/stream.m3u8"], "   \n  "):
        resp = client.get("/proxy?url=http%3A%2F%2Fupstream.example.com%2Flive%2Fstream.m3u8")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Test: open_proxy = False → 404
# ---------------------------------------------------------------------------

def test_proxy_disabled_returns_404(client):
    from utils.config import ConfigManager
    with mock.patch.object(ConfigManager, "open_proxy",
                           new_callable=mock.PropertyMock, return_value=False):
        resp = client.get("/proxy?url=http%3A%2F%2Fupstream.example.com%2Flive%2Fstream.m3u8")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data is not None
    assert "error" in data


# ---------------------------------------------------------------------------
# Test: /proxy uses chain[-1] as base_url for relative URI resolution
# ---------------------------------------------------------------------------

def test_proxy_resolves_relative_uris_against_redirect_target(client):
    """When there's a redirect, relative variant URIs are resolved against the
    final redirect target (chain[-1]), not the original URL."""
    original = "http://origin.example.com/live.m3u8"
    redirected = "http://cdn.example.com/live/index.m3u8"
    chain = [original, redirected]
    # Variant is a relative URI — should be resolved against 'redirected'
    with _patch_fetch(chain, _MASTER):
        resp = client.get(f"/proxy?url={original}")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # The encoded proxy URLs should contain the cdn.example.com base
    assert "cdn.example.com" in body


# ---------------------------------------------------------------------------
# Standalone runner (mirrors tests/test_nested_blacklist.py pattern)
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_proxy_master_rewrites_variants,
    test_proxy_media_filters_ads,
    test_proxy_missing_url_returns_400,
    test_proxy_blank_url_returns_400,
    test_proxy_upstream_empty_returns_502,
    test_proxy_upstream_whitespace_returns_502,
    test_proxy_disabled_returns_404,
    test_proxy_resolves_relative_uris_against_redirect_target,
]

if __name__ == "__main__":
    from service.app import app
    app.config["TESTING"] = True

    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            with app.test_client() as c:
                test_fn(c)
            print(f"PASS  {test_fn.__name__}")
        except Exception as exc:
            import traceback
            print(f"FAIL  {test_fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1

    print()
    if failures:
        print(f"{failures}/{len(_ALL_TESTS)} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {len(_ALL_TESTS)} tests PASSED")
        sys.exit(0)

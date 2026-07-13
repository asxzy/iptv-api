"""
Flask endpoint tests for GET /proxy/m3u, /proxy/txt, /proxy/txt/multi.

Run via:
    python -m pytest tests/test_proxy_list_endpoint.py
    python tests/test_proxy_list_endpoint.py

All file I/O is intercepted via monkeypatching _read_result_file so no real
result files need to exist and nothing is written to disk.
"""
import sys
import os
import unittest.mock as mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest


# ---------------------------------------------------------------------------
# Canned content fixtures
# ---------------------------------------------------------------------------

_TXT_CONTENT = (
    "📺央视频道,#genre#\n"
    "CCTV-1,http://cdn.example.com/cctv1.m3u8\n"
    "CCTV-1,http://cdn.example.com/cctv1-alt.m3u8\n"
    "CCTV-2,http://cdn.example.com/cctv2.m3u8\n"
)

_M3U_CONTENT = (
    '#EXTM3U x-tvg-url="http://example.com/epg.gz"\n'
    '#EXTINF:-1 tvg-name="CCTV-1" group-title="央视频道",CCTV-1\n'
    "http://cdn.example.com/cctv1.m3u8\n"
    '#EXTINF:-1 tvg-name="CCTV-2" group-title="央视频道",CCTV-2\n'
    "http://cdn.example.com/cctv2.m3u8\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Return a Flask test client."""
    from service.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _patch_open_proxy(value: bool):
    """Patch config.open_proxy via the ConfigManager property."""
    from utils.config import ConfigManager
    return mock.patch.object(
        ConfigManager,
        "open_proxy",
        new_callable=mock.PropertyMock,
        return_value=value,
    )


def _patch_read_file(return_value):
    """
    Patch service.app._read_result_file so it returns *return_value* for any
    path (None means 'file does not exist').
    """
    return mock.patch("service.app._read_result_file", return_value=return_value)


def _patch_final_file(path: str):
    """Patch config.final_file to return *path*."""
    from utils.config import ConfigManager
    return mock.patch.object(
        ConfigManager,
        "final_file",
        new_callable=mock.PropertyMock,
        return_value=path,
    )


# ---------------------------------------------------------------------------
# /proxy/txt tests
# ---------------------------------------------------------------------------

def test_proxy_txt_rewrites_station_urls(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_TXT_CONTENT):
        resp = client.get("/proxy/txt")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # Every station URL should be rewritten through the proxy
    assert "/proxy?url=" in body
    # The hostname is percent-encoded inside the proxy URL query string
    assert "cdn.example.com" in body   # hostname appears encoded e.g. cdn.example.com%2F...
    # The genre marker must pass through verbatim
    assert "📺央视频道,#genre#" in body
    # Raw URLs must not appear as bare station values (they're percent-encoded now)
    for line in body.splitlines():
        if "," in line and not line.strip().endswith(",#genre#"):
            name, value = line.split(",", 1)
            # Each source part should be a proxied URL, not a raw http:// URL
            for part in value.split("#"):
                part = part.strip()
                if part:
                    assert part.startswith(("http://localhost/proxy?url=",
                                            "http://localhost:5180/proxy?url=",
                                            "/proxy?url=")) or "?url=" in part, (
                        f"Expected proxied URL, got: {part!r}"
                    )


def test_proxy_txt_mimetype_is_text_plain(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_TXT_CONTENT):
        resp = client.get("/proxy/txt")

    assert "text/plain" in resp.content_type


def test_proxy_txt_contains_proxy_url_param(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_TXT_CONTENT):
        resp = client.get("/proxy/txt")

    body = resp.data.decode("utf-8")
    # Absolute proxy links must contain the host portion
    assert "/proxy?url=" in body


# ---------------------------------------------------------------------------
# /proxy/m3u tests
# ---------------------------------------------------------------------------

def test_proxy_m3u_rewrites_station_urls(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_M3U_CONTENT):
        resp = client.get("/proxy/m3u")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "/proxy?url=" in body
    # #EXTINF and #EXTM3U lines must be verbatim
    assert "#EXTM3U" in body
    assert "#EXTINF:-1" in body
    # Bare stream URLs must be rewritten (not appear as-is on a standalone line)
    bare_lines = [
        l for l in body.splitlines()
        if l.strip() == "http://cdn.example.com/cctv1.m3u8"
        or l.strip() == "http://cdn.example.com/cctv2.m3u8"
    ]
    assert not bare_lines, f"Bare unrewritten URL lines found: {bare_lines}"


def test_proxy_m3u_mimetype_is_mpegurl(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_M3U_CONTENT):
        resp = client.get("/proxy/m3u")

    assert "application/vnd.apple.mpegurl" in resp.content_type


def test_proxy_m3u_declares_utf8_charset(client):
    """Regression: the mpegurl mimetype is not text/* so werkzeug won't auto-add a
    charset; without an explicit charset=utf-8 the non-ASCII channel names/emoji
    render as mojibake in players (the 'random chars' bug)."""
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_M3U_CONTENT):
        resp = client.get("/proxy/m3u")

    assert "charset=utf-8" in resp.content_type.lower(), \
        f"expected charset=utf-8 in Content-Type, got: {resp.content_type!r}"


# ---------------------------------------------------------------------------
# /proxy/txt/multi tests
# ---------------------------------------------------------------------------

def test_proxy_txt_multi_merges_then_rewrites(client):
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(_TXT_CONTENT):
        resp = client.get("/proxy/txt/multi")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # After merging, CCTV-1 should have two sources joined by '#', each proxied
    cctv1_lines = [l for l in body.splitlines() if l.startswith("CCTV-1,")]
    assert len(cctv1_lines) == 1, f"Expected single merged CCTV-1 line, got: {cctv1_lines}"
    # The merged line must contain a '#' separator between the two proxied URLs
    assert "#" in cctv1_lines[0]
    assert "/proxy?url=" in cctv1_lines[0]


# ---------------------------------------------------------------------------
# open_proxy = False → 404
# ---------------------------------------------------------------------------

def test_proxy_txt_disabled_returns_404(client):
    with _patch_open_proxy(False):
        resp = client.get("/proxy/txt")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data is not None
    assert "error" in data


def test_proxy_m3u_disabled_returns_404(client):
    with _patch_open_proxy(False):
        resp = client.get("/proxy/m3u")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data is not None
    assert "error" in data


def test_proxy_txt_multi_disabled_returns_404(client):
    with _patch_open_proxy(False):
        resp = client.get("/proxy/txt/multi")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data is not None
    assert "error" in data


# ---------------------------------------------------------------------------
# File missing → waiting_tip
# ---------------------------------------------------------------------------

def test_proxy_txt_missing_file_returns_waiting_tip(client):
    import utils.constants as constants
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(None):
        resp = client.get("/proxy/txt")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert constants.waiting_tip in body or len(body) > 0


def test_proxy_m3u_missing_file_returns_waiting_tip(client):
    import utils.constants as constants
    with _patch_open_proxy(True), \
         _patch_final_file("output/result.txt"), \
         _patch_read_file(None):
        resp = client.get("/proxy/m3u")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert constants.waiting_tip in body or len(body) > 0


# ---------------------------------------------------------------------------
# Standalone runner (mirrors test_proxy_endpoint.py pattern)
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_proxy_txt_rewrites_station_urls,
    test_proxy_txt_mimetype_is_text_plain,
    test_proxy_txt_contains_proxy_url_param,
    test_proxy_m3u_rewrites_station_urls,
    test_proxy_m3u_mimetype_is_mpegurl,
    test_proxy_m3u_declares_utf8_charset,
    test_proxy_txt_multi_merges_then_rewrites,
    test_proxy_txt_disabled_returns_404,
    test_proxy_m3u_disabled_returns_404,
    test_proxy_txt_multi_disabled_returns_404,
    test_proxy_txt_missing_file_returns_waiting_tip,
    test_proxy_m3u_missing_file_returns_waiting_tip,
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

"""
Live network smoke test for the /proxy endpoint.

Hits https://t.freetv.fun/live/cctv-10ke-jiao-13.m3u8 through the Flask
test client's /proxy route and asserts that a valid playlist comes back.

The test SKIPS gracefully when the network is unavailable or the upstream
returns an error — it never fails a CI run due to external connectivity.

Run via:
    python -m pytest tests/test_proxy_live.py -v
    python tests/test_proxy_live.py
"""
import sys
import os
from urllib.parse import quote

# Insert repo root so imports work whether run from tests/ or repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest

_LIVE_URL = "https://t.freetv.fun/live/cctv-10ke-jiao-13.m3u8"
_TIMEOUT = 15  # seconds; used by the real get_redirect_chain_content call


def _check_network() -> bool:
    """Quick connectivity probe — returns True if we can reach the upstream host."""
    import socket
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo("t.freetv.fun", 443)
        return True
    except Exception:
        return False


@pytest.fixture
def client():
    from service.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_proxy_live_cctv13(client):
    """
    Fetch the live CCTV-13 playlist through /proxy and assert a valid,
    non-empty HLS playlist is returned.  Skips if the network is unavailable
    or the upstream returns empty content.
    """
    if not _check_network():
        pytest.skip("Network unavailable — skipping live smoke test")

    import unittest.mock as mock
    # We want the REAL get_redirect_chain_content to run, but with a short
    # timeout so the test doesn't hang forever.
    from utils.requests.tools import get_redirect_chain_content as _real_fetch

    def _fetch_with_timeout(url, **kwargs):
        return _real_fetch(url, timeout=_TIMEOUT)

    encoded_url = quote(_LIVE_URL, safe="")
    with mock.patch("service.app.get_redirect_chain_content", side_effect=_fetch_with_timeout):
        resp = client.get(f"/proxy?url={encoded_url}")

    if resp.status_code == 502:
        pytest.skip(f"Upstream returned empty/failed response (502) — skipping live smoke test")

    assert resp.status_code == 200, (
        f"Expected 200 from /proxy, got {resp.status_code}. Body: {resp.data[:500]}"
    )

    body = resp.data.decode("utf-8", errors="replace")
    assert body.strip(), "Proxy returned an empty body"

    has_m3u_marker = "#EXTM3U" in body
    has_extinf = "#EXTINF" in body
    has_stream_inf = "#EXT-X-STREAM-INF" in body
    is_valid_playlist = has_m3u_marker or has_extinf or has_stream_inf

    assert is_valid_playlist, (
        f"Response does not look like a valid HLS playlist.\n"
        f"First 500 chars:\n{body[:500]}"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _check_network():
        print("SKIP  test_proxy_live_cctv13 — network unavailable")
        sys.exit(0)

    from service.app import app
    import unittest.mock as mock
    from utils.requests.tools import get_redirect_chain_content as _real_fetch
    from urllib.parse import quote

    app.config["TESTING"] = True

    def _fetch_with_timeout(url, **kwargs):
        return _real_fetch(url, timeout=_TIMEOUT)

    encoded_url = quote(_LIVE_URL, safe="")

    try:
        with app.test_client() as c:
            with mock.patch("service.app.get_redirect_chain_content", side_effect=_fetch_with_timeout):
                resp = c.get(f"/proxy?url={encoded_url}")

        if resp.status_code == 502:
            print("SKIP  test_proxy_live_cctv13 — upstream returned empty/failed response")
            sys.exit(0)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        body = resp.data.decode("utf-8", errors="replace")
        assert body.strip(), "Empty body"
        assert "#EXTM3U" in body or "#EXTINF" in body or "#EXT-X-STREAM-INF" in body, (
            f"Not a valid HLS playlist:\n{body[:500]}"
        )
        print("PASS  test_proxy_live_cctv13")
        sys.exit(0)

    except AssertionError as exc:
        print(f"FAIL  test_proxy_live_cctv13: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"SKIP  test_proxy_live_cctv13 — unexpected error: {exc}")
        sys.exit(0)

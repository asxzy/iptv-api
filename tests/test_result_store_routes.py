import sys
import os
import unittest.mock as mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest
from utils.result_store import result_store


def setup_function():
    result_store.clear()


_SAMPLE_TXT = "CCTV1,http://example.com/1.ts\nCCTV2,http://example.com/2.ts"
_SAMPLE_M3U = (
    '#EXTM3U\n'
    '#EXTINF:-1 group-title="Sports",CCTV1\n'
    'http://example.com/1.ts\n'
    '#EXTINF:-1 group-title="Sports",CCTV2\n'
    'http://example.com/2.ts\n'
)


@pytest.fixture
def client():
    from service.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_returns_store_content_when_configured_path_populated(client):
    from utils.config import config
    final = config.final_file
    txt_path = os.path.splitext(final)[0] + ".txt"
    result_store.store(txt_path, _SAMPLE_TXT)
    m3u_path = os.path.splitext(final)[0] + ".m3u"
    result_store.store(m3u_path, _SAMPLE_M3U)
    with mock.patch("os.path.exists", return_value=False):
        resp = client.get("/")
    assert resp.status_code == 200
    assert "#EXTM3U" in resp.get_data(as_text=True)


def test_txt_endpoint_returns_store_content(client):
    from utils.config import config
    txt_path = os.path.splitext(config.final_file)[0] + ".txt"
    result_store.store(txt_path, _SAMPLE_TXT)
    with mock.patch("os.path.exists", return_value=False):
        resp = client.get("/txt")
    assert resp.status_code == 200
    assert "CCTV1" in resp.get_data(as_text=True)


def test_m3u_endpoint_returns_store_content(client):
    from utils.config import config
    m3u_path = os.path.splitext(config.final_file)[0] + ".m3u"
    result_store.store(m3u_path, _SAMPLE_M3U)
    with mock.patch("os.path.exists", return_value=False):
        resp = client.get("/m3u")
    assert resp.status_code == 200
    assert "#EXTM3U" in resp.get_data(as_text=True)


def test_endpoint_falls_back_to_file_when_store_empty(client):
    from utils.config import config
    txt_path = os.path.splitext(config.final_file)[0] + ".txt"
    assert result_store.get(txt_path) is None
    with mock.patch("os.path.exists", return_value=True):
        with mock.patch("builtins.open", mock.mock_open(read_data=_SAMPLE_TXT)):
            resp = client.get("/txt")
    assert resp.status_code == 200
    assert "CCTV1" in resp.get_data(as_text=True)


def test_endpoint_returns_waiting_tip_when_no_store_and_no_file(client):
    with mock.patch("os.path.exists", return_value=False):
        resp = client.get("/txt")
    from utils.constants import waiting_tip
    assert waiting_tip in resp.get_data(as_text=True)


def test_different_endpoints_independent(client):
    from utils.config import config
    txt_path = os.path.splitext(config.final_file)[0] + ".txt"
    m3u_path = os.path.splitext(config.final_file)[0] + ".m3u"

    result_store.store(txt_path, _SAMPLE_TXT)
    result_store.store(m3u_path, _SAMPLE_M3U)

    with mock.patch("os.path.exists", return_value=False):
        txt_resp = client.get("/txt")
        m3u_resp = client.get("/m3u")

    assert "CCTV1" in txt_resp.get_data(as_text=True)
    assert "#EXTM3U" in m3u_resp.get_data(as_text=True)

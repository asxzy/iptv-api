"""
Tests for utils.result_store.

Run via:
    python -m pytest tests/test_result_store.py -v
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import threading

from utils.result_store import result_store, _ResultStore


def setup_function():
    result_store.clear()


def test_store_starts_empty():
    assert result_store.get("output/result.txt") is None
    assert result_store.get("output/result.m3u") is None


def test_get_data_starts_none():
    assert result_store.get_data() is None


def test_store_and_retrieve():
    content = "CCTV1,http://example.com/1.ts\nCCTV2,http://example.com/2.ts"
    result_store.store("output/result.txt", content)
    assert result_store.get("output/result.txt") == content


def test_store_overwrite():
    result_store.store("output/result.txt", "old content")
    result_store.store("output/result.txt", "new content")
    assert result_store.get("output/result.txt") == "new content"


def test_store_multiple_paths_independent():
    txt_content = "CCTV1,http://example.com/1.ts"
    m3u_content = "#EXTM3U\n#EXTINF:-1,CCTV1\nhttp://example.com/1.ts"
    result_store.store("output/result.txt", txt_content)
    result_store.store("output/result.m3u", m3u_content)
    assert result_store.get("output/result.txt") == txt_content
    assert result_store.get("output/result.m3u") == m3u_content


def test_store_and_retrieve_data():
    data = {"Sports": {"CCTV1": [{"url": "http://example.com/1.ts"}]}}
    result_store.store_data(data)
    assert result_store.get_data() == data


def test_store_data_none():
    result_store.store_data({"key": "val"})
    result_store.store_data(None)
    assert result_store.get_data() is None


def test_clear_empties_both():
    result_store.store("output/result.txt", "some content")
    result_store.store_data({"a": "b"})
    result_store.clear()
    assert result_store.get("output/result.txt") is None
    assert result_store.get_data() is None


def test_clear_from_empty_no_error():
    result_store.clear()
    result_store.clear()


def test_singleton_identity():
    from utils.result_store import result_store as rs2
    assert result_store is rs2


def test_thread_safety():
    errors = []
    store = _ResultStore()

    def writer(path, content):
        try:
            for _ in range(200):
                store.store(path, content)
        except Exception as e:
            errors.append(e)

    def reader(path):
        try:
            for _ in range(200):
                store.get(path)
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(4):
        t = threading.Thread(target=writer, args=(f"p{i}", f"content{i}"))
        threads.append(t)
        t = threading.Thread(target=reader, args=(f"p{i}",))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread safety violations: {errors}"
    for i in range(4):
        assert store.get(f"p{i}") == f"content{i}"


def test_concurrent_store_data():
    store = _ResultStore()
    events = []

    def setter(n):
        try:
            for i in range(100):
                store.store_data({"source": n, "seq": i})
        except Exception as e:
            events.append(("error", n, e))

    threads = [threading.Thread(target=setter, args=(chr(65 + i),)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not events, f"Errors: {events}"
    last = store.get_data()
    assert last is not None
    assert "source" in last
    assert "seq" in last

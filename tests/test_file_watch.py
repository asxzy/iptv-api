"""
Tests for the auto-reloading file cache and the shared blacklist / ad-filter
providers built on it.

The point of the feature: edit blacklist.txt / proxy_ad_filter.txt and have
live readers pick it up *without restarting the process*.

Run via:
    python -m pytest tests/test_file_watch.py
"""
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils import file_watch
from service import proxy as proxy_mod


@pytest.fixture(autouse=True)
def _clear_cache():
    file_watch.invalidate()
    yield
    file_watch.invalidate()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# --- file_watch core -------------------------------------------------------

def test_loader_runs_once_when_file_unchanged(tmp_path):
    f = tmp_path / "data.txt"
    _write(f, "one")
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return f.read_text()

    assert file_watch.cached_from_files("k", [str(f)], loader) == "one"
    assert file_watch.cached_from_files("k", [str(f)], loader) == "one"
    assert calls["n"] == 1, "loader should not re-run while the file is unchanged"


def test_loader_reruns_when_file_changes(tmp_path):
    f = tmp_path / "data.txt"
    _write(f, "one")
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return f.read_text()

    assert file_watch.cached_from_files("k", [str(f)], loader) == "one"
    _write(f, "two-longer")  # size differs => signature changes
    assert file_watch.cached_from_files("k", [str(f)], loader) == "two-longer"
    assert calls["n"] == 2, "loader should re-run after the file changes"


def test_hash_content_detects_same_size_same_mtime_edit(tmp_path):
    f = tmp_path / "data.txt"
    _write(f, "old1")  # 4 bytes
    st = os.stat(f)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return f.read_text()

    assert file_watch.cached_from_files("k", [str(f)], loader, hash_content=True) == "old1"

    # Same-size edit, and reset mtime to the original so (mtime_ns, size) match —
    # simulates coarse mtime granularity. Only a content hash catches this.
    _write(f, "new2")  # also 4 bytes
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))

    assert file_watch.cached_from_files("k", [str(f)], loader, hash_content=True) == "new2"
    assert calls["n"] == 2, "content hash should detect a same-size, same-mtime edit"


def test_stat_only_misses_same_size_same_mtime_edit(tmp_path):
    """Documents why hash_content is needed: the default stat-only mode misses it."""
    f = tmp_path / "data.txt"
    _write(f, "old1")
    st = os.stat(f)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return f.read_text()

    assert file_watch.cached_from_files("k", [str(f)], loader) == "old1"
    _write(f, "new2")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert file_watch.cached_from_files("k", [str(f)], loader) == "old1"  # stale, as expected
    assert calls["n"] == 1


def test_appearing_file_triggers_reload(tmp_path):
    missing = tmp_path / "later.txt"
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return missing.read_text() if missing.exists() else "absent"

    assert file_watch.cached_from_files("k", [str(missing)], loader) == "absent"
    _write(missing, "now-here")
    assert file_watch.cached_from_files("k", [str(missing)], loader) == "now-here"
    assert calls["n"] == 2


# --- proxy ad filter hot-reload -------------------------------------------

def test_get_ad_filter_reloads_on_blacklist_edit(tmp_path, monkeypatch):
    ad_filter_path = tmp_path / "proxy_ad_filter.txt"
    blacklist_path = tmp_path / "blacklist.txt"
    _write(blacklist_path, "ads.example.com\n")

    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path", str(ad_filter_path), raising=False)
    monkeypatch.setattr(proxy_mod.constants, "blacklist_path", str(blacklist_path), raising=False)

    af = proxy_mod.get_ad_filter()
    assert af.matches("http://ads.example.com/seg.ts")
    assert not af.matches("http://cdn.example.com/seg.ts")

    # Edit the blacklist — no restart, no cache reset.
    _write(blacklist_path, "ads.example.com\nspots.example.net\n")

    af2 = proxy_mod.get_ad_filter()
    assert af2.matches("http://spots.example.net/seg.ts"), "new keyword should take effect live"


def test_get_ad_filter_honors_user_blacklist_override(tmp_path, monkeypatch):
    """The proxy must read the user_ override, same file the scan reads — not the base file."""
    blacklist_path = tmp_path / "blacklist.txt"
    user_blacklist_path = tmp_path / "user_blacklist.txt"
    ad_filter_path = tmp_path / "proxy_ad_filter.txt"  # absent → falls back to blacklist
    _write(blacklist_path, "base-only.example.com\n")
    _write(user_blacklist_path, "override.example.com\n")

    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path", str(ad_filter_path), raising=False)
    monkeypatch.setattr(proxy_mod.constants, "blacklist_path", str(blacklist_path), raising=False)

    af = proxy_mod.get_ad_filter()
    assert af.matches("http://override.example.com/seg.ts"), "should read user_blacklist.txt"
    assert not af.matches("http://base-only.example.com/seg.ts"), "base file must be shadowed by user_ override"


def test_get_ad_filter_prefers_proxy_ad_filter_with_regex(tmp_path, monkeypatch):
    ad_filter_path = tmp_path / "proxy_ad_filter.txt"
    blacklist_path = tmp_path / "blacklist.txt"
    _write(blacklist_path, "fromblacklist\n")
    _write(ad_filter_path, "# ads\nre:/ad-\\d+/\n")

    monkeypatch.setattr(proxy_mod.constants, "proxy_ad_filter_path", str(ad_filter_path), raising=False)
    monkeypatch.setattr(proxy_mod.constants, "blacklist_path", str(blacklist_path), raising=False)

    af = proxy_mod.get_ad_filter()
    assert af.matches("http://x/ad-12/seg.ts")
    # proxy_ad_filter.txt present => blacklist.txt is NOT used as fallback.
    assert not af.matches("http://x/fromblacklist/seg.ts")

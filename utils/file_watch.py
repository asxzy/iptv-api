# utils/file_watch.py
"""
Lazy, auto-reloading cache for file-backed config.

`cached_from_files(key, paths, loader)` runs `loader()` once and re-runs it only
when any of `paths` changes on disk (mtime-ns + size). This lets every reader
share one source of truth — e.g. config/blacklist.txt — and pick up edits
*without restarting the process*.

Each process keeps its own in-memory cache, so independent workers (gunicorn
web process, the scan process) each reload the next time they read after a
change. The file on disk is the shared state; the cache is just a per-process
fast path. Thread-safe via a double-checked lock; the per-access cost is one
os.stat per watched path.
"""
import hashlib
import os
import threading

_lock = threading.Lock()
_cache: dict = {}  # key -> (signature, value)


def _signature(paths, hash_content: bool) -> tuple:
    """
    A change-detection signature per file: (path, mtime_ns, size, content_hash).

    content_hash is None unless `hash_content` is set; hashing makes detection
    exact for small config files where coarse mtime granularity (e.g. Docker
    bind mounts) could otherwise miss a same-size edit within one mtime tick.
    Missing files contribute (path, None, None, None).
    """
    sig = []
    for p in paths:
        try:
            st = os.stat(p)
            digest = None
            if hash_content and st.st_size:
                with open(p, "rb") as fh:
                    digest = hashlib.blake2b(fh.read(), digest_size=16).hexdigest()
            sig.append((p, st.st_mtime_ns, st.st_size, digest))
        except OSError:
            sig.append((p, None, None, None))
    return tuple(sig)


def cached_from_files(key: str, paths: list, loader, hash_content: bool = False):
    """
    Return `loader()`, cached under `key` until any of `paths` changes on disk.

    `loader` takes no arguments and returns the parsed value; it is only called
    on first access and whenever a watched file changes. Pass `hash_content=True`
    for small files where exact content-change detection is worth a read per
    check (avoids missing same-size edits under coarse mtime granularity).
    """
    signature = _signature(paths, hash_content)
    cached = _cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with _lock:
        # Re-check under the lock: another thread may have just reloaded.
        cached = _cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        value = loader()
        _cache[key] = (signature, value)
        return value


def invalidate(key: str = None) -> None:
    """Drop a cached entry (or all) so the next access reloads. For tests / manual reload."""
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)

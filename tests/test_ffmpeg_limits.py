"""
Tests for the ffmpeg/ffprobe memory safeguards (fixes #1 + #2):

  #1  Every spawn carries INPUT_BOUND_ARGS (probesize / analyzeduration /
      rw_timeout) so a single process can't balloon while opening a stream.
  #2  All async spawns share one process-wide semaphore, so concurrent native
      processes (and their memory) stay bounded under load.

Run via:
    python -m pytest tests/test_ffmpeg_limits.py
"""
import asyncio
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.ffmpeg import limits
from utils.ffmpeg import probe as probe_mod
from utils.ffmpeg import ffmpeg as ffmpeg_mod
from utils.ffmpeg import deep_probe as deep_mod
from utils.ffmpeg.limits import INPUT_BOUND_ARGS


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """Each test gets a fresh, known semaphore; never leak one across tests."""
    limits._ffmpeg_semaphore = None
    yield
    limits._ffmpeg_semaphore = None


def _has_subsequence(args, sub):
    """True if `sub` appears as a contiguous run inside `args`."""
    n, m = len(args), len(sub)
    return any(list(args[i:i + m]) == list(sub) for i in range(n - m + 1))


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess that exits immediately."""

    def __init__(self, stdout=b"", stderr=b""):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = 0

        class _Stream:
            async def readline(_self):
                return b""

        self.stderr = _Stream()

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        self.returncode = 0
        return 0


def _capture_exec(captured, **proc_kwargs):
    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        return _FakeProc(**proc_kwargs)

    return fake_exec


# --- #1: bound args are present on every spawn -----------------------------

def test_probe_url_includes_input_bound_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _capture_exec(captured, stdout=b'{"streams": [], "format": {}}'),
    )
    asyncio.run(probe_mod.probe_url("http://example.test/stream.m3u8"))
    assert _has_subsequence(captured["args"], INPUT_BOUND_ARGS)


def test_resolution_ffprobe_includes_input_bound_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _capture_exec(captured, stdout=b'{"streams": []}'),
    )
    asyncio.run(probe_mod.get_resolution_ffprobe("http://example.test/stream.m3u8"))
    assert _has_subsequence(captured["args"], INPUT_BOUND_ARGS)


def test_ffmpeg_url_includes_input_bound_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _capture_exec(captured),
    )
    asyncio.run(ffmpeg_mod.ffmpeg_url("http://example.test/stream.m3u8", timeout=1))
    assert _has_subsequence(captured["args"], INPUT_BOUND_ARGS)


def test_keep_ratio_includes_input_bound_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _capture_exec(captured),
    )
    asyncio.run(deep_mod.measure_keep_ratio("http://example.test/stream.m3u8"))
    assert _has_subsequence(captured["args"], INPUT_BOUND_ARGS)


def test_upscale_ssim_includes_input_bound_args(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _capture_exec(captured),
    )
    asyncio.run(deep_mod.measure_upscale_ssim(
        "http://example.test/stream.m3u8", "1920x1080", "1280x720"
    ))
    assert _has_subsequence(captured["args"], INPUT_BOUND_ARGS)


# --- #2: the shared semaphore caps concurrent spawns -----------------------

def test_ffmpeg_semaphore_bounds_concurrency(monkeypatch):
    limits._ffmpeg_semaphore = asyncio.Semaphore(2)
    state = {"cur": 0, "max": 0}

    class _SlowProc:
        def __init__(self):
            self.returncode = None

        async def communicate(self):
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
            await asyncio.sleep(0.02)
            state["cur"] -= 1
            self.returncode = 0
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        return _SlowProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def run_many():
        await asyncio.gather(*[deep_mod._run(["ffmpeg"], 5) for _ in range(6)])

    asyncio.run(run_many())
    assert state["max"] <= 2, f"observed {state['max']} concurrent spawns, expected <= 2"

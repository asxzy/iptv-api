"""
Tests for the per-source atom pipeline — Phase 3a.

Each subscribe source is processed independently:
  fetch → nested blacklist → dedup (process_nested_dict) → speed test → aggregator
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import asyncio
from collections import defaultdict

import pytest

try:
    from updates.subscribe.request import process_subscribe_source
    _IMPORTS_OK = True
except ImportError:
    process_subscribe_source = None
    _IMPORTS_OK = False


# ---------------------------------------------------------------------------
# Fake aggregator
# ---------------------------------------------------------------------------

class FakeAggregator:
    def __init__(self):
        self.added: list[tuple] = []

    def add_item(self, cate, name, item, is_channel_last=False, is_last=False, is_valid=True):
        self.added.append((cate, name, item, is_channel_last, is_last, is_valid))

    def reset(self):
        self.added.clear()


# ---------------------------------------------------------------------------
# Fake speed test
# ---------------------------------------------------------------------------

async def fake_test_speed(data, **kwargs):
    on_task_complete = kwargs.get("on_task_complete")
    results = defaultdict(lambda: defaultdict(list))
    total_items = sum(len(v) for c in data.values() for v in c.values())
    completed = 0
    for cate, channel_obj in data.items():
        for name, info_list in channel_obj.items():
            for info in info_list:
                item = {**info, "speed": 5.0, "delay": 50}
                results[cate][name].append(item)
                completed += 1
                is_channel_last = completed >= len(info_list)
                is_last = completed >= total_items
                if on_task_complete:
                    on_task_complete(cate, name, item, is_channel_last, is_last, True)
    return dict(results)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="process_subscribe_source not yet implemented")


@pytest.mark.asyncio
async def test_single_source_fetch_blacklist_speed_and_push():
    """A single subscribe source: fetch -> blacklist -> speed test -> aggregator."""
    async def fake_fetch(src):
        return {
            "Sports": {
                "CCTV1": [{"url": "http://cdn.example/1.ts"}],
                "CCTV2": [{"url": "http://bad.example/x.ts"}],
            }
        }

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://src.example/list.txt",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
        blacklist=["bad.example"],
        blacklist_cache={},
    )

    assert result is not None
    assert "CCTV2" not in result.get("Sports", {})
    assert len(result["Sports"]["CCTV1"]) == 1
    assert result["Sports"]["CCTV1"][0]["speed"] == 5.0

    assert len(agg.added) == 1
    cate, name, item, *_ = agg.added[0]
    assert cate == "Sports"
    assert name == "CCTV1"


@pytest.mark.asyncio
async def test_empty_source_returns_none():
    """A source returning empty channels produces None."""
    async def fake_fetch(src):
        return {}

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://empty.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
    )
    assert result is None
    assert len(agg.added) == 0


@pytest.mark.asyncio
async def test_failed_fetch_does_not_crash():
    """Fetch exception -> caught gracefully, returns None."""
    async def fake_fetch(src):
        raise ConnectionError("Timeout")

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://failing.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
    )
    assert result is None
    assert len(agg.added) == 0


@pytest.mark.asyncio
async def test_all_blacklisted_returns_none():
    """All URLs blacklisted -> nothing to speed test -> None."""
    async def fake_fetch(src):
        return {"TV": {"Chan1": [{"url": "http://bad.example/x.ts"}]}}

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://all-bad.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
        blacklist=["bad.example"],
        blacklist_cache={},
    )
    assert result is None
    assert len(agg.added) == 0


@pytest.mark.asyncio
async def test_multiple_sources_concurrent():
    """Multiple sources processed concurrently via asyncio.gather."""
    async def fake_fetch(src):
        url = src if isinstance(src, str) else src.get("url", "")
        idx = url.split("source")[1][0]
        return {"Sports": {f"Chan{idx}": [{"url": f"http://cdn.example/chan{idx}.ts"}]}}

    agg = FakeAggregator()

    async def run_one(src_url):
        return await process_subscribe_source(
            source_entry=src_url,
            fetch_source=fake_fetch,
            test_speed_func=fake_test_speed,
            ipv6_support=True,
            aggregator_add=agg.add_item,
            blacklist=[],
        )

    sources = [f"http://source{i}.example/list.txt" for i in range(3)]
    results = await asyncio.gather(*[run_one(s) for s in sources])

    assert len(results) == 3
    non_none = [r for r in results if r]
    assert len(non_none) == 3
    assert len(agg.added) == 3


@pytest.mark.asyncio
async def test_whitelist_origin_skips_blacklist():
    """Whitelist origin entries are not removed by nested blacklist."""
    async def fake_fetch(src):
        return {
            "TV": {
                "Film1": [{"url": "http://bad.example/x.ts", "origin": "whitelist"}]
            }
        }

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://whitelisted.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
        blacklist=["bad.example"],
        blacklist_cache={},
        retain_origin=("whitelist", "hls"),
    )
    assert result is not None
    assert len(result["TV"]["Film1"]) == 1
    assert result["TV"]["Film1"][0]["url"] == "http://bad.example/x.ts"
    assert len(agg.added) == 1


@pytest.mark.asyncio
async def test_dedup_applied():
    """process_nested_dict dedup removes duplicate URLs before speed test."""
    async def fake_fetch(src):
        return {
            "TV": {
                "DupeChan": [
                    {"url": "http://cdn.example/stream1.ts"},
                    {"url": "http://cdn.example/stream1.ts"},
                    {"url": "http://cdn.example/stream2.ts"},
                ]
            }
        }

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://dupe.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
        blacklist=[],
    )
    assert result is not None
    assert len(result["TV"]["DupeChan"]) == 2


@pytest.mark.asyncio
async def test_progress_callback_invoked():
    """Progress callback receives fetch + blacklist + dedup + speed_test stages."""
    progress_log = []

    async def fake_fetch(src):
        return {"TV": {"Chan1": [{"url": "http://cdn.example/1.ts"}]}}

    agg = FakeAggregator()

    def progress(source_url, stage, pct):
        progress_log.append((source_url, stage, pct))

    await process_subscribe_source(
        source_entry="http://progress.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
        progress_callback=progress,
    )

    assert len(progress_log) >= 2
    stages = [s for _, s, _ in progress_log]
    assert "fetch" in stages
    assert "speed_test" in stages


@pytest.mark.asyncio
async def test_no_blacklist_skips_blacklist_step():
    """blacklist=None or [] skips the nested blacklist step entirely."""
    async def fake_fetch(src):
        return {"TV": {"Chan1": [{"url": "http://bad.example/x.ts"}]}}

    agg = FakeAggregator()
    result = await process_subscribe_source(
        source_entry="http://noblacklist.example/",
        fetch_source=fake_fetch,
        test_speed_func=fake_test_speed,
        ipv6_support=True,
        aggregator_add=agg.add_item,
    )
    # Without blacklist, bad.example is speed tested
    assert result is not None
    assert len(result["TV"]["Chan1"]) == 1

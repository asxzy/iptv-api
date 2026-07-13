import sys
import os
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import asyncio
import pytest

from utils.result_store import result_store


def setup_function():
    result_store.clear()


def _make_minimal_agg_data():
    return {
        "Sports": {
            "CCTV1": [
                {"url": "http://cdn.example/1.ts", "origin": "subscribe",
                 "host": "cdn.example"}
            ],
        }
    }


def _teardown_agg(agg):
    agg._stopped = True
    if agg._task and not agg._task.done():
        agg._task.cancel()
    debounce_task = getattr(agg, "_debounce_task", None)
    if debounce_task and not debounce_task.done():
        debounce_task.cancel()


@pytest.mark.asyncio
async def test_store_populated_after_atomic_write():
    result_store.clear()
    from utils.aggregator import ResultAggregator

    with patch("utils.aggregator.write_channel_to_file"):
        agg = ResultAggregator(
            base_data=_make_minimal_agg_data(),
            first_channel_name="CCTV1",
            ipv6_support=True,
            write_interval=999.0,
            result={},
        )

    try:
        agg.add_item(
            "Sports", "CCTV1",
            {"url": "http://cdn.example/1.ts", "speed": 5.0, "delay": 50},
            is_valid=True,
        )
        await agg.flush_once(force=True)
    finally:
        _teardown_agg(agg)

    stored = result_store.get_data()
    assert stored is not None
    assert "Sports" in stored
    assert "CCTV1" in stored["Sports"]


@pytest.mark.asyncio
async def test_force_flush_always_stores():
    result_store.clear()
    from utils.aggregator import ResultAggregator

    with patch("utils.aggregator.write_channel_to_file"):
        agg = ResultAggregator(
            base_data=_make_minimal_agg_data(),
            first_channel_name="CCTV1",
            ipv6_support=True,
            write_interval=999.0,
            result={},
        )

    try:
        agg.add_item(
            "Sports", "CCTV1",
            {"url": "http://cdn.example/1.ts", "speed": 5.0, "delay": 50},
            is_valid=True,
        )
        await agg.flush_once(force=True)
    finally:
        _teardown_agg(agg)

    stored = result_store.get_data()
    assert stored is not None
    assert "Sports" in stored


@pytest.mark.asyncio
async def test_flush_accumulates_items():
    result_store.clear()
    from utils.aggregator import ResultAggregator

    with patch("utils.aggregator.write_channel_to_file"):
        agg = ResultAggregator(
            base_data=_make_minimal_agg_data(),
            first_channel_name="CCTV1",
            ipv6_support=True,
            write_interval=999.0,
            result={},
        )

    try:
        agg.add_item(
            "Sports", "CCTV1",
            {"url": "http://cdn.example/1.ts", "speed": 5.0, "delay": 50},
            is_valid=True,
        )
        await agg.flush_once(force=True)
        assert len(result_store.get_data()["Sports"]["CCTV1"]) == 1

        agg.add_item(
            "Sports", "CCTV1",
            {"url": "http://cdn.example/2.ts", "speed": 3.0, "delay": 100},
            is_valid=True,
        )
        await agg.flush_once(force=True)
        assert len(result_store.get_data()["Sports"]["CCTV1"]) == 2
    finally:
        _teardown_agg(agg)

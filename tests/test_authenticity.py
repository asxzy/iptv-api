"""
Tests for anti-fake authenticity detection.

Run via:
    python -m pytest tests/test_authenticity.py
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.authenticity import lower_resolution_tier


def test_lower_resolution_tier_steps_down_one_tier():
    assert lower_resolution_tier("1920x1080") == "1280x720"
    assert lower_resolution_tier("1280x720") == "854x480"
    assert lower_resolution_tier("3840x2160") == "2560x1440"
    assert lower_resolution_tier("854x480") == "640x360"


def test_lower_resolution_tier_lowest_and_unknown_return_none():
    assert lower_resolution_tier("640x360") is None
    assert lower_resolution_tier(None) is None
    assert lower_resolution_tier("garbage") is None

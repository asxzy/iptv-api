"""
Tests for utils.scoring.

Run via:
    python -m pytest tests/test_scoring.py
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.scoring import resolution_score, NEUTRAL


def test_resolution_score_tiers():
    assert resolution_score("3840x2160") == 1.0
    assert resolution_score("1920x1080") == 0.7
    assert resolution_score("1280x720") == 0.5
    assert resolution_score("640x360") == 0.15


def test_resolution_score_unknown_is_neutral():
    assert resolution_score(None) == NEUTRAL
    assert resolution_score("") == NEUTRAL
    assert resolution_score("garbage") == NEUTRAL

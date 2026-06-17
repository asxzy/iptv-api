"""
v2/core - Atomic streaming architecture for IPTV-API.

Ensures the project root is on sys.path so v2 workers can import from the
original utils.* modules (channel, tools, config, etc.) for compatibility.
"""

import os
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# utils/blacklist.py
"""
Shared, auto-reloading blacklist provider.

Single source of truth for config/blacklist.txt (honoring a user_ override).
Any caller gets the current keyword list; the file is re-parsed only when it
changes on disk, so edits take effect without restarting the process.

Use get_blacklist() everywhere instead of reading the file directly, so the
scan, the proxy, and any future consumer stay consistent and pick up live edits.
"""
import utils.constants as constants
from utils.file_watch import cached_from_files
from utils.tools import get_urls_from_file, resolve_config_path


def get_blacklist() -> list:
    """Current blacklist keywords from config/blacklist.txt, auto-reloaded on change."""
    logical = constants.blacklist_path
    # Watch the file that get_urls_from_file actually reads (resource_path +
    # user_ override) via the same resolver, so the signature can't drift.
    real = resolve_config_path(logical)
    return cached_from_files(
        "blacklist",
        [real],
        lambda: get_urls_from_file(logical, pattern_search=False),
        hash_content=True,
    )

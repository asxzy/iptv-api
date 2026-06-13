"""Debug a single station against the nested-blacklist pipeline.

Usage:
    python debug_station.py [URL]

Reproduces exactly what main.py does in _filter_nested_blacklist for one URL,
with verbose tracing at every recursion / fetch / keyword-check step.
"""
import sys

import utils.constants as constants
from utils.tools import get_urls_from_file, check_url_by_keywords
from utils.requests.tools import get_redirect_chain_content
from updates.subscribe import request as req

DEFAULT_URL = "https://stream1.freetv.fun/940832bf25a15c5e44fa17a9b73f6dba14850e25b94c67f665d781642a9f24c1.m3u8"


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    blacklist = get_urls_from_file(constants.blacklist_path, pattern_search=False)
    print(f"blacklist keywords ({len(blacklist)}): {blacklist}")
    print(f"target url: {url}\n")

    # Wrap the real fetch with tracing.
    def traced_fetch(u):
        print(f"  [FETCH] {u}")
        chain, content = get_redirect_chain_content(u, timeout=5)
        print(f"          chain={chain}")
        print(f"          content_len={len(content)}")
        if content:
            preview = content if len(content) < 400 else content[:400] + "..."
            print(f"          content:\n{preview}")
        return chain, content

    # Trace _parse_aggregation_children.
    orig_parse = req._parse_aggregation_children
    def traced_parse(content, base_url=""):
        children = orig_parse(content, base_url=base_url)
        print(f"  [PARSE] base={base_url} -> children={children}")
        return children
    req._parse_aggregation_children = traced_parse

    print("=== check_url_by_keywords on target url directly ===")
    print(check_url_by_keywords(url, blacklist))
    print()

    print("=== nested_url_blocked ===")
    blocked = req.nested_url_blocked(url, blacklist, traced_fetch)
    print(f"\nRESULT: blocked={blocked}")

    # --- Full filter_channel_data_nested_blacklist integration, as main.py calls it ---
    print("\n=== filter_channel_data_nested_blacklist (real fn) ===")
    channel_data = {"央视频道": {"CCTV5": [{"url": url, "origin": "subscribe", "headers": None}]}}

    def make_fetch(headers):
        def _fetch(u):
            return get_redirect_chain_content(u, timeout=5, headers_override=headers)
        return _fetch

    removed = req.filter_channel_data_nested_blacklist(
        channel_data, blacklist, make_fetch,
        retain_origin=("whitelist", "hls"), show_progress=False,
    )
    print(f"removed={removed}; remaining CCTV5 entries={channel_data['央视频道']['CCTV5']}")

    # --- Failure-mode probe: what happens when the fetch fails (timeout/error)? ---
    print("\n=== failure-mode: fetch returns ([], '') (simulated timeout) ===")
    channel_data2 = {"央视频道": {"CCTV5": [{"url": url, "origin": "subscribe", "headers": None}]}}
    def make_failing_fetch(headers):
        return lambda u: ([], "")
    removed2 = req.filter_channel_data_nested_blacklist(
        channel_data2, blacklist, make_failing_fetch,
        retain_origin=("whitelist", "hls"), show_progress=False,
    )
    print(f"removed={removed2}; remaining (KEPT means survives to speed test)={channel_data2['央视频道']['CCTV5']}")


if __name__ == "__main__":
    main()

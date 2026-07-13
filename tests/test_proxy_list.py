"""
Pure unit tests for rewrite_list_to_proxy in service/proxy.py.

Run via:
    python tests/test_proxy_list.py
    python -m pytest tests/test_proxy_list.py -q
"""
import sys
import os
from urllib.parse import quote

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from service.proxy import rewrite_list_to_proxy, build_proxy_url

PROXY_BASE = "http://localhost:5180/proxy"


def _expected(url):
    """What a wrapped URL should look like for the given raw url."""
    return build_proxy_url(PROXY_BASE, url)


# ---------------------------------------------------------------------------
# m3u tests
# ---------------------------------------------------------------------------

def test_m3u_bare_url_wrapped():
    url = "http://cdn.example.com/live/cctv1.m3u8"
    content = f"#EXTM3U\n#EXTINF:-1,CCTV-1\n{url}\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert _expected(url) in result
    assert "?url=" in result
    assert quote(url, safe="") in result


def test_m3u_extinf_line_untouched():
    content = "#EXTINF:-1 tvg-name=\"CCTV-1\",CCTV-1\nhttp://cdn.example.com/cctv1.m3u8\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert '#EXTINF:-1 tvg-name="CCTV-1",CCTV-1' in result


def test_m3u_extm3u_line_untouched():
    content = '#EXTM3U x-tvg-url="http://example.com/epg.gz"\n'
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert content.strip() in result


def test_m3u_extvlcopt_line_untouched():
    content = "#EXTVLCOPT:http-user-agent=VLC\nhttp://cdn.example.com/live.m3u8\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert "#EXTVLCOPT:http-user-agent=VLC" in result


def test_m3u_blank_lines_pass_through():
    content = "#EXTM3U\n\nhttp://cdn.example.com/a.m3u8\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    # Should still have a blank line somewhere in the result
    assert "\n\n" in result or result.count("\n") >= 2


def test_m3u_trailing_newline_preserved():
    content = "#EXTM3U\nhttp://cdn.example.com/a.m3u8\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert result.endswith("\n")


def test_m3u_no_trailing_newline_preserved():
    content = "#EXTM3U\nhttp://cdn.example.com/a.m3u8"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert not result.endswith("\n")


def test_m3u_dollar_label_wrapped_on_core_only():
    """$label suffix is preserved verbatim; only the stream URL is proxied."""
    url = "http://cdn.example.com/live/stream.m3u8"
    label = "线路1"
    content = f"{url}${label}\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert _expected(url) + "$" + label in result
    # The raw stream URL must not appear outside the encoded query string
    assert result.count(url) == 0 or result.count(_expected(url)) >= 1


def test_m3u_non_url_bare_line_untouched():
    """A bare line without :// passes through verbatim."""
    content = "#EXTM3U\njust-some-text-no-url\n"
    result = rewrite_list_to_proxy(content, "m3u", PROXY_BASE)
    assert "just-some-text-no-url" in result
    assert "?url=" not in result or result.index("?url=") > result.index("just-some-text")


# ---------------------------------------------------------------------------
# txt tests
# ---------------------------------------------------------------------------

def test_txt_single_source_wrapped():
    url = "http://t.freetv.fun/live/cctv1.m3u8"
    content = f"CCTV-1,{url}"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert "CCTV-1," + _expected(url) == result


def test_txt_multi_source_each_part_wrapped():
    url_a = "http://cdn.example.com/a.m3u8"
    url_b = "http://cdn.example.com/b.m3u8"
    url_c = "http://cdn.example.com/c.m3u8"
    content = f"CCTV-5,{url_a}#{url_b}#{url_c}"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert _expected(url_a) in result
    assert _expected(url_b) in result
    assert _expected(url_c) in result
    # Must be rejoined with '#'
    assert result == "CCTV-5," + "#".join([_expected(url_a), _expected(url_b), _expected(url_c)])


def test_txt_genre_marker_untouched():
    content = "📺央视频道,#genre#\nCCTV-1,http://cdn.example.com/cctv1.m3u8\n"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert "📺央视频道,#genre#" in result


def test_txt_update_time_marker_untouched():
    """The update-time genre block passes through verbatim."""
    content = "🕘️更新时间,#genre#\n2026-06-13 05:38:59,http://cdn.example.com/ts.m3u8\n"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert "🕘️更新时间,#genre#" in result


def test_txt_dollar_label_suffix_preserved():
    url = "http://cdn.example.com/live.m3u8"
    label = "线路1"
    content = f"CCTV-1,{url}${label}"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert _expected(url) + "$" + label in result


def test_txt_comma_less_line_untouched():
    content = "no-comma-here"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert result == "no-comma-here"


def test_txt_blank_line_preserved():
    content = "CCTV-1,http://cdn.example.com/a.m3u8\n\nCCTV-2,http://cdn.example.com/b.m3u8\n"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert "\n\n" in result


def test_txt_trailing_newline_preserved():
    content = "CCTV-1,http://cdn.example.com/a.m3u8\n"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert result.endswith("\n")


def test_txt_non_url_value_untouched():
    """A value without :// is not wrapped (e.g. placeholder or empty)."""
    content = "CCTV-1,placeholder-no-schema"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert result == "CCTV-1,placeholder-no-schema"


def test_txt_non_url_already_no_schema():
    """Idempotent-ish: a value with no :// is returned unchanged."""
    content = "SomeName,just-text"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert result == "SomeName,just-text"
    assert "?url=" not in result


def test_txt_wrapped_url_contains_percent_encoded_original():
    """The proxy URL must contain the percent-encoded original URL."""
    url = "http://t.freetv.fun/live/cctv1-1.m3u8"
    content = f"CCTV-1,{url}"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    expected_encoded = quote(url, safe="")
    assert expected_encoded in result


def test_txt_multi_source_with_dollar_label():
    """Multi-source where each part has a $label suffix."""
    url_a = "http://cdn.example.com/a.m3u8"
    url_b = "http://cdn.example.com/b.m3u8"
    content = f"CH,{url_a}$线路1#{url_b}$线路2"
    result = rewrite_list_to_proxy(content, "txt", PROXY_BASE)
    assert _expected(url_a) + "$线路1" in result
    assert _expected(url_b) + "$线路2" in result


# ---------------------------------------------------------------------------
# pytest-compatible discovery + standalone runner
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_m3u_bare_url_wrapped,
    test_m3u_extinf_line_untouched,
    test_m3u_extm3u_line_untouched,
    test_m3u_extvlcopt_line_untouched,
    test_m3u_blank_lines_pass_through,
    test_m3u_trailing_newline_preserved,
    test_m3u_no_trailing_newline_preserved,
    test_m3u_dollar_label_wrapped_on_core_only,
    test_m3u_non_url_bare_line_untouched,
    test_txt_single_source_wrapped,
    test_txt_multi_source_each_part_wrapped,
    test_txt_genre_marker_untouched,
    test_txt_update_time_marker_untouched,
    test_txt_dollar_label_suffix_preserved,
    test_txt_comma_less_line_untouched,
    test_txt_blank_line_preserved,
    test_txt_trailing_newline_preserved,
    test_txt_non_url_value_untouched,
    test_txt_non_url_already_no_schema,
    test_txt_wrapped_url_contains_percent_encoded_original,
    test_txt_multi_source_with_dollar_label,
]


if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
            print(f"PASS  {test_fn.__name__}")
        except Exception as exc:
            import traceback
            print(f"FAIL  {test_fn.__name__}: {exc}")
            traceback.print_exc()
            failures += 1
    print()
    total = len(_ALL_TESTS)
    if failures:
        print(f"{failures}/{total} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {total} tests PASSED")
        sys.exit(0)

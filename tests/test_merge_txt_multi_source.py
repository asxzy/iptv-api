"""
Tests for merge_txt_multi_source: collapse consecutive same-station txt lines into a
single line whose sources are joined by '#' (URL1#URL2#URL3).

Run via:
    python -m pytest tests/test_merge_txt_multi_source.py
    python tests/test_merge_txt_multi_source.py
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.tools import merge_txt_multi_source


def test_single_source_unchanged():
    """A station with one source is emitted verbatim."""
    src = "CCTV1,http://a.m3u8"
    assert merge_txt_multi_source(src) == "CCTV1,http://a.m3u8"


def test_multiple_sources_joined_with_hash():
    """Consecutive lines with the same name merge into one '#'-joined line."""
    src = "CCTV5,http://a.m3u8\nCCTV5,http://b.m3u8\nCCTV5,http://c.m3u8"
    assert merge_txt_multi_source(src) == "CCTV5,http://a.m3u8#http://b.m3u8#http://c.m3u8"


def test_genre_marker_preserved_and_breaks_run():
    """'分类,#genre#' markers pass through untouched and separate categories."""
    src = (
        "央视频道,#genre#\n"
        "CCTV5,http://a.m3u8\n"
        "CCTV5,http://b.m3u8\n"
        "\n"
        "体育频道,#genre#\n"
        "CCTV5,http://c.m3u8"
    )
    expected = (
        "央视频道,#genre#\n"
        "CCTV5,http://a.m3u8#http://b.m3u8\n"
        "\n"
        "体育频道,#genre#\n"
        "CCTV5,http://c.m3u8"
    )
    assert merge_txt_multi_source(src) == expected


def test_blank_line_breaks_run():
    """A blank line separates runs even for the same name."""
    src = "A,http://1\nA,http://2\n\nA,http://3"
    assert merge_txt_multi_source(src) == "A,http://1#http://2\n\nA,http://3"


def test_different_names_not_merged():
    """Adjacent lines with different names stay on separate lines."""
    src = "A,http://1\nB,http://2\nA,http://3"
    assert merge_txt_multi_source(src) == "A,http://1\nB,http://2\nA,http://3"


def test_url_with_dollar_extra_info_preserved():
    """Only the first comma splits name/url, so '$extra_info' on the url survives."""
    src = "CCTV5,http://a.m3u8$线路1\nCCTV5,http://b.m3u8$线路2"
    assert merge_txt_multi_source(src) == "CCTV5,http://a.m3u8$线路1#http://b.m3u8$线路2"


def test_line_without_comma_passed_through():
    """A malformed line with no comma is preserved and breaks the run."""
    src = "A,http://1\nmalformed line\nA,http://2"
    assert merge_txt_multi_source(src) == "A,http://1\nmalformed line\nA,http://2"


def test_update_time_block_unaffected():
    """An update-time block (genre + single timestamp line) is emitted as-is."""
    src = "更新时间,#genre#\n2026-06-12 15:38:00,http://t.m3u8\n\n央视,#genre#\nCCTV1,http://a\nCCTV1,http://b"
    expected = "更新时间,#genre#\n2026-06-12 15:38:00,http://t.m3u8\n\n央视,#genre#\nCCTV1,http://a#http://b"
    assert merge_txt_multi_source(src) == expected


def test_empty_content():
    """Empty input returns empty output."""
    assert merge_txt_multi_source("") == ""


def test_trailing_newline_preserved():
    """Trailing newline structure is preserved."""
    src = "A,http://1\nA,http://2\n"
    assert merge_txt_multi_source(src) == "A,http://1#http://2\n"


_ALL_TESTS = [
    test_single_source_unchanged,
    test_multiple_sources_joined_with_hash,
    test_genre_marker_preserved_and_breaks_run,
    test_blank_line_breaks_run,
    test_different_names_not_merged,
    test_url_with_dollar_extra_info_preserved,
    test_line_without_comma_passed_through,
    test_update_time_block_unaffected,
    test_empty_content,
    test_trailing_newline_preserved,
]

if __name__ == "__main__":
    failures = 0
    for test_fn in _ALL_TESTS:
        try:
            test_fn()
            print(f"PASS  {test_fn.__name__}")
        except Exception as exc:
            print(f"FAIL  {test_fn.__name__}: {exc}")
            failures += 1
    print()
    if failures:
        print(f"{failures}/{len(_ALL_TESTS)} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {len(_ALL_TESTS)} tests PASSED")
        sys.exit(0)

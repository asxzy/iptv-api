"""
Self-contained tests for nested_url_blocked and related helpers.

Run via:
    python -m pytest tests/test_nested_blacklist.py
    python tests/test_nested_blacklist.py
"""
import sys
import os

# Insert repo root so imports work whether run from tests/ or repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from collections import defaultdict

from updates.subscribe.request import nested_url_blocked, filter_channel_data_nested_blacklist


# ---------------------------------------------------------------------------
# Fake fetch helper
# ---------------------------------------------------------------------------

class FakeFetch:
    """Redirect-aware fake fetcher matching the real fetch contract.

    __call__(url) -> (chain, content):
      - chain: [url, *redirect targets] following the `redirects` map (single hop each,
        chained, cycle-safe).
      - content: `content_map` entry for the FINAL url after redirects (else "").
    First positional arg is the content map, so existing FakeFetch({url: content}) keeps
    working. Counts calls per requested url.
    """

    def __init__(self, content_map=None, redirects=None):
        self._content = content_map or {}
        self._redirects = redirects or {}
        self.call_counts = defaultdict(int)

    def __call__(self, url):
        self.call_counts[url] += 1
        chain = [url]
        cur = url
        seen = {url}
        while cur in self._redirects:
            nxt = self._redirects[cur]
            chain.append(nxt)
            if nxt in seen:
                break
            seen.add(nxt)
            cur = nxt
        return chain, self._content.get(cur, "")

    def total_calls(self):
        return sum(self.call_counts.values())


BLACKLIST = ["/audio/", "bad.example"]

# ---------------------------------------------------------------------------
# m3u / txt content helpers
# ---------------------------------------------------------------------------

def make_m3u(*urls):
    """Build an aggregation m3u (no HLS markers) listing the given urls."""
    lines = ["#EXTM3U"]
    for i, url in enumerate(urls):
        lines.append(f"#EXTINF:-1 ,Channel{i}")
        lines.append(url)
    return "\n".join(lines)


def make_txt(*url_pairs):
    """Build txt content from (name, url) pairs."""
    return "\n".join(f"{name},{url}" for name, url in url_pairs)


def make_hls_master(*urls):
    """Build an HLS master playlist (contains #EXT-X-STREAM-INF → treated as leaf)."""
    lines = ["#EXTM3U"]
    for url in urls:
        lines.append("#EXT-X-STREAM-INF:BANDWIDTH=1000000")
        lines.append(url)
    return "\n".join(lines)


def make_hls_media(*segments):
    """Build an HLS media playlist (contains #EXT-X-TARGETDURATION → leaf)."""
    lines = [
        "#EXTM3U",
        "#EXT-X-TARGETDURATION:10",
        "#EXT-X-VERSION:3",
    ]
    for seg in segments:
        lines.append("#EXTINF:10.0,")
        lines.append(seg)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual test functions
# ---------------------------------------------------------------------------

def test_1_direct_leaf_hit():
    """Direct URL containing blacklisted keyword is blocked immediately."""
    fake = FakeFetch()
    result = nested_url_blocked("http://x/audio/live.ts", BLACKLIST, fake)
    assert result is True, "Expected True for direct blacklist match"
    assert fake.total_calls() == 0, "Should not fetch: direct hit, not a playlist url"


def test_2_clean_non_playlist_leaf():
    """Non-.m3u8 extension URL with no blacklist match returns False; not fetched."""
    fake = FakeFetch()
    result = nested_url_blocked("http://x/live.flv", BLACKLIST, fake)
    assert result is False, "Expected False for clean non-playlist leaf"
    assert fake.total_calls() == 0, "Should not fetch: not a playlist extension"


def test_3_nested_m3u8_all_children_clean():
    """Nested m3u8 where all children are clean → False; original URL kept."""
    content = make_m3u(
        "http://cdn.example.com/ch1/stream.ts",
        "http://cdn.example.com/ch2/stream.ts",
    )
    fake = FakeFetch({"http://agg.example.com/list.m3u8": content})
    result = nested_url_blocked("http://agg.example.com/list.m3u8", BLACKLIST, fake)
    assert result is False, "Expected False: all children are clean"


def test_4_nested_m3u8_one_child_blacklisted():
    """Nested m3u8 with one child matching blacklist → True (all-or-nothing)."""
    content = make_m3u(
        "http://cdn.example.com/ch1/stream.ts",
        "http://bad.example.com/ch2/stream.ts",
    )
    fake = FakeFetch({"http://agg.example.com/list.m3u8": content})
    result = nested_url_blocked("http://agg.example.com/list.m3u8", BLACKLIST, fake)
    assert result is True, "Expected True: one child matches bad.example"


def test_5_deep_nesting_blocked_at_leaf():
    """A.m3u8 → B.m3u8 → C.m3u8 (C lists blacklisted url) → A returns True."""
    c_content = make_m3u("http://bad.example.com/stream.ts")
    b_content = make_m3u("http://host.example.com/C.m3u8")
    a_content = make_m3u("http://host.example.com/B.m3u8")
    fake = FakeFetch({
        "http://host.example.com/A.m3u8": a_content,
        "http://host.example.com/B.m3u8": b_content,
        "http://host.example.com/C.m3u8": c_content,
    })
    result = nested_url_blocked("http://host.example.com/A.m3u8", BLACKLIST, fake)
    assert result is True, "Expected True: transitive blacklist hit through deep nesting"


def test_6_hls_master_clean_variants_recursed():
    """An HLS master playlist's variant streams ARE nested .m3u8 links and must be
    followed. Clean variants → False, and the variant URLs are actually fetched
    (relative URIs resolved against the master's URL)."""
    master_content = make_hls_master("720.m3u8", "1080.m3u8")
    base = "http://host.example.com/index.m3u8"
    fake = FakeFetch({base: master_content})  # variant fetches miss → "" → clean leaf
    result = nested_url_blocked(base, BLACKLIST, fake, cache={})
    assert result is False, "Expected False: clean master variants"
    # Proves recursion into the master (the opposite of the old buggy 'leaf' behavior):
    assert fake.call_counts.get("http://host.example.com/720.m3u8", 0) == 1, \
        "Master variant must be resolved against base and followed"


def test_6b_hls_media_segments_not_checked():
    """HLS media-playlist segments (.ts, positive #EXTINF duration) are the media source
    itself and must NOT be blacklist-checked, even when a segment path contains a
    blacklisted keyword like '/audio/'."""
    media_content = make_hls_media("seg/audio/0.ts", "seg/audio/1.ts")
    fake = FakeFetch({"http://host.example.com/media.m3u8": media_content})
    result = nested_url_blocked("http://host.example.com/media.m3u8", BLACKLIST, fake, cache={})
    assert result is False, \
        "Expected False: media segments are not nested links and are not checked"


def test_6c_hls_master_blacklisted_variant_real_repro():
    """Real-world regression (freetv 'no signal' placeholder): an HLS master whose only
    variant points to a nosignal_h264 playlist. With 'nosignal' blacklisted, the master
    must be blocked BEFORE speed testing. This is the exact case the user reported."""
    master_content = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080\n"
        "http://files4.3y1.xyz/media/video/nosignal_h264/playlist.m3u8\n"
    )
    url = ("https://stream1.freetv.fun/"
           "df7cf71b3e02015b9029ac087b3eec56fde92fd81d162962570629faec293037.m3u8")
    variant = "http://files4.3y1.xyz/media/video/nosignal_h264/playlist.m3u8"
    fake = FakeFetch({url: master_content})
    result = nested_url_blocked(url, ["nosignal"], fake, cache={})
    assert result is True, "Expected True: master variant URL contains 'nosignal'"
    # Caught by the direct substring check on the variant URL — no need to fetch it.
    assert fake.call_counts.get(variant, 0) == 0, \
        "Blacklisted variant should be caught without being fetched"


def test_7_cycle_terminates():
    """Cycle A.m3u8 → B.m3u8 → A.m3u8 with no blacklist hit → terminates, returns False."""
    a_content = make_m3u("http://host.example.com/B.m3u8")
    b_content = make_m3u("http://host.example.com/A.m3u8")
    fake = FakeFetch({
        "http://host.example.com/A.m3u8": a_content,
        "http://host.example.com/B.m3u8": b_content,
    })
    # Should not hang or raise; must return False (no blacklist hit)
    result = nested_url_blocked("http://host.example.com/A.m3u8", BLACKLIST, fake)
    assert result is False, "Expected False: cycle with no blacklist hit"


def test_8_no_depth_limit_deep_chain_blocked():
    """Recursion is NOT depth-limited: a blacklisted url buried arbitrarily deep in a
    chain of nested m3u8 playlists is still caught. Recursion only stops at a
    non-playlist (leaf) url."""
    mapping = {}
    chain_len = 10  # far deeper than any old limit
    deepest = f"http://host.example.com/level{chain_len}.m3u8"
    mapping[deepest] = make_m3u("http://bad.example.com/deep.ts")
    prev = deepest
    for d in range(chain_len - 1, -1, -1):
        current = f"http://host.example.com/level{d}.m3u8"
        mapping[current] = make_m3u(prev)
        prev = current

    root = "http://host.example.com/level0.m3u8"
    fake = FakeFetch(mapping)
    result = nested_url_blocked(root, BLACKLIST, fake)
    assert result is True, "Expected True: deep blacklisted url must be caught (no depth limit)"
    # Every level was fetched on the way down (no early depth cutoff).
    assert fake.call_counts[f"http://host.example.com/level{chain_len}.m3u8"] == 1, \
        "Deepest playlist must be reached and fetched"


def test_9_cache_prevents_duplicate_fetches():
    """Shared cache ensures a repeated url is fetched only once."""
    content = make_m3u("http://cdn.example.com/clean.ts")
    shared_url = "http://shared.example.com/list.m3u8"
    fake = FakeFetch({shared_url: content})
    cache = {}

    # First call
    r1 = nested_url_blocked(shared_url, BLACKLIST, fake, cache=cache)
    # Second call — should hit cache, NOT call fake again
    r2 = nested_url_blocked(shared_url, BLACKLIST, fake, cache=cache)

    assert r1 is False
    assert r2 is False
    assert fake.call_counts[shared_url] == 1, \
        f"Expected 1 fetch (cached), got {fake.call_counts[shared_url]}"


def test_10_fetch_failure_not_blocked():
    """URL whose fetch returns "" is treated as NOT blocked (never over-block on uncertainty)."""
    fake = FakeFetch({})  # all misses → ""
    result = nested_url_blocked("http://failing.example.com/list.m3u8", BLACKLIST, fake)
    assert result is False, "Expected False: fetch failure should not block"


def test_11_cycle_with_blacklisted_sibling_shared_cache():
    """Fix-1 repro: P→N, N→P (cycle), P→X(blacklisted). With a SHARED cache,
    resolving P first must not poison N's verdict. Both P and N must be True."""
    p_url = "http://host.example.com/P.m3u8"
    n_url = "http://host.example.com/N.m3u8"
    # P lists N and a blacklisted child X; N lists P (back-edge → cycle).
    p_content = make_m3u(n_url, "http://bad.example.com/X.ts")
    n_content = make_m3u(p_url)
    fake = FakeFetch({p_url: p_content, n_url: n_content})
    cache = {}

    p_result = nested_url_blocked(p_url, BLACKLIST, fake, cache=cache)
    n_result = nested_url_blocked(n_url, BLACKLIST, fake, cache=cache)

    assert p_result is True, "Expected True: P reaches blacklisted X directly"
    assert n_result is True, "Expected True: N reaches X via P; cache must not poison it"


def test_12_hls_marker_inside_url_fragment():
    """An HLS marker appearing inside a URL fragment must NOT prevent the URL from being
    extracted and checked (the marker is part of the URI, not a directive)."""
    content = "#EXTM3U\n#EXTINF:-1 ,Bad\nhttp://bad.example/stream.ts#EXT-X-ENDLIST\n"
    fake = FakeFetch({"http://agg.example.com/list.m3u8": content})
    result = nested_url_blocked("http://agg.example.com/list.m3u8", ["bad.example"], fake)
    assert result is True, \
        "Expected True: marker inside URL fragment must not make this a leaf"


def test_13_txt_nested_aggregation():
    """A .txt nested aggregation is recursed like m3u: blacklisted child → True; clean → False."""
    bad_content = make_txt(("Chan", "http://bad.example.com/x.ts"))
    fake_bad = FakeFetch({"http://agg.example.com/list.txt": bad_content})
    result_bad = nested_url_blocked("http://agg.example.com/list.txt", BLACKLIST, fake_bad)
    assert result_bad is True, "Expected True: .txt child matches blacklist"

    clean_content = make_txt(
        ("Chan1", "http://cdn.example.com/clean1.ts"),
        ("Chan2", "http://cdn.example.com/clean2.ts"),
    )
    fake_clean = FakeFetch({"http://agg.example.com/clean.txt": clean_content})
    result_clean = nested_url_blocked("http://agg.example.com/clean.txt", BLACKLIST, fake_clean)
    assert result_clean is False, "Expected False: all .txt children clean"


def test_14_blacklist_noop():
    """Empty or None blacklist short-circuits: always False, and never fetches."""
    fake_empty = FakeFetch({"http://agg.example.com/list.m3u8": make_m3u("http://bad.example.com/x.ts")})
    r_empty = nested_url_blocked("http://agg.example.com/list.m3u8", [], fake_empty)
    assert r_empty is False, "Expected False: empty blacklist is a no-op"
    assert fake_empty.total_calls() == 0, "Empty blacklist must not fetch"

    fake_none = FakeFetch({"http://agg.example.com/list.m3u8": make_m3u("http://bad.example.com/x.ts")})
    r_none = nested_url_blocked("http://agg.example.com/list.m3u8", None, fake_none)
    assert r_none is False, "Expected False: None blacklist is a no-op"
    assert fake_none.total_calls() == 0, "None blacklist must not fetch"


# ---------------------------------------------------------------------------
# filter_channel_data_nested_blacklist tests
# ---------------------------------------------------------------------------

def _make_channel_data(*entries):
    """Build a channel_data dict from a list of (category, name, url, origin, headers) tuples."""
    result = {}
    for category, name, url, origin, headers in entries:
        result.setdefault(category, {}).setdefault(name, []).append(
            {"url": url, "origin": origin, "headers": headers}
        )
    return result


def test_F1_blacklisted_removed_clean_kept():
    """One url whose nested m3u8 contains a blacklisted child is removed;
    a clean leaf url is kept. Return value == number of removed entries."""
    blacklisted_url = "http://agg.example.com/list.m3u8"
    clean_url = "http://cdn.example.com/clean.ts"
    # The blacklisted m3u8 lists a bad child
    bad_content = make_m3u("http://bad.example.com/stream.ts")
    fake = FakeFetch({blacklisted_url: bad_content})
    make_fetch = lambda headers: fake  # noqa: E731

    channel_data = _make_channel_data(
        ("Cat1", "Chan1", blacklisted_url, "subscribe", None),
        ("Cat1", "Chan2", clean_url, "subscribe", None),
    )
    removed = filter_channel_data_nested_blacklist(channel_data, BLACKLIST, make_fetch)
    assert removed == 1, f"Expected 1 removed, got {removed}"
    assert channel_data["Cat1"]["Chan1"] == [], "Blacklisted entry must be removed"
    assert len(channel_data["Cat1"]["Chan2"]) == 1, "Clean entry must be kept"


def test_F2_retain_origin_exempt():
    """An entry with origin in retain_origin is NOT removed even if its url would be blocked,
    and the fake is NOT fetched for it."""
    blacklisted_url = "http://agg.example.com/list.m3u8"
    bad_content = make_m3u("http://bad.example.com/stream.ts")
    fake = FakeFetch({blacklisted_url: bad_content})
    make_fetch = lambda headers: fake  # noqa: E731

    # origin="whitelist" is in retain_origin
    channel_data = _make_channel_data(
        ("Cat1", "Chan1", blacklisted_url, "whitelist", None),
    )
    removed = filter_channel_data_nested_blacklist(
        channel_data, BLACKLIST, make_fetch, retain_origin=("whitelist", "hls")
    )
    assert removed == 0, "Exempt origin must not be removed"
    assert len(channel_data["Cat1"]["Chan1"]) == 1, "Exempt entry must remain"
    assert fake.total_calls() == 0, "Fetcher must NOT be called for exempt origins"


def test_F3_empty_blacklist_noop():
    """blacklist=None or blacklist=[] returns 0 immediately; nothing fetched; channel_data unchanged."""
    url = "http://agg.example.com/list.m3u8"
    bad_content = make_m3u("http://bad.example.com/stream.ts")
    fake = FakeFetch({url: bad_content})
    make_fetch = lambda headers: fake  # noqa: E731

    channel_data = _make_channel_data(("Cat1", "Chan1", url, "subscribe", None))
    original_len = len(channel_data["Cat1"]["Chan1"])

    r1 = filter_channel_data_nested_blacklist(channel_data, None, make_fetch)
    assert r1 == 0, "None blacklist must return 0"
    assert len(channel_data["Cat1"]["Chan1"]) == original_len, "channel_data must be unchanged"
    assert fake.total_calls() == 0, "None blacklist must not fetch"

    r2 = filter_channel_data_nested_blacklist(channel_data, [], make_fetch)
    assert r2 == 0, "Empty blacklist must return 0"
    assert fake.total_calls() == 0, "Empty blacklist must not fetch"


def test_F4_real_world_nosignal_repro():
    """Real-world repro: freetv master m3u8 pointing to nosignal_h264 variant.
    Blacklist=['nosignal'], origin='subscribe' → entry removed."""
    master_url = (
        "https://stream1.freetv.fun/"
        "df7cf71b3e02015b9029ac087b3eec56fde92fd81d162962570629faec293037.m3u8"
    )
    master_content = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080\n"
        "http://files4.3y1.xyz/media/video/nosignal_h264/playlist.m3u8\n"
    )
    fake = FakeFetch({master_url: master_content})
    make_fetch = lambda headers: fake  # noqa: E731

    channel_data = _make_channel_data(
        ("Sports", "SomeChannel", master_url, "subscribe", None),
    )
    removed = filter_channel_data_nested_blacklist(
        channel_data, ["nosignal"], make_fetch, retain_origin=("whitelist", "hls")
    )
    assert removed == 1, f"Expected 1 removed, got {removed}"
    assert channel_data["Sports"]["SomeChannel"] == [], "nosignal master must be removed"


def test_R1_redirect_target_blacklisted():
    """A non-playlist url that 302-redirects to a blacklisted placeholder is caught by
    the redirect TARGET url, not silently followed. (catvod live.php -> backup.m3u8)"""
    live = "https://iptv.catvod.com/live.php?id=CCTV1&line=14"
    backup = "https://www.catvod.com/backup.m3u8"
    fake = FakeFetch(redirects={live: backup})
    result = nested_url_blocked(live, ["backup"], fake)
    assert result is True, "Expected True: redirect target contains blacklisted 'backup'"


def test_R2_redirect_to_clean_playlist_content_checked():
    """A redirect to a clean-named playlist is followed, and that playlist's nested
    content is still blacklist-checked."""
    live = "https://host.example.com/live.php?id=1"
    real = "https://cdn.example.com/real.m3u8"
    fake = FakeFetch(
        content_map={real: make_m3u("https://bad.example.com/x.ts")},
        redirects={live: real},
    )
    assert nested_url_blocked(live, BLACKLIST, fake) is True, \
        "Expected True: resolved playlist content lists a blacklisted url"


def test_R3_clean_redirect_not_blocked():
    """A redirect to a fully clean playlist is not blocked (no over-blocking)."""
    live = "https://host.example.com/live.php?id=2"
    real = "https://cdn.example.com/clean.m3u8"
    fake = FakeFetch(
        content_map={real: make_m3u("https://cdn.example.com/clean.ts")},
        redirects={live: real},
    )
    assert nested_url_blocked(live, BLACKLIST, fake) is False, \
        "Expected False: clean redirect target and clean content"


def test_R4_terminal_media_not_fetched():
    """Obvious terminal media (.ts/.flv/...) is a leaf: never fetched, even though it
    could in theory redirect. Keeps the pass cheap for direct media urls."""
    fake = FakeFetch(redirects={"http://host/seg.ts": "http://bad.example/backup.m3u8"})
    assert nested_url_blocked("http://host/seg.ts", BLACKLIST, fake) is False
    assert fake.total_calls() == 0, "terminal media must not be fetched"


# ---------------------------------------------------------------------------
# pytest-compatible test discovery  (functions prefixed with test_)
# plus a standalone runner for direct python execution
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_1_direct_leaf_hit,
    test_2_clean_non_playlist_leaf,
    test_3_nested_m3u8_all_children_clean,
    test_4_nested_m3u8_one_child_blacklisted,
    test_5_deep_nesting_blocked_at_leaf,
    test_6_hls_master_clean_variants_recursed,
    test_6b_hls_media_segments_not_checked,
    test_6c_hls_master_blacklisted_variant_real_repro,
    test_7_cycle_terminates,
    test_8_no_depth_limit_deep_chain_blocked,
    test_9_cache_prevents_duplicate_fetches,
    test_10_fetch_failure_not_blocked,
    test_11_cycle_with_blacklisted_sibling_shared_cache,
    test_12_hls_marker_inside_url_fragment,
    test_13_txt_nested_aggregation,
    test_14_blacklist_noop,
    test_F1_blacklisted_removed_clean_kept,
    test_F2_retain_origin_exempt,
    test_F3_empty_blacklist_noop,
    test_F4_real_world_nosignal_repro,
    test_R1_redirect_target_blacklisted,
    test_R2_redirect_to_clean_playlist_content_checked,
    test_R3_clean_redirect_not_blocked,
    test_R4_terminal_media_not_fetched,
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

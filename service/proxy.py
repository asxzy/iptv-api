"""
Pure, network-free HLS ad-filtering proxy core.

No Flask, no network calls — all functions operate on strings only so they
can be tested with fakes (see tests/test_proxy_filter.py).
"""
import os
import re
from urllib.parse import urljoin, quote, urlparse

import utils.constants as constants


# ---------------------------------------------------------------------------
# AdFilter
# ---------------------------------------------------------------------------

class AdFilter:
    """Decides whether a segment/variant URI is an ad."""

    def __init__(
        self,
        keywords=None,
        regexes=None,
        drop_cue_ads=True,
        drop_discontinuity_ads=True,
    ):
        self.keywords: list = list(keywords or [])
        self.regexes: list = list(regexes or [])
        self.drop_cue_ads: bool = drop_cue_ads
        self.drop_discontinuity_ads: bool = drop_discontinuity_ads

    def matches(self, uri: str) -> bool:
        """Return True if uri matches any keyword (substring) or compiled regex."""
        for kw in self.keywords:
            if kw and kw in uri:
                return True
        for pattern in self.regexes:
            if pattern.search(uri):
                return True
        return False


# ---------------------------------------------------------------------------
# load_ad_filters
# ---------------------------------------------------------------------------

def load_ad_filters() -> AdFilter:
    """
    Read config/proxy_ad_filter.txt if present (one token/line, '#' comments
    skipped, 're:' prefix → compiled regex).  Fall back to reading
    config/blacklist.txt keywords.  Resilient to missing files.
    """
    # Resolve the proxy_ad_filter path defensively — constant may not exist yet.
    proxy_ad_filter_path = getattr(
        constants,
        "proxy_ad_filter_path",
        os.path.join("config", "proxy_ad_filter.txt"),
    )

    keywords: list = []
    regexes: list = []

    def _read_proxy_filter(path: str) -> bool:
        """Try to read path; return True if file existed and was loaded."""
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("re:"):
                        pattern_str = line[3:].strip()
                        try:
                            regexes.append(re.compile(pattern_str))
                        except re.error:
                            pass  # silently skip broken regex
                    else:
                        keywords.append(line)
            return True
        except OSError:
            return False

    if not _read_proxy_filter(proxy_ad_filter_path):
        # Fall back to blacklist.txt — keyword-only (no re: prefix support there)
        bl_path = getattr(constants, "blacklist_path", os.path.join("config", "blacklist.txt"))
        try:
            if os.path.isfile(bl_path):
                with open(bl_path, "r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        keywords.append(line)
        except OSError:
            pass

    return AdFilter(keywords=keywords, regexes=regexes)


# ---------------------------------------------------------------------------
# Playlist detection
# ---------------------------------------------------------------------------

def is_master_playlist(content: str) -> bool:
    """True when the content contains an HLS master-playlist variant tag."""
    return "#EXT-X-STREAM-INF" in content


def is_media_playlist(content: str) -> bool:
    """True when content looks like an HLS media playlist."""
    return "#EXTINF" in content or "#EXT-X-TARGETDURATION" in content


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def build_proxy_url(proxy_base: str, target_url: str) -> str:
    """
    Build a proxy URL, e.g.
        ("/proxy", "http://x/a.m3u8") → "/proxy?url=http%3A%2F%2Fx%2Fa.m3u8"
    """
    encoded = quote(target_url, safe="")
    return f"{proxy_base}?url={encoded}"


def resolve_uri(base_url: str, uri: str) -> str:
    """
    Return an absolute URL for *uri* relative to *base_url*.
    If *uri* is already absolute it is returned unchanged.
    """
    return urljoin(base_url, uri)


# ---------------------------------------------------------------------------
# Master playlist rewriting
# ---------------------------------------------------------------------------

# Matches URI="..." attribute in tag lines (greedy-safe, handles escaped quotes).
_URI_ATTR_RE = re.compile(r'(URI=")([^"]+)(")')


def _rewrite_uri_attr(line: str, base_url: str, proxy_base: str) -> str:
    """Rewrite all URI="..." occurrences in a tag attribute line."""
    def _replace(m):
        abs_url = resolve_uri(base_url, m.group(2))
        return m.group(1) + build_proxy_url(proxy_base, abs_url) + m.group(3)
    return _URI_ATTR_RE.sub(_replace, line)


def filter_master_playlist(content: str, base_url: str, proxy_base: str) -> str:
    """
    Rewrite a master playlist so that every variant URI (and URI= attributes in
    #EXT-X-MEDIA / #EXT-X-I-FRAME-STREAM-INF) points to build_proxy_url(...).
    Everything else is kept verbatim.
    """
    lines = content.splitlines(keepends=True)
    out = []
    expect_variant_uri = False  # next non-comment, non-tag line is a variant URI

    for line in lines:
        stripped = line.rstrip("\r\n")

        if stripped.startswith("#EXT-X-STREAM-INF"):
            # Tag with attributes; the *next* bare line is the variant URI.
            out.append(line)
            expect_variant_uri = True
            continue

        if stripped.startswith("#EXT-X-MEDIA") or stripped.startswith("#EXT-X-I-FRAME-STREAM-INF"):
            # URI="..." is embedded as an attribute on the same line.
            rewritten = _rewrite_uri_attr(stripped, base_url, proxy_base)
            # Preserve the original line ending.
            ending = line[len(stripped):]
            out.append(rewritten + ending)
            continue

        if expect_variant_uri and stripped and not stripped.startswith("#"):
            # This is the bare variant URI line following #EXT-X-STREAM-INF.
            abs_url = resolve_uri(base_url, stripped)
            proxy_url = build_proxy_url(proxy_base, abs_url)
            ending = line[len(stripped):]
            out.append(proxy_url + ending)
            expect_variant_uri = False
            continue

        if stripped.startswith("#") or not stripped:
            # Any other tag or blank line resets the variant-uri expectation.
            if not stripped.startswith("#EXT-X-STREAM-INF"):
                expect_variant_uri = False

        out.append(line)

    return "".join(out)


# ---------------------------------------------------------------------------
# Media playlist rewriting / ad filtering
# ---------------------------------------------------------------------------

# Tags that belong to the global/header scope — always pass through immediately.
_HEADER_TAGS = {
    "#EXTM3U",
    "#EXT-X-VERSION",
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA-SEQUENCE",
    "#EXT-X-DISCONTINUITY-SEQUENCE",
    "#EXT-X-PLAYLIST-TYPE",
    "#EXT-X-ENDLIST",
}

# Tags that, when seen, belong to the *next* segment (buffered until URI line).
_SEGMENT_TAGS = {
    "#EXTINF",
    "#EXT-X-BYTERANGE",
    "#EXT-X-KEY",
    "#EXT-X-MAP",
    "#EXT-X-PROGRAM-DATE-TIME",
    "#EXT-X-DATERANGE",
    "#EXT-X-DISCONTINUITY",
    "#EXT-X-GAP",
    "#EXT-X-CUE-OUT",
    "#EXT-X-CUE-IN",
    "#EXT-X-SCTE35",
    "#EXT-X-ASSET",
    "#EXT-X-CUE",
}


def _rewrite_key_map_uri(line: str, base_url: str) -> str:
    """Rewrite URI="..." in #EXT-X-KEY or #EXT-X-MAP to absolute URL."""
    def _replace(m):
        abs_url = resolve_uri(base_url, m.group(2))
        return m.group(1) + abs_url + m.group(3)
    return _URI_ATTR_RE.sub(_replace, line)


def filter_media_playlist(
    content: str,
    base_url: str,
    ad_filter: AdFilter,
    segment_proxy_base: str = None,
) -> str:
    """
    Walk the media playlist line-by-line, dropping ad segments and rewriting
    kept segment URIs to absolute (or through segment_proxy_base if given).

    Ad detection:
    - Cue ad break: between #EXT-X-CUE-OUT and #EXT-X-CUE-IN (when drop_cue_ads).
    - Keyword/regex match on resolved URI.
    - Discontinuity-bounded block whose segments all match (when drop_discontinuity_ads).

    Fidelity: unknown tags are preserved verbatim; trailing-newline structure is kept.
    """
    lines = content.splitlines(keepends=True)
    out = []  # output accumulator

    # State machine
    in_cue_break = False         # between CUE-OUT and CUE-IN
    pending = []                 # buffered per-segment tag lines (not yet emitted)
    pending_discont = False      # True when #EXT-X-DISCONTINUITY is buffered in pending

    # Discontinuity block accumulation for conservative-drop logic.
    # We accumulate an entire discontinuity block before deciding to drop/keep it.
    discont_block = []           # [(emit_lines, resolved_uri_or_None), ...]
    in_discont_block = False     # currently collecting a discont block
    discont_block_pending = []   # lines to prepend (the opening #EXT-X-DISCONTINUITY line)

    def _flush_discont_block():
        """Decide keep/drop for the accumulated discontinuity block and emit."""
        if not in_discont_block or not discont_block:
            return
        # Drop only if drop_discontinuity_ads AND at least one segment URI matches.
        should_drop = (
            ad_filter.drop_discontinuity_ads
            and any(
                uri is not None and ad_filter.matches(uri)
                for (_, uri) in discont_block
            )
        )
        if not should_drop:
            for (emit_lines, _) in discont_block:
                out.extend(emit_lines)

    def _resolve_and_rewrite(uri_line: str) -> str:
        """Return the rewritten segment URI line (absolute or proxied)."""
        raw_uri = uri_line.rstrip("\r\n")
        abs_url = resolve_uri(base_url, raw_uri)
        if segment_proxy_base:
            new_uri = build_proxy_url(segment_proxy_base, abs_url)
        else:
            new_uri = abs_url
        ending = uri_line[len(raw_uri):]
        return new_uri + ending

    for line in lines:
        stripped = line.rstrip("\r\n")

        # ---- Header / global tags ----------------------------------------
        is_header = any(stripped.startswith(t) for t in _HEADER_TAGS)
        if is_header:
            if in_discont_block:
                _flush_discont_block()
                in_discont_block = False
                discont_block = []
                discont_block_pending = []
            out.append(line)
            continue

        # ---- Blank lines -------------------------------------------------
        if not stripped:
            pending.append(line)
            continue

        # ---- CUE-OUT / CUE-IN -------------------------------------------
        if stripped.startswith("#EXT-X-CUE-OUT"):
            in_cue_break = True
            if not ad_filter.drop_cue_ads:
                pending.append(line)
            # else: suppress the tag
            continue

        if stripped.startswith("#EXT-X-CUE-IN"):
            in_cue_break = False
            if not ad_filter.drop_cue_ads:
                pending.append(line)
            # else: suppress the tag
            continue

        # ---- Discontinuity tag -------------------------------------------
        if stripped.startswith("#EXT-X-DISCONTINUITY"):
            if in_discont_block:
                # Closing a previous discontinuity block (new one starts).
                _flush_discont_block()
                in_discont_block = False
                discont_block = []
                discont_block_pending = []

            # Start a new discontinuity block; buffer the tag itself.
            in_discont_block = True
            discont_block_pending = [line]
            pending = []
            continue

        # ---- #EXT-X-KEY / #EXT-X-MAP rewrite ----------------------------
        if stripped.startswith("#EXT-X-KEY") or stripped.startswith("#EXT-X-MAP"):
            rewritten = _rewrite_key_map_uri(stripped, base_url)
            ending = line[len(stripped):]
            tag_line = rewritten + ending
            pending.append(tag_line)
            continue

        # ---- Other per-segment tags (buffer them) ------------------------
        if stripped.startswith("#"):
            pending.append(line)
            continue

        # ---- URI line (non-comment, non-tag) -----------------------------
        raw_uri = stripped
        abs_uri = resolve_uri(base_url, raw_uri)

        if in_discont_block:
            # We are collecting segments inside a discontinuity block.
            # Build "what we'd emit if kept" for this segment.
            if in_cue_break and ad_filter.drop_cue_ads:
                # Inside both a cue break and a discont block — treat as ad.
                segment_emit = []
            else:
                seg_tag_lines = discont_block_pending + pending
                discont_block_pending = []
                pending = []
                if segment_proxy_base:
                    new_uri = build_proxy_url(segment_proxy_base, abs_uri)
                else:
                    new_uri = abs_uri
                ending = line[len(stripped):]
                uri_out_line = new_uri + ending
                segment_emit = seg_tag_lines + [uri_out_line]

            discont_block.append((segment_emit, abs_uri))
            pending = []
            continue

        # Normal (non-discontinuity-block) segment handling.
        drop = False
        if in_cue_break and ad_filter.drop_cue_ads:
            drop = True
        elif ad_filter.matches(abs_uri):
            drop = True

        if drop:
            pending = []
        else:
            # Emit pending tags then the (rewritten) URI.
            out.extend(pending)
            pending = []
            rewritten_line = _resolve_and_rewrite(line)
            out.append(rewritten_line)

    # Flush any residual discontinuity block.
    if in_discont_block:
        _flush_discont_block()

    # Flush any remaining pending lines (e.g. trailing #EXT-X-ENDLIST already
    # handled above, but anything else that wasn't followed by a URI).
    out.extend(pending)

    return "".join(out)


# ---------------------------------------------------------------------------
# Station-list rewriting
# ---------------------------------------------------------------------------

def _wrap_url(url: str, proxy_base: str) -> str:
    """
    Wrap a single station URL (or multi-source part) through the proxy.

    Splits off any trailing $label suffix before wrapping so only the stream
    URI is proxied; the label is re-attached afterwards.

    Returns the part unchanged when it contains no '://' (e.g. placeholder
    strings or already-rewritten proxy URLs that were passed in again).
    """
    core, sep, label = url.strip().partition("$")
    if "://" not in core:
        return url  # nothing to wrap
    proxied = build_proxy_url(proxy_base, core)
    return proxied + "$" + label if sep else proxied


def rewrite_list_to_proxy(content: str, file_type: str, proxy_base: str) -> str:
    """
    Rewrite every station URL in *content* to point through the proxy.

    file_type == "m3u":
        Lines starting with '#' or blank lines pass through verbatim.
        A bare line that contains '://' has its URL wrapped; otherwise verbatim.
        Line endings are preserved (splitlines(keepends=True)).

    file_type == "txt":
        Blank lines, lines whose stripped form ends with ',#genre#', and
        comma-less lines pass through verbatim (same rules as
        merge_txt_multi_source).
        Otherwise the line is split on the first comma into (name, value).
        value may be a '#'-separated multi-source list; each part is wrapped
        individually, then rejoined with '#'.
        Trailing-newline structure is preserved (split on '\\n', rejoin).
    """
    if file_type == "m3u":
        lines = content.splitlines(keepends=True)
        out = []
        for line in lines:
            stripped = line.rstrip("\r\n")
            if not stripped or stripped.startswith("#"):
                out.append(line)
            elif "://" in stripped:
                ending = line[len(stripped):]
                out.append(_wrap_url(stripped, proxy_base) + ending)
            else:
                out.append(line)
        return "".join(out)

    # file_type == "txt" (default)
    parts = content.split("\n")
    out = []
    for raw in parts:
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.endswith(",#genre#") or "," not in line:
            out.append(raw)
            continue
        name, value = line.split(",", 1)
        sources = value.split("#")
        new_sources = [_wrap_url(s, proxy_base) for s in sources]
        out.append(name + "," + "#".join(new_sources))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def filter_playlist(
    content: str,
    base_url: str,
    proxy_base: str,
    ad_filter: AdFilter,
    segment_proxy_base: str = None,
) -> tuple:
    """
    Detect and dispatch to the appropriate filter.

    Returns (filtered_text, kind) where kind is one of:
        "master"      — was a master playlist
        "media"       — was a media playlist
        "passthrough" — not a recognizable playlist (content returned unchanged)
    """
    if is_master_playlist(content):
        return filter_master_playlist(content, base_url, proxy_base), "master"
    if is_media_playlist(content):
        return filter_media_playlist(content, base_url, ad_filter, segment_proxy_base), "media"
    return content, "passthrough"

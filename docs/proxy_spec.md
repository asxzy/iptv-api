# Real-time HLS ad-filtering proxy — implementation spec

## Problem
Providers hijack m3u8 playlists and inject ad segments into the **live media playlist**
(the actual stream the player consumes). Build-time blacklist filtering can't catch this
because the ad is inserted dynamically at playback time. We need a **runtime proxy**: the
client points its player at our proxy URL instead of the upstream; on every request we
fetch the upstream playlist live, strip the injected ads, rewrite nested URLs so they keep
flowing through the proxy, and serve a clean playlist.

## Endpoints (Flask, `service/app.py`)
- `GET /proxy?url=<urlencoded upstream playlist URL>`
  - Fetch upstream (redirect-aware). Detect master vs media playlist by content.
  - **Master playlist**: rewrite every variant `URI` (and `#EXT-X-MEDIA:...URI="..."`,
    `#EXT-X-I-FRAME-STREAM-INF:...URI="..."`) to `/proxy?url=<absolute resolved variant>`
    so the media playlist it points to also gets filtered. Keep all variant lines.
  - **Media playlist**: drop ad segments (see Ad detection), rewrite each kept segment URI
    to an **absolute** URL (resolved against the upstream base) by default so media bytes
    go straight to the CDN and don't transit our server. Also rewrite `#EXT-X-KEY:URI=`
    and `#EXT-X-MAP:URI=` to absolute. If `proxy_segments` config is on, rewrite segment
    URIs to `/proxy/segment?url=<absolute>` instead (route streams bytes through us).
  - Errors: 400 if `url` missing/malformed; 502 if upstream fetch fails / empty.
  - Response mimetype: `application/vnd.apple.mpegurl`.
- `GET /proxy/segment?url=<urlencoded segment URL>` (only used when `proxy_segments` on)
  - Stream the upstream segment bytes back to the client (pass-through), preserving
    content-type. Keeps it lightweight; default OFF.

## Ad detection (configurable) — `AdFilter`
1. **Keyword blocklist** — substring match on the *resolved* segment/variant URI.
   Source: `config/proxy_ad_filter.txt` if present, else fall back to `config/blacklist.txt`
   keywords. One token per line, `#` comments ignored.
2. **Regex** — lines in `proxy_ad_filter.txt` prefixed with `re:` compile to regex matched
   against the URI.
3. **Cue ad breaks** — segments between `#EXT-X-CUE-OUT` and `#EXT-X-CUE-IN` are ad breaks;
   drop those segments and the cue tags (`drop_cue_ads`, default True).
4. **Discontinuity-bounded ad blocks** — drop a discontinuity block ONLY when its segment
   URIs match a keyword/regex (`drop_discontinuity_ads`, default True). Conservative:
   never drop a discontinuity block whose segments are clean (live streams use legit
   discontinuities).

## Pure, testable module: `service/proxy.py`
No Flask, no network in the pure functions — so they unit-test with fakes like the
existing `tests/test_nested_blacklist.py` pattern.

```
class AdFilter:
    keywords: list[str]
    regexes: list[re.Pattern]
    drop_cue_ads: bool = True
    drop_discontinuity_ads: bool = True
    def matches(self, uri: str) -> bool   # keyword OR regex hit

def load_ad_filters() -> AdFilter                      # reads config files
def is_master_playlist(content: str) -> bool           # has #EXT-X-STREAM-INF
def is_media_playlist(content: str) -> bool            # has #EXTINF / #EXT-X-TARGETDURATION
def build_proxy_url(proxy_base: str, target_url: str) -> str
def resolve_uri(base_url: str, uri: str) -> str        # urljoin, leave absolute as-is
def filter_master_playlist(content, base_url, proxy_base) -> str
def filter_media_playlist(content, base_url, ad_filter, segment_proxy_base=None) -> str
def filter_playlist(content, base_url, proxy_base, ad_filter, segment_proxy_base=None)
    -> tuple[str, str]   # (filtered_text, "master"|"media"|"passthrough")
```

### Media playlist line-walk algorithm (fidelity-preserving)
Iterate lines, buffering "pending" segment tag lines (`#EXTINF`, `#EXT-X-BYTERANGE`,
`#EXT-X-DISCONTINUITY`, etc.). On a non-comment URI line: that URI + its pending tags form
one segment block. Decide keep/drop:
  - in a cue ad break (between CUE-OUT/CUE-IN) and `drop_cue_ads` → drop.
  - resolved URI matches AdFilter → drop.
  - else keep: emit pending tags + rewritten URI line.
Track `#EXT-X-CUE-OUT`/`#EXT-X-CUE-IN` state; when `drop_cue_ads`, suppress the cue tags
themselves. Header/global tags (`#EXTM3U`, `#EXT-X-VERSION`, `-TARGETDURATION`,
`-MEDIA-SEQUENCE`, `-PLAYLIST-TYPE`, `-ENDLIST`, `-DISCONTINUITY-SEQUENCE`) pass through.
Rewrite `#EXT-X-KEY:...URI="..."` and `#EXT-X-MAP:...URI="..."` to absolute.
Preserve unknown tags verbatim. Preserve trailing-newline structure.

## Config additions (`utils/config.py`, `config/config.ini`)
- `open_proxy` (bool, default True) — toggle endpoints.
- `proxy_segments` (bool, default False) — stream segment bytes through `/proxy/segment`.
- New path constant `proxy_ad_filter_path = config/proxy_ad_filter.txt` in `utils/constants.py`.

## Proxied station-list endpoints (added in phase 2)
Serve the existing result list with every station URL rewritten to point through `/proxy`,
so a player loading the list streams everything through the ad filter.

- `GET /proxy/m3u`       → `config.final_file` as m3u, station URLs rewritten
- `GET /proxy/txt`       → `config.final_file` as txt, station URLs rewritten
- `GET /proxy/txt/multi` → multi-source txt (merge_txt_multi_source) then rewritten
- 404 when `config.open_proxy` is False. `config.final_file == output/result.txt`
  (derive `.m3u`/`.txt` via `os.path.splitext(path)[0] + ".<type>"`). If the file is
  missing, return the waiting tip (like the existing list routes).
- The proxy base is **root-relative** (`/proxy`). The player resolves each
  `/proxy?url=...` link against the URL it fetched the list from, so it uses the exact
  scheme/host/port the client used. This avoids emitting an internal host/port (e.g.
  gunicorn's `127.0.0.1:5180`) that `request.host_url` would report behind nginx /
  port-forwarding. Responses also declare `charset=utf-8` (the mpegurl mimetype is not
  `text/*`, so werkzeug won't add it automatically and non-ASCII channel names would
  otherwise render as mojibake).

### Pure rewrite helper (`service/proxy.py`)
```
def rewrite_list_to_proxy(content: str, file_type: str, proxy_base: str) -> str
```
- `file_type == "m3u"`: pass through every `#`-prefixed/blank line verbatim; for a bare
  line containing `://`, replace it with the wrapped URL. Preserve line endings.
- `file_type == "txt"`: blank lines, `*,#genre#` markers, and comma-less lines pass through
  verbatim (same rules as `merge_txt_multi_source`). Otherwise split `name,value` on the
  first comma, split `value` on `#` (multi-source), wrap each part that contains `://`,
  rejoin with `#`, emit `name,<new value>`. Preserve trailing-newline structure.
- URL wrapping (shared): given a raw url, split off a `$label` suffix
  (`core, _, label = url.partition("$")`); if `core` has no `://` leave the part unchanged;
  else `build_proxy_url(proxy_base, core)` and re-append `"$"+label` if a label was present.
  (The `$label` is player display metadata, not part of the stream URL.)

## Tests
- `tests/test_proxy_filter.py` — pure-function unit tests (self-contained, pytest + main runner),
  covering: master rewrite (variant + EXT-X-MEDIA + I-FRAME URIs, relative resolution),
  media keyword drop, regex drop, CUE-OUT/IN drop, discontinuity-bounded drop (and
  clean-discontinuity keep), EXT-X-KEY/MAP rewrite, segment_proxy_base rewrite, master/media
  detection, empty/passthrough, trailing newline preservation, no-filter no-op.
- `tests/test_proxy_endpoint.py` — Flask test client with monkeypatched upstream fetch:
  `/proxy` master→rewritten, `/proxy` media→ad-stripped, missing url→400, upstream fail→502.
- Live smoke test (network): fetch the single upstream `config/local/test-cctv13.m3u`
  (`https://t.freetv.fun/live/cctv-10ke-jiao-13.m3u8`) through `/proxy` and assert a valid,
  non-empty playlist comes back.

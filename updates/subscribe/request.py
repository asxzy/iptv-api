import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from logging import INFO
from threading import Lock
from time import time
import sys
from urllib.parse import urlsplit, urljoin

import m3u8
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio

import utils.constants as constants
from utils.channel import format_channel_name
from utils.config import config
from utils.i18n import t
from utils.requests.tools import get_soup_requests
from utils.retry import retry_func
from utils.tools import (
    merge_objects,
    get_pbar_remaining,
    get_name_value,
    get_logger, join_url,
    github_blob_to_raw,
    save_url_content, close_logger_handlers,
    disable_urls_in_file,
    check_url_by_keywords,
)

_PLAYLIST_EXTENSIONS = (".m3u", ".m3u8", ".txt")

logger = get_logger(constants.log_path)


def _looks_like_playlist_url(url: str) -> bool:
    try:
        path = urlsplit(url).path.lower()
    except Exception:
        return False
    return path.endswith(_PLAYLIST_EXTENSIONS)


def _parse_aggregation_children(content: str, base_url: str = "") -> list:
    """Return the nested m3u/m3u8 links and station URLs contained in `content`,
    resolving relative URIs against `base_url`.

    Included (these are "links" we must blacklist-check / recurse into):
    - HLS master-playlist variant streams (#EXT-X-STREAM-INF -> nested .m3u8 links).
    - #EXTINF:-1 aggregation channels (the stations URL_B_i / URL_C_j).
    - Plain "name,url" txt entries and bare URL-per-line lists.

    Excluded:
    - HLS media segments (.ts, positive #EXTINF duration). Per requirement, the media
      source itself is never blacklist-checked here -- only the nested links are.
    """
    if not content:
        return []
    raw_children = []
    if "#EXTM3U" in content:
        parsed = None
        try:
            parsed = m3u8.loads(content)
        except Exception:
            parsed = None
        if parsed is not None and (parsed.playlists or parsed.segments):
            for playlist in parsed.playlists:
                # Master-playlist variant streams: nested .m3u8 links.
                if playlist.uri:
                    raw_children.append(playlist.uri)
            for segment in parsed.segments:
                # duration < 0 (e.g. -1) marks a live channel/station, not a media segment;
                # positive-duration segments are media (.ts) and are intentionally skipped.
                if segment.uri and (segment.duration is None or segment.duration < 0):
                    raw_children.append(segment.uri)
        else:
            # Aggregation lists with attributes (#EXTINF:-1 tvg-... ,Name) make m3u8 raise;
            # fall back to the project's extended-m3u parser.
            data = get_name_value(content, pattern=constants.multiline_m3u_pattern, open_headers=False)
            raw_children = [(item.get("value") or "") for item in data]
    else:
        # Plain txt "name,url" list, or a bare URL-per-line list.
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = constants.multiline_txt_pattern.match(line)
            raw_children.append(match.group("value") if match else line)
    children = []
    for child in raw_children:
        child = (child or "").strip().partition("$")[0].strip()
        if not child:
            continue
        if "://" not in child and base_url:
            child = urljoin(base_url, child)
        if "://" in child:
            children.append(child)
    return children


def nested_url_blocked(url, blacklist, fetch_text, cache=None, cache_lock=None,
                       depth=0, _in_progress=None) -> bool:
    """All-or-nothing recursive blacklist check.

    Recursion is NOT depth-limited: it keeps following nested links as long as the
    url is itself a playlist (.m3u/.m3u8/.txt), and stops once the url is not a
    playlist (a real media/leaf) or the fetch yields nothing. The cycle guard
    (`_in_progress`) guarantees termination on self-referential playlists.

    - fetch_text: callable(url)->str, returns content or "" on failure.
    - cache: optional dict[str,bool] shared across calls (thread-safe via cache_lock).
    - depth: tracked only to gate caching (deeper verdicts may be cycle-truncated).
    """
    if not blacklist or not url:
        return False
    if check_url_by_keywords(url, blacklist):
        return True
    if not _looks_like_playlist_url(url):
        return False
    if cache is not None:
        if cache_lock is not None:
            with cache_lock:
                if url in cache:
                    return cache[url]
        elif url in cache:
            return cache[url]
    if _in_progress is None:
        _in_progress = set()
    if url in _in_progress:  # cycle guard
        return False
    _in_progress.add(url)
    blocked = False
    try:
        children = _parse_aggregation_children(fetch_text(url), base_url=url)
        for child in children:
            if nested_url_blocked(child, blacklist, fetch_text, cache, cache_lock,
                                  depth + 1, _in_progress):
                blocked = True
                break
    finally:
        _in_progress.discard(url)
    # Only cache top-level (complete) verdicts; deeper verdicts may be cycle-truncated and context-dependent.
    if cache is not None and depth == 0:
        if cache_lock is not None:
            with cache_lock:
                cache[url] = blocked
        else:
            cache[url] = blocked
    return blocked


def filter_channel_data_nested_blacklist(channel_data, blacklist, make_fetch, retain_origin=(),
                                         cache=None, cache_lock=None, max_workers=32,
                                         show_progress=True) -> int:
    """Prune `channel_data` IN PLACE, removing every ChannelData whose url is
    nested-blacklisted (per nested_url_blocked). Covers all sources uniformly.

    Candidate playlist urls are checked concurrently (only .m3u/.m3u8/.txt urls are
    actually fetched; everything else is a free direct keyword check), with a visible
    progress bar so the stage is never silent.

    - make_fetch: callable(headers) -> (callable(url) -> str). Builds the fetcher for a url's headers.
    - retain_origin: origins exempt from the blacklist (e.g. whitelist/hls).
    - cache / cache_lock: optional shared per-run verdict cache.
    Returns the number of ChannelData entries removed.
    """
    if not blacklist:
        return 0
    # Collect unique candidate urls (skip exempt origins); keep one representative headers per url.
    candidates = {}
    for names in channel_data.values():
        for info_list in names.values():
            for info in info_list:
                if info.get("origin") in retain_origin:
                    continue
                url = info.get("url")
                if url and url not in candidates:
                    candidates[url] = info.get("headers")
    if not candidates:
        return 0
    if cache is None:
        cache = {}
    if cache_lock is None:
        cache_lock = Lock()
    blocked = set()
    blocked_lock = Lock()
    total = len(candidates)
    logger.info("Nested blacklist: checking %d candidate playlist url(s) with %d workers...",
                total, max_workers)
    start_ts = time()
    pbar = tqdm(
        total=total,
        desc=t("pbar.blacklist"),
        file=sys.stdout,
        mininterval=1,
        dynamic_ncols=False,
    ) if show_progress else None

    def _check(item):
        url, headers = item
        try:
            if nested_url_blocked(url, blacklist, make_fetch(headers),
                                  cache=cache, cache_lock=cache_lock):
                with blocked_lock:
                    blocked.add(url)
        except Exception:
            pass
        finally:
            if pbar is not None:
                pbar.update()

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(_check, list(candidates.items())))
    finally:
        if pbar is not None:
            pbar.close()

    elapsed = time() - start_ts
    if not blocked:
        logger.info("Nested blacklist: 0 of %d url(s) blocked (%.1fs)", total, elapsed)
        return 0
    removed = 0
    for names in channel_data.values():
        for name, info_list in list(names.items()):
            kept = [info for info in info_list if info.get("url") not in blocked]
            removed += len(info_list) - len(kept)
            names[name] = kept
    logger.info("Nested blacklist: blocked %d url(s), removed %d entr(ies) of %d candidate(s) (%.1fs)",
                len(blocked), removed, total, elapsed)
    return removed


async def get_channels_by_subscribe_urls(
        urls,
        names=None,
        whitelist=None,
        callback=None,
):
    """
    Get the channels by subscribe urls
    """
    normalized_names = {format_channel_name(name) for name in (names or []) if name}
    if not os.getenv("GITHUB_ACTIONS") and config.cdn_url:
        def _map_raw(u):
            raw_u = github_blob_to_raw(u)
            return join_url(config.cdn_url, raw_u) if "raw.githubusercontent.com" in raw_u else raw_u

        def _map_entry(e):
            if isinstance(e, dict):
                e = e.copy()
                e.setdefault('source_url', e.get('url'))
                e['url'] = _map_raw(e.get('url'))
                return e
            return {'url': _map_raw(e), 'source_url': e}

        urls = [_map_entry(u) for u in urls]
        whitelist = [_map_raw(u) for u in whitelist] if whitelist else None
    if whitelist:
        index_map = {u: i for i, u in enumerate(whitelist)}

        def sort_key(u):
            key = u['url'] if isinstance(u, dict) else u
            return index_map.get(key, len(whitelist))

        urls.sort(key=sort_key)
    subscribe_results = {}
    subscribe_urls_len = len(urls)
    pbar = tqdm_asyncio(
        total=subscribe_urls_len,
        desc=t("pbar.getting_name").format(name=t("name.subscribe")),
        file=sys.stdout,
        mininterval=0,
        miniters=1,
        dynamic_ncols=False,
    )
    start_time = time()
    mode_name = t("name.subscribe")
    if callback:
        callback(
            t("pbar.getting_name").format(name=mode_name),
            0,
        )
    logger = get_logger(constants.unmatch_log_path, level=INFO, init=True)
    request_timeout = config.request_timeout
    open_headers = config.open_headers
    open_unmatch_category = config.open_unmatch_category
    open_auto_disable_source = config.open_auto_disable_source
    disabled_urls = set()
    disabled_lock = Lock()

    def _mark_disabled(source_url: str, reason: str):
        if not open_auto_disable_source or not source_url:
            return
        with disabled_lock:
            disabled_urls.add(source_url)
        logger.warning(t("msg.auto_disable_source").format(name=mode_name, url=source_url, reason=reason))

    def process_subscribe_channels(subscribe_info: str | dict) -> defaultdict:
        subscribe_url = subscribe_info.get('url') if isinstance(subscribe_info, dict) else subscribe_info
        source_url = subscribe_info.get('source_url', subscribe_url) if isinstance(subscribe_info,
                                                                                   dict) else subscribe_url
        headers = subscribe_info.get('headers') if isinstance(subscribe_info, dict) else None
        channels = defaultdict(list)
        in_whitelist = whitelist and (subscribe_url in whitelist)
        disable_reason = None
        try:
            response = None
            try:
                response = retry_func(lambda: get_soup_requests(subscribe_url, timeout=request_timeout,
                                                                headers_override=headers), name=subscribe_url)
            except Exception as e:
                logger.error("Subscribe request failed: %s", e)
                disable_reason = t("msg.auto_disable_request_failed")
            if response:
                if hasattr(response, 'text'):
                    response.encoding = "utf-8"
                    content = response.text
                else:
                    content = str(response)
                if not content:
                    disable_reason = t("msg.auto_disable_empty_content")
                try:
                    save_url_content('subscribe', subscribe_url, content)
                except Exception:
                    pass
                if content:
                    m3u_type = True if "#EXTM3U" in content else False
                    data = get_name_value(
                        content,
                        pattern=(
                            constants.multiline_m3u_pattern
                            if m3u_type
                            else constants.multiline_txt_pattern
                        ),
                        open_headers=open_headers if m3u_type else False
                    )

                    for item in data:
                        data_name = item.get("name", "").strip()
                        url = item.get("value", "").strip()
                        if data_name and url:
                            name = format_channel_name(data_name)
                            if normalized_names and name not in normalized_names:
                                logger.info(f"{data_name},{url}")
                                if not open_unmatch_category:
                                    continue
                            url_partition = url.partition("$")
                            url = url_partition[0]
                            info = url_partition[2]
                            value = {
                                "url": url,
                                "headers": item.get("headers", None),
                                "extra_info": info
                            }
                            if in_whitelist:
                                value["origin"] = "whitelist"
                            if name in channels:
                                if value not in channels[name]:
                                    channels[name].append(value)
                            else:
                                channels[name] = [value]
                if not channels and not disable_reason:
                    disable_reason = t("msg.auto_disable_no_match")
        except Exception as e:
            logger.error(t("msg.error_name_info").format(name=subscribe_url, info=e))
            if not disable_reason:
                disable_reason = t("msg.auto_disable_request_failed")
        finally:
            if disable_reason:
                _mark_disabled(source_url, disable_reason)
            pbar.update()
            if callback:
                callback(
                    t("msg.progress_desc").format(name=f"{t('pbar.get')}{mode_name}",
                                                  remaining_total=subscribe_urls_len - pbar.n,
                                                  item_name=mode_name,
                                                  remaining_time=get_pbar_remaining(n=pbar.n, total=pbar.total,
                                                                                    start_time=start_time)),
                    int((pbar.n / subscribe_urls_len) * 100),
                )
            return channels

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(process_subscribe_channels, subscribe_url)
            for subscribe_url in urls
        ]
        for future in futures:
            subscribe_results = merge_objects(subscribe_results, future.result())
        pbar.close()
        active_count = len(urls)
        disabled_count = 0
        if disabled_urls:
            counts = disable_urls_in_file(constants.subscribe_path, disabled_urls)
            active_count = counts["active"]
            disabled_count = counts["disabled"]
        logger.info(t("msg.auto_disable_source_done").format(name=mode_name, active_count=active_count,
                                                              disabled_count=disabled_count))
        close_logger_handlers(logger)
        return subscribe_results

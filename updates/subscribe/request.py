import asyncio
import logging
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

# Obvious terminal media / segment / asset files: a leaf we never fetch (no redirect or
# playlist nesting worth checking, and we must not download media bodies).
_TERMINAL_MEDIA_EXTENSIONS = (
    ".ts", ".flv", ".mp4", ".mkv", ".mov", ".avi", ".m4s", ".webm",
    ".aac", ".m4a", ".mp3", ".ac3", ".ogg", ".wav",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico",
)

logger = get_logger(constants.log_path)


def _is_terminal_media_url(url: str) -> bool:
    try:
        path = urlsplit(url).path.lower()
    except Exception:
        return False
    return path.endswith(_TERMINAL_MEDIA_EXTENSIONS)


def _match_blacklist_keyword(text: str, keywords: list[str]) -> str | None:
    """Return the first blacklist keyword found in *text*, or None if no match.

    Performs the same substring check as check_url_by_keywords but surfaces
    *which* keyword matched so callers can log it.
    """
    if not keywords:
        return None
    for kw in keywords:
        if kw in text:
            return kw
    return None


def _parse_aggregation_children(content: str, base_url: str = "") -> list:
    """Return the nested m3u/m3u8 links and station URLs contained in `content`,
    resolving relative URIs against `base_url`.

    Included:
    - HLS master-playlist variant streams (#EXT-X-STREAM-INF -> nested .m3u8 links).
    - ALL segment URIs (both live/station markers with -1 duration and media
      segments with positive duration). Media segment URIs are checked against
      the blacklist via direct keyword match in nested_url_blocked, which runs
      before the terminal-media early-return — no network fetch is performed.
    - Plain "name,url" txt entries and bare URL-per-line lists.

    NOTE: terminal media extensions (.ts/.flv/...) are never fetched by
    nested_url_blocked (direct keyword match → blocked | clean → False).
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
                if segment.uri:
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


def nested_url_blocked(url, blacklist, fetch, cache=None, cache_lock=None,
                       depth=0, _in_progress=None) -> bool:
    """All-or-nothing recursive, redirect-aware blacklist check.

    For each url (unless it is an obvious terminal media file) `fetch` resolves
    redirects and returns (chain, content):
      - every url in the redirect chain is blacklist-checked, so a server that 302s a
        dead channel to a fixed placeholder (e.g. .../backup.m3u8) is caught by the
        TARGET url instead of being silently followed;
      - the final playlist content is parsed and its nested links are recursed.

    Recursion is NOT depth-limited; it follows nested m3u/m3u8 links until a terminal
    media/leaf url. The cycle guard (`_in_progress`) guarantees termination.

    - fetch: callable(url) -> (chain: list[str], content: str); ([], "") on failure.
    - cache: optional dict[str,bool] shared across calls (thread-safe via cache_lock).
    - depth: tracked only to gate caching (deeper verdicts may be cycle-truncated).
    """
    if not blacklist or not url:
        return False
    if url_kw := _match_blacklist_keyword(url, blacklist):
        logger.debug("Blacklist hit: url=%s, keyword=%s", url, url_kw)
        return True
    if _is_terminal_media_url(url):
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
    # A failed fetch returns ([], "") -- distinct from a successful fetch of a non-playlist
    # leaf (chain=[url], content=""). A failed fetch is an UNKNOWN, not a clean verdict:
    # caching it would freeze one transient timeout into a permanent whitelist, and the
    # history re-check pass could never recover. Track it so we skip caching below.
    fetch_failed = False
    try:
        chain, content = fetch(url)
        if not chain:
            fetch_failed = True
        # 1) Blacklist-check every redirect target (don't silently follow to a placeholder).
        for target in chain:
            if target and target != url and (kw := _match_blacklist_keyword(target, blacklist)):
                logger.debug("Blacklist hit (redirect): url=%s, target=%s, keyword=%s", url, target, kw)
                blocked = True
                break
        # 2) Check raw playlist content for blacklist keywords (catches keywords in
        #    EXTINF metadata, comments, or any non-URI text that would otherwise be
        #    invisible to the URI-only child recursion below).
        if not blocked and content and (kw := _match_blacklist_keyword(content, blacklist)):
            logger.debug("Blacklist hit (content): url=%s, keyword=%s", url, kw)
            blocked = True
        # 3) Recurse into the nested links of the final (resolved) playlist content.
        if not blocked and content:
            base = chain[-1] if chain else url
            for child in _parse_aggregation_children(content, base_url=base):
                if nested_url_blocked(child, blacklist, fetch, cache, cache_lock,
                                      depth + 1, _in_progress):
                    blocked = True
                    break
    finally:
        _in_progress.discard(url)
    # Only cache top-level (complete) verdicts; deeper verdicts may be cycle-truncated and
    # context-dependent. Never cache a verdict derived from a failed fetch (see above) --
    # unless we still managed to block it (e.g. via a redirect target), which is authoritative.
    if cache is not None and depth == 0 and (blocked or not fetch_failed):
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
    checked = [0]
    checked_lock = Lock()
    progress_interval = max(1, min(100, total // 10))
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
            with checked_lock:
                checked[0] += 1
                c = checked[0]
            if c % progress_interval == 0 or c == total:
                logger.info("Nested blacklist: %d/%d checked (%.1fs)", c, total, time() - start_ts)

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _ in executor.map(_check, list(candidates.items())):
                pass
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


async def nested_url_blocked_async(url, blacklist, fetch, cache=None, cache_lock=None,
                                   depth=0, _in_progress=None, semaphore=None) -> bool:
    """Async version of nested_url_blocked.

    Same contract as the sync counterpart, but:
    - `fetch` is an async callable:  await fetch(url) -> (chain, content)
    - `semaphore`: optional asyncio.Semaphore to limit recursive fetch concurrency.
    - `cache_lock`: optional asyncio.Lock for thread-safe cache access (use
      asyncio.Lock when sharing across tasks in the same loop).
    """
    if not blacklist or not url:
        return False
    if url_kw := _match_blacklist_keyword(url, blacklist):
        logger.debug("Blacklist hit: url=%s, keyword=%s", url, url_kw)
        return True
    if _is_terminal_media_url(url):
        return False
    if cache is not None:
        if cache_lock is not None:
            async with cache_lock:
                if url in cache:
                    return cache[url]
        elif url in cache:
            return cache[url]
    if _in_progress is None:
        _in_progress = set()
    if url in _in_progress:
        return False
    _in_progress.add(url)
    blocked = False
    fetch_failed = False
    try:
        if semaphore is not None:
            async with semaphore:
                chain, content = await fetch(url)
        else:
            chain, content = await fetch(url)
        if not chain:
            fetch_failed = True
        for target in chain:
            if target and target != url and (kw := _match_blacklist_keyword(target, blacklist)):
                logger.debug("Blacklist hit (redirect): url=%s, target=%s, keyword=%s", url, target, kw)
                blocked = True
                break
        if not blocked and content:
            base = chain[-1] if chain else url
            # Check raw playlist content for blacklist keywords (catches keywords in
            # EXTINF metadata, comments, non-URI text invisible to child URI check).
            if kw := _match_blacklist_keyword(content, blacklist):
                logger.debug("Blacklist hit (content): url=%s, keyword=%s", url, kw)
                blocked = True
            else:
                for child in _parse_aggregation_children(content, base_url=base):
                    if await nested_url_blocked_async(
                        child, blacklist, fetch, cache, cache_lock,
                        depth + 1, _in_progress, semaphore,
                    ):
                        blocked = True
                        break
    finally:
        _in_progress.discard(url)
    if cache is not None and depth == 0 and (blocked or not fetch_failed):
        if cache_lock is not None:
            async with cache_lock:
                cache[url] = blocked
        else:
            cache[url] = blocked
    return blocked


async def filter_channel_data_nested_blacklist_async(
    channel_data, blacklist, make_fetch, retain_origin=(),
    cache=None, cache_lock=None, max_workers=32, show_progress=True,
) -> int:
    """Async version of filter_channel_data_nested_blacklist.

    Same contract as the sync counterpart, but uses asyncio.gather with a
    Semaphore for concurrent URL checking instead of ThreadPoolExecutor.
    """
    if not blacklist:
        return 0
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
        cache_lock = asyncio.Lock()
    blocked = set()
    blocked_lock = asyncio.Lock()
    total = len(candidates)
    checked = [0]
    checked_lock = asyncio.Lock()
    progress_interval = max(1, min(100, total // 10))
    logger.info("Nested blacklist (async): checking %d candidate playlist url(s) with %d workers...",
                total, max_workers)
    start_ts = time()
    pbar = tqdm(
        total=total,
        desc=t("pbar.blacklist"),
        file=sys.stdout,
        mininterval=1,
        dynamic_ncols=False,
    ) if show_progress else None
    sem = asyncio.Semaphore(max_workers)

    async def _check(item):
        url, headers = item
        try:
            if await nested_url_blocked_async(
                url, blacklist, make_fetch(headers),
                cache=cache, cache_lock=cache_lock, semaphore=sem,
            ):
                async with blocked_lock:
                    blocked.add(url)
        except Exception:
            pass
        finally:
            if pbar is not None:
                pbar.update()
            async with checked_lock:
                checked[0] += 1
                c = checked[0]
            if c % progress_interval == 0 or c == total:
                logger.info("Nested blacklist (async): %d/%d checked (%.1fs)", c, total, time() - start_ts)

    try:
        await asyncio.gather(*[_check(item) for item in candidates.items()])
    finally:
        if pbar is not None:
            pbar.close()

    elapsed = time() - start_ts
    if not blocked:
        logger.info("Nested blacklist (async): 0 of %d url(s) blocked (%.1fs)", total, elapsed)
        return 0
    removed = 0
    for names in channel_data.values():
        for name, info_list in list(names.items()):
            kept = [info for info in info_list if info.get("url") not in blocked]
            removed += len(info_list) - len(kept)
            names[name] = kept
    logger.info("Nested blacklist (async): blocked %d url(s), removed %d entr(ies) of %d candidate(s) (%.1fs)",
                len(blocked), removed, total, elapsed)
    return removed


async def process_subscribe_source(
    source_entry,
    fetch_source,
    test_speed_func,
    ipv6_support,
    aggregator_add,
    blacklist=None,
    blacklist_cache=None,
    blacklist_cache_lock=None,
    make_nested_fetch=None,
    retain_origin=(),
    progress_callback=None,
):
    """Process one subscribe source end-to-end: fetch → blacklist → speed test → aggregator.

    Builds subscribe-only channel data (no template merge — the aggregator's
    base_data already holds the template).  Returns the speed test result dict,
    or None if the source produced nothing processable.

    Parameters
    ----------
    source_entry : dict or str
        The subscribe source entry (url, optional headers/source_url).
    fetch_source : async callable(source_entry) -> dict
        Returns channel data in subscribe_result format:
        {category: {channel_name: [{url, headers?, ...}]}}.
    test_speed_func : async callable
        Signature: test_speed_func(data, ipv6=..., on_task_complete=...)
    ipv6_support : bool
    aggregator_add : callable
        The aggregator.add_item callback.
    blacklist : list[str], optional
        Blacklist keywords.  If empty/None, blacklist step is skipped.
    blacklist_cache : dict, optional
    blacklist_cache_lock : asyncio.Lock, optional
    make_nested_fetch : callable, optional
        Factory for the nested blacklist fetcher:
        make_nested_fetch(headers) -> async_callable(url) -> (chain, content)
    retain_origin : tuple, optional
    progress_callback : callable, optional
        Called with (source_url, stage, pct).
    """
    subscribe_url = source_entry.get('url') if isinstance(source_entry, dict) else source_entry
    if progress_callback:
        progress_callback(subscribe_url, "fetch", 0)

    try:
        channels = await fetch_source(source_entry)
    except Exception:
        logger.debug("process_subscribe_source: fetch failed for %s", subscribe_url, exc_info=True)
        if progress_callback:
            progress_callback(subscribe_url, "fetch", 100)
        return None

    if not channels:
        if progress_callback:
            progress_callback(subscribe_url, "fetch", 100)
        return None

    if progress_callback:
        progress_callback(subscribe_url, "fetch", 100)

    # Normalise the fetched channels into CategoryChannelData.
    # Subscribe results are keyed by channel name — wrap under a default category.
    channel_data = {}
    has_any = False
    for cate, channel_obj in channels.items():
        for name, info_list in channel_obj.items():
            if not info_list:
                continue
            has_any = True
            normalised = []
            for info in info_list:
                url = (info.get("url") or "").strip()
                if not url:
                    continue
                entry = {
                    "url": url,
                    "headers": info.get("headers"),
                    "origin": info.get("origin", "subscribe"),
                    "extra_info": info.get("extra_info", ""),
                }
                normalised.append(entry)
            if normalised:
                channel_data.setdefault(cate, {}).setdefault(name, []).extend(normalised)

    if not has_any:
        return None

    # Nested blacklist (keyword-only for terminal media, actual fetch for playlists)
    if blacklist:
        if progress_callback:
            progress_callback(subscribe_url, "blacklist", 0)
        if make_nested_fetch is None:
            async def _noop_fetch(u):
                return [u], ""
            nested_fetch_factory = lambda headers: _noop_fetch
        else:
            nested_fetch_factory = make_nested_fetch
        removed = await filter_channel_data_nested_blacklist_async(
            channel_data,
            blacklist,
            nested_fetch_factory,
            retain_origin=retain_origin,
            cache=blacklist_cache,
            cache_lock=blacklist_cache_lock,
            show_progress=False,
        )
        if progress_callback:
            progress_callback(subscribe_url, "blacklist", 100)

    if not _channel_data_has_urls(channel_data):
        return None

    # Dedup — simple URL-based dedup (origin-agnostic, unlike process_nested_dict
    # which skips whitelist/hls origins — not appropriate for per-source processing).
    if progress_callback:
        progress_callback(subscribe_url, "dedup", 0)
    seen_urls = set()
    for cate, channels in list(channel_data.items()):
        for name, info_list in list(channels.items()):
            kept = []
            for info in info_list:
                url = info.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    kept.append(info)
            channels[name] = kept
    test_data = channel_data
    if progress_callback:
        progress_callback(subscribe_url, "dedup", 100)

    urls_count = sum(len(v) for c in test_data.values() for v in c.values())
    if urls_count == 0:
        return None

    # Speed test
    if progress_callback:
        progress_callback(subscribe_url, "speed_test", 0)

    results = await test_speed_func(
        test_data,
        ipv6=ipv6_support,
        on_task_complete=aggregator_add,
    )

    if progress_callback:
        progress_callback(subscribe_url, "speed_test", 100)

    return results


def _channel_data_has_urls(channel_data) -> bool:
    for obj in channel_data.values():
        for info_list in obj.values():
            if info_list:
                return True
    return False


def _fetch_single_subscribe_sync(
    subscribe_info,
    *,
    normalized_names,
    whitelist,
    request_timeout,
    open_headers,
    open_unmatch_category,
    logger,
    _mode_name,
    mark_disabled,
    pbar_update=None,
):
    """Fetch and parse a single subscribe source (sync, may be called from a thread).

    Returns {channel_name: [{url, headers, extra_info, origin?, ...}]} or empty dict.
    """
    subscribe_url = subscribe_info.get('url') if isinstance(subscribe_info, dict) else subscribe_info
    source_url = subscribe_info.get('source_url', subscribe_url) if isinstance(subscribe_info,
                                                                                dict) else subscribe_url
    headers_override = subscribe_info.get('headers') if isinstance(subscribe_info, dict) else None
    channels = defaultdict(list)
    in_whitelist = whitelist and (subscribe_url in whitelist)
    disable_reason = None
    try:
        response = None
        try:
            response = retry_func(lambda: get_soup_requests(subscribe_url, timeout=request_timeout,
                                                            headers_override=headers_override), name=subscribe_url)
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
                            logger.info(f"{data_name}\n  URL: {url}")
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
            mark_disabled(source_url, disable_reason)
        if pbar_update:
            pbar_update()
    return channels


async def fetch_single_subscribe_async(
    subscribe_info,
    *,
    normalized_names,
    whitelist,
    mark_disabled=None,
):
    """Async wrapper around _fetch_single_subscribe_sync, runs the sync fetch in a thread.

    Returns {channel_name: [{url, headers, extra_info, origin?, ...}]} or empty dict.
    """
    request_timeout = config.request_timeout
    open_headers = config.open_headers
    open_unmatch_category = config.open_unmatch_category
    _mode_name = t("name.subscribe")
    logger = get_logger(constants.unmatch_log_path, level=INFO, init=True)

    if mark_disabled is None:
        mark_disabled = lambda *a: None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _fetch_single_subscribe_sync(
            subscribe_info,
            normalized_names=normalized_names,
            whitelist=whitelist,
            request_timeout=request_timeout,
            open_headers=open_headers,
            open_unmatch_category=open_unmatch_category,
            logger=logger,
            _mode_name=_mode_name,
            mark_disabled=mark_disabled,
        ),
    )


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
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] [UNMATCH] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            ))
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

    def _pbar_update():
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

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                _fetch_single_subscribe_sync,
                subscribe_url,
                normalized_names=normalized_names,
                whitelist=whitelist,
                request_timeout=request_timeout,
                open_headers=open_headers,
                open_unmatch_category=open_unmatch_category,
                logger=logger,
                _mode_name=mode_name,
                mark_disabled=_mark_disabled,
                pbar_update=_pbar_update,
            )
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

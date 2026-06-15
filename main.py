import asyncio
import copy
import datetime
import gzip
import logging
import os
import pickle
import sys
from threading import Lock
from time import time
from typing import Callable, Optional, Any

import pytz
from tqdm import tqdm

import utils.constants as constants
import utils.frozen as frozen
from updates.epg import get_epg
from updates.epg.tools import write_to_xml, compress_to_gz
from updates.subscribe import get_channels_by_subscribe_urls
from updates.subscribe.request import filter_channel_data_nested_blacklist
from utils.aggregator import ResultAggregator
from utils.channel import get_channel_items, append_total_data, test_speed, retain_origin
from utils.processing_status import status
from utils.requests.tools import get_redirect_chain_content
from utils.config import config
from utils.i18n import t
from utils.speed import clear_cache
from utils.tools import (
    get_logger,
    get_pbar_remaining,
    process_nested_dict,
    format_interval,
    check_ipv6_support,
    get_version_info,
    get_urls_len,
    get_public_url,
    parse_times,
    to_serializable,
    get_subscribe_entries,
    count_disabled_urls,
)
from utils.types import CategoryChannelData
from utils.whitelist import load_whitelist_maps
from utils.blacklist import get_blacklist

logger = get_logger(constants.log_path, init=True)

ProgressCallback = Callable[..., Any]


class UpdateSource:
    def __init__(self):
        self.whitelist_maps = None
        self.blacklist = None
        self._blacklist_snapshot = None
        self.nested_blacklist_cache = {}
        self.nested_blacklist_lock = Lock()

        self.update_progress: Optional[ProgressCallback] = None
        self.run_ui = False

        self.tasks: list[asyncio.Task] = []

        self.channel_items: CategoryChannelData = {}
        self.channel_names: list[str] = []

        self.subscribe_result = {}
        self.epg_result = {}

        self.channel_data: CategoryChannelData = {}

        self.pbar: Optional[tqdm] = None
        self.total = 0
        self.start_time = None

        self.stop_event: Optional[asyncio.Event] = None
        self.ipv6_support = False
        self.now = None

        self.aggregator: Optional[ResultAggregator] = None

    # ----------------------------
    # progress / pbar
    # ----------------------------
    def pbar_update(self, name: str = "", item_name: str = ""):
        if not self.pbar:
            return
        if self.pbar.n < self.total:
            self.pbar.update()
            remaining_total = self.total - self.pbar.n
            remaining_time = get_pbar_remaining(n=self.pbar.n, total=self.total, start_time=self.start_time)
            if self.update_progress:
                self.update_progress(
                    t("msg.progress_desc").format(
                        name=name,
                        remaining_total=remaining_total,
                        item_name=item_name,
                        remaining_time=remaining_time,
                    ),
                    int((self.pbar.n / self.total) * 100),
                )

    # ----------------------------
    # IO: cache
    # ----------------------------
    def _load_cache(self) -> dict:
        if not (config.open_history and os.path.exists(constants.cache_path)):
            return {}
        try:
            with gzip.open(constants.cache_path, "rb") as f:
                return pickle.load(f) or {}
        except Exception:
            return {}

    def _save_cache(self, cache_result: dict):
        serializable = to_serializable(cache_result or {})
        cache_dir = os.path.dirname(constants.cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with gzip.open(constants.cache_path, "wb") as f:
            pickle.dump(serializable, f)

    # ----------------------------
    # stage 1: prepare
    # ----------------------------
    def _prepare_channel_data(self):
        logger.info("Phase: preparing channel data…")
        status.set_phase("preparing")
        self.whitelist_maps = load_whitelist_maps(constants.whitelist_path)
        self.blacklist = get_blacklist()
        # The nested cache memoizes per-URL verdicts computed against the
        # blacklist keywords; drop it when the blacklist changes so a live edit
        # isn't masked by stale "allowed" verdicts from a previous run.
        if self.blacklist != self._blacklist_snapshot:
            with self.nested_blacklist_lock:
                self.nested_blacklist_cache.clear()
            self._blacklist_snapshot = list(self.blacklist)
        self.channel_items = get_channel_items(self.whitelist_maps, self.blacklist)
        self.channel_data = {}

        self.channel_names = [
            name for channel_obj in self.channel_items.values() for name in channel_obj.keys()
        ]

        if config.open_history and os.path.exists(constants.frozen_path):
            frozen.load(constants.frozen_path)

    def _make_blacklist_fetch(self):
        """Build the make_fetch(headers) used by the nested blacklist passes.

        The first attempt uses a short timeout to keep the pass snappy on dead/slow hosts.
        But a FAILED fetch (empty chain) means we couldn't verify the url at all -- treating
        that as "clean" fails open and lets a working placeholder (e.g. a 'nosignal' stream,
        which the speed test will happily keep) slip into results. So a failed snappy fetch
        of a playlist gets one more chance with the full request_timeout before we give up."""
        primary_timeout = min(config.request_timeout, 5)
        retry_timeout = config.request_timeout
        do_retry = retry_timeout > primary_timeout

        def make_fetch(headers):
            def _fetch(u):
                chain, content = get_redirect_chain_content(
                    u, timeout=primary_timeout, headers_override=headers
                )
                if not chain and do_retry:
                    chain, content = get_redirect_chain_content(
                        u, timeout=retry_timeout, headers_override=headers
                    )
                return chain, content
            return _fetch

        return make_fetch

    def _filter_nested_blacklist(self):
        """Apply the nested m3u/m3u8 blacklist to the fully-merged channel data, BEFORE
        the speed test, so it covers all sources (subscribe, local, history). Reuses the
        per-run cache populated during subscribe parsing to avoid re-fetching."""
        if not self.blacklist:
            return
        logger.info("Applying nested blacklist before speed test (%d keyword(s))...", len(self.blacklist))

        removed = filter_channel_data_nested_blacklist(
            self.channel_data,
            self.blacklist,
            self._make_blacklist_fetch(),
            retain_origin=retain_origin,
            cache=self.nested_blacklist_cache,
            cache_lock=self.nested_blacklist_lock,
        )
        if removed:
            logger.info("Nested blacklist removed %d url(s) before speed test", removed)

    def _filter_history_nested_blacklist(self, cache):
        """Prune the loaded history/cache result through the SAME nested blacklist, reusing
        the per-run verdict cache so urls already judged in the base-data pass cost nothing.

        Without this, sort_channel_result re-emits history entries verbatim (no blacklist
        check), so a blacklisted url that ever reached a previous result would persist
        across every future run, bypassing the filter entirely."""
        if not (self.blacklist and cache):
            return
        removed = filter_channel_data_nested_blacklist(
            cache,
            self.blacklist,
            self._make_blacklist_fetch(),
            retain_origin=retain_origin,
            cache=self.nested_blacklist_cache,
            cache_lock=self.nested_blacklist_lock,
            show_progress=False,
        )
        if removed:
            logger.info("Nested blacklist removed %d url(s) from loaded history", removed)

    # ----------------------------
    # stage 2: fetch subscribe/epg (concurrent)
    # ----------------------------
    async def _fetch_subscribe(self, channel_names: list[str]):
        whitelist_entries, default_entries = get_subscribe_entries(constants.subscribe_path)
        disabled_count = count_disabled_urls(constants.subscribe_path)

        seen = set()
        subscribe_entries = []
        for e in (whitelist_entries + default_entries):
            url = e['url'] if isinstance(e, dict) else e
            if url in seen:
                continue
            seen.add(url)
            subscribe_entries.append(e)

        logger.info(
            t("msg.subscribe_urls_whitelist_total").format(
                default_count=len(default_entries),
                whitelist_count=len(whitelist_entries),
                disabled_count=disabled_count,
                total=len(subscribe_entries),
            ),
        )

        if not subscribe_entries:
            logger.warning(t("msg.no_subscribe_urls").format(file=constants.subscribe_path))
            return {}

        whitelist_urls = [e['url'] for e in whitelist_entries]

        return await get_channels_by_subscribe_urls(
            subscribe_entries,
            names=channel_names,
            whitelist=whitelist_urls,
            callback=self.update_progress,
        )

    async def _fetch_epg(self, channel_names: list[str]):
        return await get_epg(channel_names, callback=self.update_progress)

    async def visit_page(self, channel_names: list[str] = None):
        """
        Visits subscribe and epg pages concurrently to fetch data.
        """
        channel_names = channel_names or []
        logger.info("Phase: fetching subscribe/EPG sources (%d channel names)", len(channel_names))
        status.set_phase("fetching_subscribe")

        cors: list[tuple[str, asyncio.Future]] = []
        if config.open_method.get("subscribe"):
            cors.append(("subscribe_result", asyncio.create_task(self._fetch_subscribe(channel_names))))
            logger.debug("Spawned subscribe fetch task")
        if config.open_method.get("epg"):
            cors.append(("epg_result", asyncio.create_task(self._fetch_epg(channel_names))))
            status.set_phase("fetching_epg")
            logger.debug("Spawned EPG fetch task")

        if not cors:
            return

        results = await asyncio.gather(*(c for _, c in cors), return_exceptions=True)
        for (attr, _), res in zip(cors, results):
            if isinstance(res, Exception):
                logger.error("%s failed: %s", attr, res)
                setattr(self, attr, {})
            else:
                setattr(self, attr, res)

    def _write_epg_files_if_needed(self):
        if not self.epg_result:
            return
        write_to_xml(self.epg_result, constants.epg_result_path)
        compress_to_gz(constants.epg_result_path, constants.epg_gz_result_path)

    # ----------------------------
    # stage 3: aggregator lifecycle
    # ----------------------------
    async def _start_aggregator(self, cache: dict):
        self.aggregator = ResultAggregator(
            base_data=self.channel_data,
            first_channel_name=self.channel_names[0] if self.channel_names else None,
            ipv6_support=self.ipv6_support,
            write_interval=2.0,
            result=cache,
        )
        await self.aggregator.start()

    async def _stop_aggregator(self):
        if self.aggregator:
            await self.aggregator.stop()
            self.aggregator = None

    # ----------------------------
    # stage 4: speed test
    # ----------------------------
    async def _run_speed_test(self) -> CategoryChannelData:
        """
        Run speed test on the channel data and return the test results.
        """
        logger.info("Phase: speed testing")
        test_data = {
            category: copy.deepcopy(items)
            for category, items in self.channel_data.items()
            if category != t("content.unmatch_channel")
        }
        urls_total = get_urls_len(test_data)

        process_nested_dict(
            test_data,
            seen=set(),
            filter_host=config.speed_test_filter_host,
            ipv6_support=self.ipv6_support,
        )
        self.total = get_urls_len(test_data)

        logger.info(t("msg.total_urls_need_test_speed").format(total=urls_total, speed_total=self.total))
        status.set_phase("speed_testing", progress=0, total_urls=self.total)

        if self.total <= 0:
            self.aggregator.is_last = True
            await self.aggregator.flush_once(force=True)
            return {}
        if self.update_progress:
            self.update_progress(
                t("msg.progress_speed_test").format(total=urls_total, speed_total=self.total),
                0,
            )

        self.start_time = time()
        self.pbar = tqdm(
            total=self.total,
            desc=t("pbar.speed_test"),
            file=sys.stdout,
            mininterval=0,
            miniters=1,
            dynamic_ncols=False,
        )
        try:
            return await test_speed(
                test_data,
                ipv6=self.ipv6_support,
                callback=lambda: self.pbar_update(name=t("pbar.speed_test"), item_name=t("pbar.url")),
                on_task_complete=self.aggregator.add_item,
            )
        finally:
            if self.pbar:
                self.pbar.close()
                self.pbar = None

    # ----------------------------
    # stage 5: ui final notify
    # ----------------------------
    def _notify_ui_finished(self, main_start_time: float):
        if not self.run_ui:
            return

        open_service = config.open_service
        service_tip = t("msg.service_tip") if open_service else ""

        tip = (
            t("msg.service_run_success").format(service_tip=service_tip)
            if open_service and config.open_update is False
            else t("msg.update_completed").format(
                time=format_interval(time() - main_start_time),
                service_tip=service_tip,
            )
        )

        if self.update_progress:
            self.update_progress(
                tip,
                100,
                finished=True,
                url=f"{get_public_url()}" if open_service else None,
                now=self.now,
            )

    # ----------------------------
    # main flow
    # ----------------------------
    async def main(self):
        try:
            main_start_time = time()

            self._prepare_channel_data()

            if not self.channel_names:
                logger.warning(t("msg.no_channel_names").format(file=config.source_file))
                self._notify_ui_finished(main_start_time)
                return

            await self.visit_page(self.channel_names)
            self.tasks = []
            self._write_epg_files_if_needed()

            logger.info("Phase: merging channel sources (subscribe / local / history)...")
            status.set_phase("merging")
            _merge_start = time()
            total_subscribe = sum(len(urls) for urls in self.subscribe_result.values()) if self.subscribe_result else 0
            append_total_data(
                self.channel_items.items(),
                self.channel_data,
                self.subscribe_result,
                self.whitelist_maps,
                self.blacklist,
            )
            _merge_elapsed = time() - _merge_start
            total_urls = get_urls_len(self.channel_data)
            logger.info(
                "Merged %d subscribe urls + template → %d total urls in %.1fs",
                total_subscribe, total_urls, _merge_elapsed,
            )

            self._filter_nested_blacklist()
            status.set_phase("blacklisting")

            cache = self._load_cache()
            self._filter_history_nested_blacklist(cache)
            if cache:
                logger.info("Loaded history cache with %d categories", len(cache))

            await self._start_aggregator(cache)
            try:
                if config.open_speed_test:
                    clear_cache()
                    await self._run_speed_test()
                else:
                    self.aggregator.test_results = self.channel_data
                    self.aggregator.is_last = True
                    await self.aggregator.flush_once(force=True)

            finally:
                logger.info("Phase: finalizing (saving cache/frozen state)")
                status.set_phase("finalizing", progress=95)
                if self.aggregator:
                    try:
                        await self.aggregator.flush_once(force=True)
                    except Exception:
                        logger.debug("final re-sort flush failed", exc_info=True)
                if config.open_history:
                    self._save_cache(self.aggregator.result)
                    frozen.save(constants.frozen_path)
                    logger.debug("Cache and frozen state saved")
                await self._stop_aggregator()

            total_elapsed = time() - main_start_time
            logger.info(
                t("msg.update_completed").format(
                    time=format_interval(total_elapsed),
                    service_tip="",
                ),
            )
            status.set_complete()
            logger.info(
                "Update pipeline finished in %.1fs — total urls tested: %d",
                total_elapsed,
                status.get().get("tested_urls", 0),
            )
            self._notify_ui_finished(main_start_time)

        except asyncio.exceptions.CancelledError:
            logger.warning(t("msg.update_cancelled"))
            status.set_error("update cancelled")
        except Exception:
            logger.exception("Update pipeline crashed with unhandled exception")
            status.set_error("unhandled pipeline exception")

    # ----------------------------
    # lifecycle control
    # ----------------------------
    async def start(self, callback=None):
        def default_callback(*args, **kwargs):
            pass

        _orig = callback or default_callback

        def _progress_cb(*args, **kwargs):
            if args and isinstance(args[1], int):
                status.set_progress(args[1])
            _orig(*args, **kwargs)

        self.update_progress = _progress_cb
        self.run_ui = True if callback else False

        if not config.open_update:
            if self.run_ui:
                self.update_progress(t("msg.update_disabled"), 0, finished=True)
            return

        if self.run_ui:
            self.update_progress(t("msg.check_ipv6_support"), 0)

        self.ipv6_support = config.ipv6_support or check_ipv6_support()
        logger.debug("IPv6 support: %s", self.ipv6_support)

        if not os.getenv("GITHUB_ACTIONS") and (config.update_interval or config.update_times):
            logger.info("Starting scheduler (interval=%s, times=%s)", config.update_interval, config.update_times)
            await self.scheduler(asyncio.Event())
        elif config.update_startup:
            logger.info("Starting one-shot update pipeline")
            await self.main()

    def stop(self):
        for task in self.tasks:
            task.cancel()
        self.tasks = []

        if self.pbar:
            self.pbar.close()
            self.pbar = None

        if self.stop_event:
            self.stop_event.set()

    async def scheduler(self, stop_event: asyncio.Event):
        self.stop_event = stop_event
        tz = pytz.timezone(config.time_zone)
        mode = config.update_mode
        update_times = parse_times(config.update_times)

        try:
            self.now = datetime.datetime.now(tz)
            if config.update_startup:
                logger.info("Running initial update on startup")
                status.reset()
                await self.main()

            while not stop_event.is_set():
                self.now = datetime.datetime.now(tz)
                logger.debug("Scheduler tick at %s", self.now.strftime("%Y-%m-%d %H:%M:%S"))

                if mode == "time" and update_times:
                    candidates = []
                    for h, m in update_times:
                        candidate = self.now.replace(hour=h, minute=m, second=0, microsecond=0)
                        if candidate <= self.now:
                            candidate = candidate + datetime.timedelta(days=1)
                        candidates.append(candidate)

                    next_time = min(candidates)
                    wait_seconds = (next_time - self.now).total_seconds()
                    logger.info(t("msg.schedule_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")))
                    logger.debug("Sleeping %.0f seconds until next scheduled update", wait_seconds)

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
                        if stop_event.is_set():
                            break
                    except asyncio.TimeoutError:
                        self.now = datetime.datetime.now(tz)
                        logger.info("Scheduled time reached — starting update")
                        status.reset()
                        await self.main()
                else:
                    next_time = self.now + datetime.timedelta(hours=config.update_interval)
                    logger.info(t("msg.schedule_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")))
                    logger.debug("Sleeping %.1f hours until next interval update", config.update_interval)

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=config.update_interval * 3600)
                    except asyncio.TimeoutError:
                        self.now = datetime.datetime.now(tz)
                        logger.info("Interval reached — starting update")
                        status.reset()
                        await self.main()

        except asyncio.CancelledError:
            logger.warning(t("msg.schedule_cancelled"))


if __name__ == "__main__":
    info = get_version_info()
    logger.info(
        t("msg.version_info").format(name=info["name"], version=info["version"], build_time=info["build_time"]),
    )
    logger.debug("Config: log_level=%s, open_update=%s, open_speed_test=%s, open_service=%s",
                 logging.getLevelName(config.log_level), config.open_update, config.open_speed_test, config.open_service)
    if not config.open_update:
        logger.warning(t("msg.update_disabled"))
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        update_source = UpdateSource()
        loop.run_until_complete(update_source.start())

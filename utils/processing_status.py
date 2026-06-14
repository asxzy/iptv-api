"""
Processing status tracker — module-level singleton.

Provides a shared, thread-safe view of the current update pipeline phase
so that the Flask web routes and the result-file writer can show real-time
progress to clients.

Usage from any module::

    from utils.processing_status import status
    status.set_phase("speed_testing", progress=45, current="CCTV-1,http://...")
    status.set_error("subscribe source X failed")
    p = status.get()  # dict snapshot
"""

import threading
from time import time
from typing import Optional


class _ProcessingStatus:
    PHASES = (
        "idle",
        "preparing",
        "fetching_subscribe",
        "fetching_epg",
        "merging",
        "blacklisting",
        "speed_testing",
        "finalizing",
        "complete",
        "error",
    )

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------
    def reset(self):
        with self._lock:
            self._phase: str = "idle"
            self._progress: int = 0  # 0–100
            self._current_category: str = ""
            self._current_name: str = ""
            self._current_url: str = ""
            self._total_items: int = 0
            self._completed_items: int = 0
            self._total_urls: int = 0
            self._tested_urls: int = 0
            self._errors: list[str] = []
            self._start_time: Optional[float] = None
            self._phase_start_time: Optional[float] = None

    def set_phase(
        self,
        phase: str,
        *,
        progress: Optional[int] = None,
        current: str = "",
        total_items: Optional[int] = None,
        completed_items: Optional[int] = None,
        total_urls: Optional[int] = None,
        tested_urls: Optional[int] = None,
    ):
        with self._lock:
            assert phase in self.PHASES, f"Unknown phase: {phase!r}"
            if self._start_time is None:
                self._start_time = time()
            self._phase = phase
            self._phase_start_time = time()
            if progress is not None:
                self._progress = progress
            if current:
                self._current_url = current
            if total_items is not None:
                self._total_items = total_items
            if completed_items is not None:
                self._completed_items = completed_items
            if total_urls is not None:
                self._total_urls = total_urls
            if tested_urls is not None:
                self._tested_urls = tested_urls

    def set_current_item(self, category: str = "", name: str = "", url: str = ""):
        with self._lock:
            if category:
                self._current_category = category
            if name:
                self._current_name = name
            if url:
                self._current_url = url

    def set_progress(self, progress: int):
        with self._lock:
            self._progress = progress

    def inc_tested(self, n: int = 1):
        with self._lock:
            self._tested_urls += n

    def set_error(self, msg: str):
        with self._lock:
            self._errors.append(msg)
            self._phase = "error"

    def set_complete(self):
        with self._lock:
            self._phase = "complete"
            self._progress = 100

    def get(self) -> dict:
        with self._lock:
            elapsed = (time() - self._start_time) if self._start_time else 0.0
            phase_elapsed = (time() - self._phase_start_time) if self._phase_start_time else 0.0
            return {
                "phase": self._phase,
                "progress": self._progress,
                "current_category": self._current_category,
                "current_name": self._current_name,
                "current_url": self._current_url,
                "total_items": self._total_items,
                "completed_items": self._completed_items,
                "total_urls": self._total_urls,
                "tested_urls": self._tested_urls,
                "errors": list(self._errors),
                "elapsed_seconds": round(elapsed, 1),
                "phase_elapsed_seconds": round(phase_elapsed, 1),
                "is_processing": self._phase not in ("idle", "complete", "error"),
            }

    def is_processing(self) -> bool:
        return self.get()["is_processing"]

    def phase(self) -> str:
        with self._lock:
            return self._phase


# Module-level singleton — import and use directly.
status = _ProcessingStatus()

import threading
from typing import Any, Optional


class _ResultStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._contents: dict[str, str] = {}
        self._data: Any = None

    def store(self, path: str, content: str) -> None:
        with self._lock:
            self._contents[path] = content

    def get(self, path: str) -> Optional[str]:
        with self._lock:
            return self._contents.get(path)

    def store_data(self, data: Any) -> None:
        with self._lock:
            self._data = data

    def get_data(self) -> Any:
        with self._lock:
            return self._data

    def clear(self) -> None:
        with self._lock:
            self._contents.clear()
            self._data = None


result_store = _ResultStore()

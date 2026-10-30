"""Request-scoped search measurements, including calls from worker threads."""
import threading
import time


class SearchMetrics:
    def __init__(self):
        self.started = time.perf_counter()
        self._lock = threading.Lock()
        self.api_calls = {}
        self.timings_ms = {}

    def call(self, kind):
        with self._lock:
            self.api_calls[kind] = self.api_calls.get(kind, 0) + 1

    def duration(self, name, started):
        with self._lock:
            self.timings_ms[name] = round((time.perf_counter() - started) * 1000, 2)

    def snapshot(self):
        with self._lock:
            return {
                "timings_ms": dict(self.timings_ms),
                "api_calls": dict(self.api_calls),
                "total_ms": round((time.perf_counter() - self.started) * 1000, 2),
            }

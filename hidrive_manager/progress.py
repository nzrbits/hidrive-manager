"""Terminal progress reporting (stderr, in-place), safe for concurrent workers."""
from __future__ import annotations

import sys
import threading
import time


def human_size(n: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.0f} {units[i]}" if i == 0 else f"{n:.1f} {units[i]}"


def human_rate(bps: float) -> str:
    return human_size(bps) + "/s"


class Progress:
    """Tracks bytes done vs total across many files and redraws one status line."""

    def __init__(self, total_bytes: int, total_files: int, enabled: bool | None = None, label: str = ""):
        self.total_bytes = max(total_bytes, 0)
        self.total_files = total_files
        self.done_bytes = 0
        self.done_files = 0
        self.label = label
        self.enabled = sys.stderr.isatty() if enabled is None else enabled
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._last_draw = 0.0
        self._drawn = False

    def advance(self, n: int) -> None:
        with self._lock:
            self.done_bytes += n
            self._maybe_draw()

    def file_done(self) -> None:
        with self._lock:
            self.done_files += 1
            self._maybe_draw(force=True)

    def _maybe_draw(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.1:
            return
        self._last_draw = now
        elapsed = max(now - self._start, 1e-6)
        rate = self.done_bytes / elapsed
        pct = (self.done_bytes / self.total_bytes * 100) if self.total_bytes else 100.0
        bar_w = 24
        filled = int(bar_w * min(pct, 100) / 100)
        bar = "#" * filled + "-" * (bar_w - filled)
        line = (
            f"\r{self.label}[{bar}] {pct:5.1f}%  {human_size(self.done_bytes)}/{human_size(self.total_bytes)}"
            f"  {self.done_files}/{self.total_files} files  {human_rate(rate)}   "
        )
        sys.stderr.write(line)
        sys.stderr.flush()
        self._drawn = True

    def finish(self) -> None:
        with self._lock:
            if self.enabled and self._drawn:
                self._maybe_draw(force=True)
                sys.stderr.write("\n")
                sys.stderr.flush()

    def write(self, msg: str) -> None:
        """Print a line without breaking the progress bar."""
        with self._lock:
            if self.enabled and self._drawn:
                sys.stderr.write("\r\033[K")
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            if self.enabled and self._drawn:
                self._maybe_draw(force=True)

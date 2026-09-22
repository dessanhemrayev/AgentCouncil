"""Animated terminal spinner with no external dependencies.

While the agents are thinking (this can take minutes), the user sees a
live line like:

    ⠹ ROUND 3 — move: Claude Code (47s)

The animation spins in a background daemon thread and stops on exit from
the context manager (the line is erased so it does not interfere with the
result output). If stdout is not a TTY (pipe, CI, capture pytest), the
animation is disabled: one line is printed at start and one line per
update() — so the log stays informative.
"""

import itertools
import sys
import threading
import time
from typing import Optional, TextIO


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    INTERVAL = 0.1

    def __init__(
        self,
        text: str = "",
        stream: Optional[TextIO] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._text = text
        self._enabled = self._stream.isatty() if enabled is None else enabled
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_len = 0

    # -- public API ----------------------------------------------------

    def start(self) -> "Spinner":
        self._started_at = time.monotonic()
        if not self._enabled:
            self._print(f"{self._text}…")
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._enabled:
            self._clear_line()

    def update(self, text: str) -> None:
        with self._lock:
            self._text = text
        if not self._enabled:
            self._print(f"— {text}…")

    # -- context manager ---------------------------------------------

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- internals ---------------------------------------------------------

    def _print(self, line: str) -> None:
        print(line, file=self._stream, flush=True)

    def _elapsed(self) -> int:
        if self._started_at is None:
            return 0
        return int(time.monotonic() - self._started_at)

    def _clear_line(self) -> None:
        self._stream.write("\r" + " " * self._last_len + "\r")
        self._stream.flush()
        self._last_len = 0

    def _render(self, frame: str) -> None:
        line = f"{frame} {self._text} ({self._elapsed()}с)"
        pad = max(0, self._last_len - len(line))
        self._stream.write("\r" + line + " " * pad)
        self._stream.flush()
        self._last_len = len(line)

    def _animate(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop_event.is_set():
                break
            with self._lock:
                self._render(frame)
            time.sleep(self.INTERVAL)

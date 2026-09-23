

import sys
import threading
import time
from typing import Optional, TextIO

from yaspin import yaspin as _yaspin


class Spinner:
    """Compatibility wrapper around yaspin with the project's legacy API."""

    def __init__(
        self,
        text: str = "",
        stream: Optional[TextIO] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._text = text
        self._enabled = self._stream.isatty() if enabled is None else enabled
        self._spinner = None
        self._started_at: Optional[float] = None
        self._timer_thread: Optional[threading.Thread] = None

    # -- public API ----------------------------------------------------

    def start(self) -> "Spinner":
        if not self._enabled:
            self._print(f"{self._text}…")
            return self

        self._started_at = time.monotonic()
        self._spinner = _yaspin(text=self._render_text(), stream=self._stream, timer=False)
        self._spinner.start()
        self._timer_thread = threading.Thread(target=self._update_elapsed, daemon=True)
        self._timer_thread.start()
        return self

    def stop(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=0.2)
            self._timer_thread = None
        if self._enabled:
            self._stream.write("\r")
            self._stream.flush()

    def update(self, text: str) -> None:
        self._text = text
        if not self._enabled:
            self._print(f"— {text}…")
        elif self._spinner is not None:
            self._spinner.text = self._render_text(text)

    # -- context manager ---------------------------------------------

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- internals ---------------------------------------------------------

    def _render_text(self, text: Optional[str] = None) -> str:
        label = self._text if text is None else text
        if self._started_at is None:
            return label
        elapsed = int(time.monotonic() - self._started_at)
        return f"{label} ({elapsed}s)"

    def _update_elapsed(self) -> None:
        while self._spinner is not None:
            self._spinner.text = self._render_text()
            time.sleep(0.2)

    def _print(self, line: str) -> None:
        print(line, file=self._stream, flush=True)

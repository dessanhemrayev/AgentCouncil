"""Tests for src/spinner.py — animated progress indicator without dependencies."""

import io
import time

import pytest

from src.cli.spinner import Spinner


class FakeTty(io.StringIO):
    """StringIO pretending to be a terminal (isatty() -> True)."""

    def isatty(self) -> bool:
        return True


class TestNonTty:
    def test_start_prints_single_line(self):
        stream = io.StringIO()

        Spinner("работаю", stream=stream, enabled=False).start().stop()

        assert stream.getvalue() == "работаю…\n"

    def test_update_prints_lines(self):
        stream = io.StringIO()
        sp = Spinner("этап 1", stream=stream, enabled=False)

        sp.start()
        sp.update("этап 2")
        sp.stop()

        assert stream.getvalue() == "этап 1…\n— этап 2…\n"

    def test_default_stream_is_not_tty_in_tests(self, monkeypatch):
        # In tests (and pipes) stdout is not a TTY — no animation.
        stream = io.StringIO()
        monkeypatch.setattr("src.cli.spinner.sys.stdout", stream)

        Spinner("проверка").start().stop()

        assert stream.getvalue() == "проверка…\n"

    def test_stop_without_start_is_safe(self):
        stream = io.StringIO()

        Spinner("x", stream=stream, enabled=False).stop()

        assert stream.getvalue() == ""


class TestTty:
    def test_renders_frames_with_text_and_elapsed(self):
        stream = FakeTty()
        sp = Spinner("думаю", stream=stream, enabled=True)

        sp.start()
        time.sleep(0.25)  # пара кадров при INTERVAL=0.1
        sp.stop()

        out = stream.getvalue()
        assert "думаю" in out
        assert "⠋" in out  # первый кадр braille-анимации
        assert "(0с)" in out  # таймер секунд
        assert out.endswith("\r")  # при остановке строка затирается

    def test_update_changes_rendered_text(self):
        stream = FakeTty()
        sp = Spinner("ход: A", stream=stream, enabled=True)

        sp.start()
        time.sleep(0.05)
        sp.update("ход: B")
        time.sleep(0.25)
        sp.stop()

        out = stream.getvalue()
        assert "ход: A" in out
        assert "ход: B" in out

    def test_frames_cycled_over_time(self):
        stream = FakeTty()
        sp = Spinner("x", stream=stream, enabled=True)

        sp.start()
        time.sleep(0.25)
        sp.stop()

        # At 0.25s with INTERVAL=0.1 several frames should be drawn.
        assert stream.getvalue().count("\r") >= 3

    def test_context_manager_stops_on_exception(self):
        stream = FakeTty()

        with pytest.raises(ValueError):
            with Spinner("работаю", stream=stream, enabled=True):
                raise ValueError("бум")

        assert stream.getvalue().endswith("\r")

"""Tests for the GUI bootloader — src/gui/launcher.py (moved from main.py):
tkinter interpreter detection, relaunch, infinite relaunch loop guard.
tkinter is available in this environment, so "unavailability" is simulated
by replacing builtins.__import__ rather than by lacking the package.
"""

import builtins
import subprocess

import pytest

ctk = pytest.importorskip("customtkinter", exc_type=ImportError)  # noqa: F841 — GUI extra (T4)

from src.gui import launcher  # noqa: E402


def _fake_import_without_tkinter(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("no tkinter for this fake interpreter")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestFindInterpreterWithTkinter:
    def test_non_windows_returns_none(self, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "linux")

        assert launcher._find_interpreter_with_tkinter() is None

    def test_py_launcher_missing_returns_none(self, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "win32")

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("py not found")

        monkeypatch.setattr("subprocess.run", fake_run)

        assert launcher._find_interpreter_with_tkinter() is None

    def test_returns_first_candidate_with_working_tkinter(self, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "win32")

        listing = subprocess.CompletedProcess(
            args=["py", "-0p"],
            returncode=0,
            stdout=" -3.10-64  C:\\Python310\\python.exe\n -3.13-64  C:\\Python313\\python.exe\n",
        )

        def fake_run(cmd, **kwargs):
            if cmd == ["py", "-0p"]:
                return listing
            # first candidate (3.10) without tkinter, second (3.13) — working
            candidate = cmd[0]
            code = 1 if "Python310" in candidate else 0
            return subprocess.CompletedProcess(args=cmd, returncode=code)

        monkeypatch.setattr("subprocess.run", fake_run)

        assert launcher._find_interpreter_with_tkinter() == "C:\\Python313\\python.exe"

    def test_no_working_candidate_returns_none(self, monkeypatch):
        monkeypatch.setattr(launcher.sys, "platform", "win32")

        listing = subprocess.CompletedProcess(
            args=["py", "-0p"],
            returncode=0,
            stdout=" -3.10-64  C:\\Python310\\python.exe\n",
        )

        def fake_run(cmd, **kwargs):
            if cmd == ["py", "-0p"]:
                return listing
            return subprocess.CompletedProcess(args=cmd, returncode=1)

        monkeypatch.setattr("subprocess.run", fake_run)

        assert launcher._find_interpreter_with_tkinter() is None


class TestLaunchGui:
    def test_tkinter_available_calls_gui_main(self, monkeypatch):
        called = []
        monkeypatch.setattr("src.gui.app.main", lambda: called.append(True))

        launcher.launch_gui()

        assert called == [True]

    def test_recursion_guard_exits_without_second_rerun(self, monkeypatch, capsys):
        _fake_import_without_tkinter(monkeypatch)
        monkeypatch.setenv("AGENTCOUNCIL_GUI_RERUN", "1")

        def boom(*args, **kwargs):
            raise AssertionError("не должно искать интерпретатор второй раз")

        monkeypatch.setattr("src.gui.launcher._find_interpreter_with_tkinter", boom)

        with pytest.raises(SystemExit) as exc_info:
            launcher.launch_gui()

        assert exc_info.value.code == 1
        assert "tkinter недоступен" in capsys.readouterr().err

    def test_reruns_with_found_interpreter_and_env_flag(self, monkeypatch):
        _fake_import_without_tkinter(monkeypatch)
        monkeypatch.delenv("AGENTCOUNCIL_GUI_RERUN", raising=False)
        monkeypatch.setattr(
            "src.gui.launcher._find_interpreter_with_tkinter",
            lambda: "C:\\other\\python.exe",
        )

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            launcher.launch_gui()

        assert exc_info.value.code == 0
        assert captured["cmd"] == ["C:\\other\\python.exe", "-m", "src.gui.app"]
        assert captured["env"]["AGENTCOUNCIL_GUI_RERUN"] == "1"

    def test_falls_back_to_cli_when_no_interpreter_found(self, monkeypatch, capsys):
        _fake_import_without_tkinter(monkeypatch)
        monkeypatch.delenv("AGENTCOUNCIL_GUI_RERUN", raising=False)
        monkeypatch.setattr(
            "src.gui.launcher._find_interpreter_with_tkinter", lambda: None
        )

        launcher.launch_gui()

        assert "CLI-режим" in capsys.readouterr().err

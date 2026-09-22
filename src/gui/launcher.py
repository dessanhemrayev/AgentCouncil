"""GUI bootstrapper: finding an interpreter with tkinter and relaunching.

Moved here from main.py: the root CLI file stays thin, and the knowledge of
"how to launch the GUI in a foreign interpreter" lives in the GUI layer.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _find_interpreter_with_tkinter() -> Optional[str]:
    """Finds a Python interpreter with working tkinter via the Windows py launcher.

    On this platform (the project is Windows-only, see README) `python` on PATH
    is not guaranteed to be built with Tcl/Tk (e.g. uv cpython without tkinter) —
    the py launcher usually sees other installed interpreters."""
    if sys.platform != "win32":
        return None

    try:
        listing = subprocess.run(
            ["py", "-0p"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError):
        return None

    candidates = re.findall(r"(\S+python\.exe)", listing.stdout, re.IGNORECASE)
    for candidate in candidates:
        try:
            check = subprocess.run(
                [candidate, "-c", "import tkinter"], capture_output=True, timeout=10
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return candidate

    return None


def launch_gui() -> None:
    """council --gui — GUI as the primary interface.

    First try `import tkinter` in the CURRENT interpreter; if it is not
    built with Tcl/Tk, find a suitable one and relaunch `<it> -m src.gui.app` with
    the env-flag AGENTCOUNCIL_GUI_RERUN. The flag also protects against infinite
    relaunch loops: if it is already set and tkinter is still unavailable —
    immediately a clean error, no second relaunch.

    `-m src.gui.app`: src/gui is now a package, entry point is app.py with
    `if __name__ == "__main__"` (previously this was a flat module src/gui.py)."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        if os.environ.get("AGENTCOUNCIL_GUI_RERUN") == "1":
            print(
                "Ошибка: tkinter недоступен даже в перезапущенном интерпретаторе.\n"
                "Установите Python с Tcl/Tk (python.org) или `uv python install <version>` "
                "с поддержкой tkinter, либо используйте CLI-режим (без --gui).",
                file=sys.stderr,
            )
            sys.exit(1)

        interpreter = _find_interpreter_with_tkinter()
        if interpreter is None:
            print(
                "Ошибка: tkinter не найден ни в одном обнаруженном интерпретаторе Python.\n"
                "Установите Python с Tcl/Tk (python.org) или `uv python install <version>`.\n"
                "Продолжаю в CLI-режиме (используйте --idea/--agents и т.д. без --gui).",
                file=sys.stderr,
            )
            return

        print(
            f"tkinter недоступен в текущем интерпретаторе — перезапуск через {interpreter}..."
        )
        env = dict(os.environ)
        env["AGENTCOUNCIL_GUI_RERUN"] = "1"
        result = subprocess.run(
            [interpreter, "-m", "src.gui.app"],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
        )
        sys.exit(result.returncode)
    else:
        from .app import main as gui_main

        gui_main()

"""GUI layer: re-exports public API for backward compatibility.

Old imports like `from src.gui import CouncilGUI, QueueWriter` still work
(see tests/test_gui.py); entry point is `python -m src.gui.app`.

The re-exports are LAZY (PEP 562): importing this package must not pull in
customtkinter, so the CLI and the CLI-only test suite work in environments
without the GUI extra (`uv sync` without `--extra gui`). Only attribute
ACCESS (CouncilGUI / QueueWriter / main) or a direct submodule import
(`from src.gui import launcher`, `import src.gui.app`) triggers the real
import — and those are exactly the GUI entry points that need it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CouncilGUI, main
    from .worker import QueueWriter


def __getattr__(name: str):
    if name == "CouncilGUI":
        from .app import CouncilGUI

        return CouncilGUI
    if name == "QueueWriter":
        from .worker import QueueWriter

        return QueueWriter
    if name == "main":
        from .app import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CouncilGUI",
    "QueueWriter",
    "main",
]

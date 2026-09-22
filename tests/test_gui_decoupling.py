"""GUI/CLI decoupling (T4, permanent regression — checklist answer 8).

customtkinter is an opt-in extra (`uv sync --extra gui`); the CLI and the
CLI test suite must import and run WITHOUT it. These tests assert:

- `import src.gui` — fine (the package __init__ re-exports lazily, PEP 562);
- `import main` — fine (the root CLI no longer imports the GUI launcher at
  module top — old main.py:14 broke every CLI test without the GUI extra);
- accessing src.gui.CouncilGUI — ImportError (honest: the GUI really is not
  installed here).

All checks run in a SUBPROCESS — an earlier in-process version of this file
poisoned sys.modules (it purged "main" and stood in for "customtkinter"
with a fake mapping) and 10 unrelated tests went red: modules imported
BEFORE the purge kept referencing the old main module object, while later
monkeypatch.setattr(main, ...) patched the freshly re-imported one, so the
fakes were invisible to the old references. A subprocess cannot leak: its
sys.modules dies with it.

The block uses documented import-system semantics: a None entry in
sys.modules makes `import customtkinter` raise a real ImportError (and
retrying raises again — failed imports are never cached). A fake mapping
object was the old bug: the import "succeeded", then src.gui.app blew up
with AttributeError instead of ImportError.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Without the GUI extra — the honest "CLI-only environment" case. A None
# entry in sys.modules makes every `import customtkinter` raise ImportError.
BLOCKED_CHECK = """\
import sys

sys.modules["customtkinter"] = None

import src.gui  # noqa: F401
import main  # noqa: F401

assert callable(main.parse_args)
assert callable(main.run_noninteractive)

# Importing the package must not pull in src.gui.app (it imports
# customtkinter) — laziness is the whole point of the PEP 562 __init__.
assert "src.gui.app" not in sys.modules

try:
    src.gui.CouncilGUI  # attribute access on purpose
except ImportError:
    pass
else:
    raise AssertionError("src.gui.CouncilGUI imported without customtkinter")

# A failed import must not be cached: the retry raises the same ImportError.
try:
    import customtkinter  # noqa: F401
except ImportError:
    pass
else:
    raise AssertionError("blocked customtkinter imported on retry")

# The block is clean: once lifted, the real package imports where the extra
# is actually installed; without it, ImportError is the honest result too.
del sys.modules["customtkinter"]
try:
    import customtkinter  # noqa: F401
except ImportError:
    print("extra-not-installed")
else:
    print("extra-ok")
"""

# With the extra installed — laziness must hold even when the real import
# would succeed.
LAZY_CHECK = """\
import sys

import src.gui

assert "src.gui.app" not in sys.modules
src.gui.CouncilGUI  # attribute access on purpose
assert "src.gui.app" in sys.modules
"""


def _run_check(script: str) -> subprocess.CompletedProcess:
    """Run script in a fresh interpreter rooted at the repo.

    cwd=REPO_ROOT: for `python -c`, sys.path[0] is "" (the cwd), so `src.*`
    and `main` resolve exactly like under pytest (pyproject: pythonpath
    ["."]); PYTHONPATH is set too, for the odd launcher where "" is dropped.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,  # returncode is the assertion payload, not a crash
    )


def test_cli_imports_work_without_customtkinter():
    """The CLI package chain imports with customtkinter hard-blocked.

    Covers, in one isolated interpreter: `import src.gui` + `import main`
    succeed; src.gui stays lazy (no src.gui.app); CouncilGUI access raises
    ImportError (and keeps raising it — no failed-import cache); the block
    is clean — after lifting it the real customtkinter imports again.
    """
    result = _run_check(BLOCKED_CHECK)
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n---\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip() in {"extra-ok", "extra-not-installed"}


def test_gui_package_init_is_lazy_even_with_extra():
    """WITH the extra: `import src.gui` must not import src.gui.app — only
    attribute access does."""
    if importlib.util.find_spec("customtkinter") is None:
        pytest.skip("customtkinter extra not installed in this environment")
    result = _run_check(LAZY_CHECK)
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n---\nstderr:\n{result.stderr}"
    )

"""CouncilWorker evidence copy: must preserve binary content (PDF/DOCX/DOC).

Regression: the worker used to force-decode evidence files as UTF-8 text
and silently skip anything that failed (e.g. any real PDF/DOCX), which made
attaching those formats through the GUI impossible even though the CLI path
(main.py) already copied evidence as raw bytes. No customtkinter import here
— CouncilWorker.run() only touches config/core modules.
"""

import queue
import sys
from types import SimpleNamespace

import pytest

from src.gui.worker import CouncilWorker


class _FakeSession:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "session"
        self.dir.mkdir()

    def write_idea(self, idea):
        pass


def _make_gui(tmp_path, evidence_files):
    return SimpleNamespace(
        evidence_files=evidence_files,
        preset_var=SimpleNamespace(get=lambda: None),
        task_mode_var=SimpleNamespace(get=lambda: False),
        round_timeout_var=SimpleNamespace(get=lambda: 1200),
        tr=lambda key, **kwargs: key.format(**kwargs) if kwargs else key,
        root=SimpleNamespace(after=lambda delay, fn, *a: fn(*a)),
        session_dir=None,
    )


def test_binary_evidence_is_copied_byte_for_byte(tmp_path, monkeypatch):
    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake binary content\n%%EOF"
    src = tmp_path / "paper.pdf"
    src.write_bytes(pdf_bytes)

    session = _FakeSession(tmp_path)
    monkeypatch.setattr("src.core.session.SessionWriter", lambda: session)

    captured = {}

    async def fake_run_council_async(*args, **kwargs):
        captured["evidence_dir"] = kwargs.get("evidence_dir")

    monkeypatch.setattr(
        "src.core.orchestrator.run_council_async", fake_run_council_async
    )

    gui = _make_gui(tmp_path, [src])
    worker = CouncilWorker(
        queue.Queue(), on_status=lambda *a: None, on_finished=lambda *a: None
    )
    worker.run(gui, "task", agents=[("claude", ["claude", "-p", "{prompt}"])])

    copied = session.dir / "evidence" / "paper.pdf"
    assert copied.exists()
    assert copied.read_bytes() == pdf_bytes


def test_missing_evidence_file_is_skipped_not_fatal(tmp_path, monkeypatch):
    missing = tmp_path / "gone.docx"  # never created

    session = _FakeSession(tmp_path)
    monkeypatch.setattr("src.core.session.SessionWriter", lambda: session)

    async def fake_run_council_async(*args, **kwargs):
        pass

    monkeypatch.setattr(
        "src.core.orchestrator.run_council_async", fake_run_council_async
    )

    gui = _make_gui(tmp_path, [missing])
    finished = []
    worker = CouncilWorker(
        queue.Queue(),
        on_status=lambda *a: None,
        on_finished=lambda sd: finished.append(sd),
    )
    worker.run(gui, "task", agents=[("claude", ["claude", "-p", "{prompt}"])])

    assert not (session.dir / "evidence" / "gone.docx").exists()
    assert finished == [session.dir]


@pytest.mark.parametrize("failure", ["create", "write"])
def test_session_setup_failure_schedules_completion_and_restores_streams(
    tmp_path, monkeypatch, failure
):
    class FailingSession(_FakeSession):
        def write_idea(self, idea):
            raise OSError("cannot save idea")

    def create_session():
        if failure == "create":
            raise OSError("cannot create session")
        return FailingSession(tmp_path)

    monkeypatch.setattr("src.core.session.SessionWriter", create_session)
    finished = []
    worker = CouncilWorker(
        queue.Queue(),
        on_status=lambda *args: None,
        on_finished=finished.append,
    )
    stdout, stderr = sys.stdout, sys.stderr

    worker.run(_make_gui(tmp_path, []), "task", agents=[])

    assert finished == [None]
    assert sys.stdout is stdout
    assert sys.stderr is stderr

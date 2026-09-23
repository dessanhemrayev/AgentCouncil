"""Background GUI worker: the same council run as the CLI.

The GUI counts nothing itself — it calls the orchestration
``core.orchestrator.run_council_async`` in a background thread, intercepting its
normal print() output into a queue for the log panel. (Previously it imported
run_council_async from the root CLI file; now the orchestration lives in core,
and the GUI has no dependency on the root.)
"""

import asyncio
import io
import queue
import sys
from pathlib import Path
from typing import List

from src.core.models import CouncilMember
from src.core.logging_utils import get_logger
from src.core.web_utils import fetch_url, is_url, save_fetched_evidence

logger = get_logger("gui.worker")


class QueueWriter(io.TextIOBase):
    def __init__(self, q: "queue.Queue[str]", on_line=None):
        self._q = q
        self._on_line = on_line
        self._buffer = ""

    def write(self, s: str) -> int:
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._q.put(line)
            if self._on_line is not None:
                self._on_line(line)
        return len(s)

    def flush(self) -> None:
        pass


class CouncilWorker:
    """Council run in the background thread (formerly CouncilGUI._run_worker).

    gui — CouncilGUI (reads preset/task_mode/timeout/evidence files);
    on_status(agent_name, status, stage), on_stage(stage) and
    on_finished(session_dir|None) — GUI callbacks (thread-safe via root.after
    in the GUI itself).
    """

    def __init__(
        self,
        log_queue: "queue.Queue[str]",
        on_status,
        on_finished,
        on_stage=None,
    ):
        self._log_queue = log_queue
        self._on_status = on_status
        self._on_finished = on_finished
        self._on_stage = on_stage

    def run(self, gui, task: str, agents: List[CouncilMember]) -> None:
        from ..core.orchestrator import run_council_async
        from ..core.session import SessionWriter

        session = SessionWriter()
        session.write_idea(task)
        queue_writer = QueueWriter(
            self._log_queue, getattr(session, "write_console", None)
        )
        old_stdout, old_stderr = sys.stdout, sys.stderr
        setattr(sys, "stdout", queue_writer)
        setattr(sys, "stderr", queue_writer)

        try:
            evidence_dir = None
            if gui.evidence_files:
                evidence_dir = session.dir / "evidence"
                evidence_dir.mkdir(parents=True, exist_ok=True)
                for ev_item in gui.evidence_files:
                    if is_url(ev_item):
                        # Fetch URL and save as evidence
                        logger.info("Fetching evidence from URL: %s", ev_item)
                        try:
                            content, content_type = fetch_url(ev_item)
                            saved_path = save_fetched_evidence(
                                evidence_dir, ev_item, content, content_type
                            )
                            logger.info(
                                "  Evidence: %s -> %s", ev_item, saved_path.name
                            )
                        except Exception as exc:
                            logger.warning(
                                gui.tr("skip_evidence", name=ev_item, error=exc)
                            )
                            continue
                    else:
                        src_path = Path(ev_item)
                        try:
                            data = src_path.read_bytes()
                        except OSError as exc:
                            # A missing/unreadable file must not crash the run — skip with a log entry.
                            logger.warning(
                                gui.tr("skip_evidence", name=src_path.name, error=exc)
                            )
                            continue
                        target = evidence_dir / src_path.name
                        if target.exists():
                            # Name collision — suffix _2, _3, ...
                            stem, suffix = src_path.stem, src_path.suffix
                            n = 2
                            while target.exists():
                                target = evidence_dir / f"{stem}_{n}{suffix}"
                                n += 1
                        # Bytes, not text: evidence can be binary (PDF/DOCX/DOC) — the
                        # council agents get the file path and read it with their own
                        # tools; only plain-text evidence is quote-verifiable (verification.py).
                        target.write_bytes(data)

            try:
                round_timeout = max(1, int(gui.round_timeout_var.get()))
            except (TypeError, ValueError):
                round_timeout = 1200
                logger.warning(
                    gui.tr(
                        "invalid_timeout",
                        value=gui.round_timeout_var.get(),
                    )
                )

            asyncio.run(
                run_council_async(
                    task,
                    agents,
                    session,
                    quick_mode=False,
                    preset=gui.preset_var.get(),
                    no_open=True,
                    evidence_dir=evidence_dir,
                    task_mode=gui.task_mode_var.get(),
                    round_timeout=round_timeout,
                    on_agent_status=self._on_status,
                    on_stage=self._on_stage,
                )
            )

            gui.session_dir = session.dir
            gui.root.after(0, self._on_finished, session.dir)

        except Exception as exc:
            logger.error(gui.tr("worker_error", error=exc))
            gui.root.after(0, self._on_finished, None)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

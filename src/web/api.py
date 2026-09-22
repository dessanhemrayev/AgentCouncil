"""Web API endpoints for AgentCouncil dashboard."""

import asyncio
import io
import json
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse

from ..cli.render import render_claims_map_lines
from ..config import load_config
from ..config.agents import (
    add_member_to_config,
    build_roster,
    discover_agents,
    member_available,
    select_members,
)
from ..core.models import CouncilMember
from ..core.session import SessionWriter
from .schemas import (
    AgentConfig,
    AgentsResponse,
    RunResponse,
    SessionDetail,
    SessionSummary,
    WSAgentStatus,
    WSError,
    WSFinished,
    WSLog,
    WSProgress,
    WSStageChange,
)


# Session management. Only one run at a time (see start_council) — this keeps
# a single global stdout/stderr redirect (for the live log stream) safe and
# makes ACTIVE_RUNS trivial: at most one entry, keyed by session_id.
SESSIONS_DIR = Path("sessions")
ACTIVE_RUNS: dict[str, dict] = {}  # session_id -> {started_at, websocket}

_SESSION_ID_RE = re.compile(r"run-\d+")


def _resolve_session_dir(session_id: str) -> Path:
    """Validate session_id and resolve it to a directory under SESSIONS_DIR.

    session_id comes straight from the URL path, so it must be checked against
    the exact "run-NNN" shape before being joined onto SESSIONS_DIR — otherwise
    a value like "../../etc" escapes the sessions folder entirely, and any
    Path.relative_to() check performed against that already-escaped directory
    (rather than against SESSIONS_DIR itself) would never catch it.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return SESSIONS_DIR / session_id


async def _ws_send(websocket: WebSocket, text: str) -> None:
    try:
        await websocket.send_text(text)
    except Exception:
        pass


def get_session_dirs() -> List[Path]:
    """Get all session directories sorted by run number (newest first)."""
    if not SESSIONS_DIR.exists():
        return []
    dirs = [d for d in SESSIONS_DIR.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.name, reverse=True)
    return dirs


def parse_session_dir(session_dir: Path) -> Optional[SessionSummary]:
    """Parse a session directory into a SessionSummary."""
    match = re.fullmatch(r"run-(\d+)", session_dir.name)
    if not match:
        return None

    run_number = int(match.group(1))
    idea_path = session_dir / "idea.md"
    meta_path = session_dir / "meta.json"
    verdict_path = session_dir / "verdict.md"
    task_verdict_path = session_dir / "task-verdict.md"

    idea_preview = ""
    if idea_path.exists():
        idea_preview = idea_path.read_text(encoding="utf-8")[:200]

    agents = []
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            agents = [a.get("name", "") for a in meta.get("agents", [])]
        except Exception:
            pass

    if session_dir.name in ACTIVE_RUNS:
        status = "running"
    elif verdict_path.exists() or task_verdict_path.exists():
        status = "completed"
    else:
        status = "failed"

    created_at = datetime.fromtimestamp(session_dir.stat().st_ctime)

    return SessionSummary(
        session_id=session_dir.name,
        run_number=run_number,
        created_at=created_at,
        idea_preview=idea_preview,
        status=status,
        agents=agents,
        verdict_path=str(verdict_path) if verdict_path.exists() else None,
        task_verdict_path=str(task_verdict_path)
        if task_verdict_path.exists()
        else None,
    )


def load_session_detail(session_dir: Path) -> Optional[SessionDetail]:
    """Load full session detail."""
    summary = parse_session_dir(session_dir)
    if not summary:
        return None

    detail = SessionDetail(**summary.model_dump())

    verdict_path = session_dir / "verdict.md"
    if verdict_path.exists():
        detail.verdict_markdown = verdict_path.read_text(encoding="utf-8")

    task_verdict_path = session_dir / "task-verdict.md"
    if task_verdict_path.exists():
        detail.task_verdict_markdown = task_verdict_path.read_text(encoding="utf-8")

    for round_name in ["round1", "round2", "round3"]:
        round_dir = session_dir / round_name
        if round_dir.exists():
            detail.rounds[round_name] = {}
            for md_file in round_dir.glob("*.md"):
                agent_name = md_file.stem
                detail.rounds[round_name][agent_name] = md_file.read_text(
                    encoding="utf-8"
                )

    vote_path = session_dir / "vote.json"
    if vote_path.exists():
        try:
            detail.vote = json.loads(vote_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    claims_path = session_dir / "claims.json"
    if claims_path.exists():
        try:
            detail.claims_map = json.loads(claims_path.read_text(encoding="utf-8"))
            # claims_map is a per-agent CLM-id x round matrix (variants/r2/r3
            # nested per agent) — rather than re-deriving a flat table in JS
            # and risking a mismatch, reuse the same renderer the CLI already
            # uses (render_claims_map_lines) and show it as preformatted text.
            detail.claims_map_text = "\n".join(
                render_claims_map_lines(detail.claims_map)
            )
        except Exception:
            pass

    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        try:
            detail.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    mismatch_path = session_dir / "citation_mismatches.json"
    if mismatch_path.exists():
        try:
            data = json.loads(mismatch_path.read_text(encoding="utf-8"))
            detail.citation_mismatches = data.get("mismatches", [])
        except Exception:
            pass

    return detail


class _WebSocketLogWriter(io.TextIOBase):
    """Redirect target for sys.stdout/sys.stderr during a run.

    Mirrors src/gui/worker.py's QueueWriter (buffer to full lines, forward
    each one), but pushes WSLog frames over the socket instead of into a
    queue.Queue. Safe to swap sys.stdout globally for the run's duration only
    because start_council refuses a second concurrent run (ACTIVE_RUNS) — one
    run's prints can never interleave with another's.
    """

    def __init__(self, get_websocket):
        self._get_websocket = get_websocket
        self._buffer = ""

    def write(self, s: str) -> int:
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            websocket = self._get_websocket()
            if websocket and line:
                msg = WSLog(line=line)
                asyncio.create_task(_ws_send(websocket, msg.model_dump_json()))
        return len(s)

    def flush(self) -> None:
        pass


async def run_council_background(
    session_id: str,
    session: SessionWriter,
    task: str,
    agent_names: str,
    preset: Optional[str],
    task_mode: bool,
    round_timeout: int,
    evidence_urls: List[str],
    evidence_uploads: List[tuple[str, bytes]],
) -> None:
    """Run council in background, streaming updates over WebSocket.

    The WebSocket is looked up dynamically from ACTIVE_RUNS[session_id] on
    every callback rather than being fixed at call time: the run is started
    right away from POST /api/council/start (no socket yet), and a client
    typically only opens /ws/{session_id} a moment later. Re-running this
    whole function to "attach" a late socket would start a second, parallel
    council on the same session — this way the same run just starts emitting
    once ACTIVE_RUNS[session_id]["websocket"] is set.
    """
    from ..core.orchestrator import run_council_async
    from ..core.web_utils import fetch_url, is_url, save_fetched_evidence

    run_dir = session.dir
    evidence_dir = run_dir / "evidence"

    session.write_idea(task)

    if evidence_urls or evidence_uploads:
        evidence_dir.mkdir(parents=True, exist_ok=True)
    for url in evidence_urls:
        if not is_url(url):
            continue
        try:
            content, content_type = fetch_url(url)
            save_fetched_evidence(evidence_dir, url, content, content_type)
        except Exception:
            pass
    for filename, data in evidence_uploads:
        # Path(...).name strips any directory components a crafted filename
        # might carry — the upload must land inside evidence_dir, nowhere else.
        safe_name = Path(filename).name or "evidence"
        target = evidence_dir / safe_name
        if target.exists():
            stem, suffix = target.stem, target.suffix
            n = 2
            while target.exists():
                target = evidence_dir / f"{stem}_{n}{suffix}"
                n += 1
        target.write_bytes(data)

    config = load_config()
    roster = build_roster(config, discover_agents())
    council = select_members(roster, agent_names or None, config)

    def _current_websocket() -> Optional[WebSocket]:
        run_info = ACTIVE_RUNS.get(session_id)
        return run_info.get("websocket") if run_info else None

    # Deterministic progress denominator — same formula as the desktop GUI
    # (src/gui/app.py: call_done/call_total), so the two stay comparable.
    run_started = time.monotonic()
    counters = {
        "done": 0,
        "total": (3 * len(council) + 1) if task_mode else 3 * len(council),
    }

    # Callbacks for WebSocket updates. orchestrator.run_council_async calls
    # on_agent_status/on_stage synchronously (see _make_stage_cb/_emit_stage
    # in src/core/orchestrator.py) — they must stay plain `def`s, not
    # `async def`s, otherwise calling them just builds a coroutine that is
    # never awaited and nothing is ever sent.
    def on_agent_status(agent_name: str, status: str, stage: str) -> None:
        websocket = _current_websocket()
        if websocket:
            msg = WSAgentStatus(
                agent=agent_name,
                status=status,
                stage=stage,
                detail="",
                timestamp=asyncio.get_event_loop().time(),
            )
            asyncio.create_task(_ws_send(websocket, msg.model_dump_json()))

        if status == "running" and stage.startswith("task"):
            # Task-mode work/review is dynamic: each new call grows the
            # progress denominator (mirrors app.py's _update_agent_status).
            counters["total"] += 1
            return
        if status not in ("done", "error", "timeout"):
            return
        counters["done"] += 1
        if websocket:
            progress_msg = WSProgress(
                done=counters["done"],
                total=counters["total"],
                elapsed=f"{int(time.monotonic() - run_started)}s",
            )
            asyncio.create_task(_ws_send(websocket, progress_msg.model_dump_json()))

    def on_stage(stage: str) -> None:
        websocket = _current_websocket()
        if websocket:
            msg = WSStageChange(stage=stage)
            asyncio.create_task(_ws_send(websocket, msg.model_dump_json()))

    log_writer = _WebSocketLogWriter(_current_websocket)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = log_writer
    sys.stderr = log_writer

    try:
        await run_council_async(
            task,
            council,
            session,
            quick_mode=False,
            preset=preset,
            no_open=True,
            evidence_dir=evidence_dir if evidence_dir.exists() else None,
            config_path=None,
            adversarial=False,
            task_mode=task_mode,
            round_timeout=float(round_timeout),
            on_agent_status=on_agent_status,
            on_stage=on_stage,
        )

        websocket = _current_websocket()
        if websocket:
            verdict_path = run_dir / ("task-verdict.md" if task_mode else "verdict.md")
            finished_msg = WSFinished(
                session_dir=str(run_dir),
                verdict_path=str(verdict_path) if verdict_path.exists() else "",
            )
            await _ws_send(websocket, finished_msg.model_dump_json())

    except Exception as exc:
        websocket = _current_websocket()
        if websocket:
            error_msg = WSError(message=str(exc))
            await _ws_send(websocket, error_msg.model_dump_json())
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        ACTIVE_RUNS.pop(session_id, None)


# API Endpoints
def create_api_router(app: FastAPI):
    """Register API routes on the FastAPI app."""

    @app.get("/api/sessions", response_model=List[SessionSummary])
    async def list_sessions():
        """List all council sessions."""
        sessions = []
        for session_dir in get_session_dirs():
            summary = parse_session_dir(session_dir)
            if summary:
                sessions.append(summary)
        return sessions

    @app.get("/api/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(session_id: str):
        """Get full session detail."""
        session_dir = _resolve_session_dir(session_id)
        if not session_dir.exists():
            raise HTTPException(status_code=404, detail="Session not found")
        detail = load_session_detail(session_dir)
        if not detail:
            raise HTTPException(status_code=404, detail="Session not found")
        return detail

    def _agent_config(member: CouncilMember) -> AgentConfig:
        return AgentConfig(
            id=member.id,
            name=member.name,
            kind=member.kind,
            command=member.command,
            model=member.model,
            available=member_available(member),
            is_default=member.is_default,
            enabled=member.enabled,
        )

    @app.get("/api/agents", response_model=AgentsResponse)
    async def list_agents():
        """List the full roster (council.json members + autodiscovered CLIs)."""
        config = load_config()
        roster = build_roster(config, discover_agents())
        return AgentsResponse(agents=[_agent_config(m) for m in roster])

    @app.post("/api/agents", response_model=AgentConfig)
    async def add_agent(
        kind: str = Form(...),
        id: str = Form(...),
        name: str = Form(""),
        command: str = Form(""),
        model: str = Form(""),
        api_key_env: str = Form(""),
        aliases: str = Form(""),
        is_default: bool = Form(True),
    ):
        """Add a new agent to council.json's members[] (persisted immediately).

        Validated by the same member_from_config() the config loader itself
        uses (src/config/agents.py) — an entry that passes here can never be
        rejected on the next load. GUI and CLI pick it up automatically
        (both build their roster from council.json + PATH on every launch).
        """
        entry: dict = {
            "id": id.strip(),
            "name": name.strip() or id.strip(),
            "type": kind.strip().lower(),
            "default": is_default,
            "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
        }
        if entry["type"] == "cli":
            try:
                entry["command"] = shlex.split(command)
            except ValueError as exc:
                # Unbalanced quotes etc. — shlex.split's own error.
                raise HTTPException(status_code=400, detail=f"invalid command: {exc}")
        else:
            entry["model"] = model.strip()
            if api_key_env.strip():
                entry["api_key_env"] = api_key_env.strip()

        config = load_config()
        try:
            member = add_member_to_config(config, entry)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to save council.json: {exc}"
            )

        return _agent_config(member)

    @app.post("/api/council/start", response_model=RunResponse)
    async def start_council(
        task: str = Form(...),
        agents: str = Form(""),
        preset: Optional[str] = Form(None),
        task_mode: bool = Form(False),
        round_timeout: int = Form(1200),
        evidence_urls: str = Form(""),
        evidence_files: List[UploadFile] = File([]),
    ):
        """Start a new council run (multipart — evidence_files are real uploads)."""
        if not task.strip():
            raise HTTPException(status_code=400, detail="task must not be empty")
        if ACTIVE_RUNS:
            # One run at a time (see _WebSocketLogWriter docstring) — also
            # keeps the WS status grid from mixing updates from two runs.
            raise HTTPException(
                status_code=409, detail="A council run is already in progress"
            )

        urls = [line.strip() for line in evidence_urls.splitlines() if line.strip()]
        uploads = [(f.filename, await f.read()) for f in evidence_files if f.filename]

        # SessionWriter allocates AND creates the run-NNN directory itself
        # (next_session_dir) — reusing that instead of hand-rolling a number
        # here avoids both a collision race and an orphaned leftover directory.
        session = SessionWriter(SESSIONS_DIR)
        session_id = session.dir.name

        ACTIVE_RUNS[session_id] = {
            "started_at": datetime.now(),
            "websocket": None,
        }

        # Run in background — starts immediately, streams once a client
        # attaches a websocket to ACTIVE_RUNS[session_id] below.
        asyncio.create_task(
            run_council_background(
                session_id=session_id,
                session=session,
                task=task,
                agent_names=agents,
                preset=preset or None,
                task_mode=task_mode,
                round_timeout=round_timeout,
                evidence_urls=urls,
                evidence_uploads=uploads,
            )
        )

        return RunResponse(session_id=session_id)

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        """WebSocket for real-time updates during council run."""
        await websocket.accept()

        # Attach to an already-running background task, if any — the run was
        # already started by POST /api/council/start; this only starts
        # streaming updates to it, it must NOT start a second run.
        run_info = ACTIVE_RUNS.get(session_id)
        if run_info is not None:
            run_info["websocket"] = websocket
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if ACTIVE_RUNS.get(session_id) is run_info:
                    run_info["websocket"] = None
        else:
            # No active run (already finished, or unknown session) — just
            # keep the connection alive for polling.
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass

    @app.get("/api/sessions/{session_id}/files/{file_path:path}")
    async def get_session_file(session_id: str, file_path: str):
        """Serve a file from a session directory."""
        session_dir = _resolve_session_dir(session_id)
        if not session_dir.exists():
            raise HTTPException(status_code=404, detail="Session not found")

        file_full_path = session_dir / file_path
        # Security: ensure file is within session directory
        try:
            file_full_path.resolve().relative_to(session_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        if not file_full_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(file_full_path)

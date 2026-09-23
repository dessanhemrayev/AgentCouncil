import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TextIO

from .models import AgentResult


def slugify(name: str) -> str:
    """Agent name -> filesystem-safe file name.
    'Gemini CLI' -> 'gemini-cli', 'DeepSeek Harness' -> 'deepseek-harness'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "agent"


def next_session_dir(base: Path) -> Path:
    """Sessions are stored as sessions/run-001, run-002, ... — a new run
    gets the next free number (scanning the existing ones)."""
    base.mkdir(parents=True, exist_ok=True)

    existing = []
    for child in base.iterdir():
        if child.is_dir():
            match = re.fullmatch(r"run-(\d+)", child.name)
            if match:
                existing.append(int(match.group(1)))

    number = max(existing, default=0) + 1
    session_dir = base / f"run-{number:03d}"
    session_dir.mkdir(parents=True)
    return session_dir


class SessionWriter:
    """Writes each council move to disk as results arrive — so reasoning is
    not lost on a crash/long run and can be read/diffed afterwards:

    sessions/run-001/
      idea.md
      round1/claude-code.md
      round2/claude-code.md
      round2/claude-code.json      (structured part, if it parsed)
      round3/claude-code.md
      vote.json
    events.jsonl
    """

    def __init__(self, base: Optional[Path] = None):
        self.dir = next_session_dir(base if base is not None else Path("sessions"))
        (self.dir / "artifacts").mkdir(parents=True, exist_ok=True)

    def write_idea(self, idea: str) -> None:
        (self.dir / "idea.md").write_text(idea, encoding="utf-8")

    @property
    def idea_path(self) -> Path:
        return self.dir / "idea.md"

    @property
    def artifacts_dir(self) -> Path:
        return self.dir / "artifacts"

    def _round_dir(self, round_name: str) -> Path:
        round_dir = self.dir / round_name
        round_dir.mkdir(parents=True, exist_ok=True)
        return round_dir

    def agent_path(self, round_name: str, agent_name: str) -> Path:
        """Path to the agent's file in the round. Does not check existence —
        this is for the NEXT round's prompt to LINK to (the file must already
        be written by write_agent_result by the time the path is needed)."""
        return self.dir / round_name / f"{slugify(agent_name)}.md"

    def write_agent_result(
        self,
        round_name: str,
        agent_name: str,
        result: AgentResult,
        parsed_json: Optional[dict] = None,
    ) -> Path:
        """Writes ONE agent's answer as soon as it arrives (not a batch at
        the end of the round) — important for Round 3: the next agent in
        turn order must be able to read the previous agent's file before
        its own move."""
        round_dir = self._round_dir(round_name)
        slug = slugify(agent_name)

        if result.error:
            text = f"# {agent_name} — ERROR\n\n{result.error}\n"
            if result.output:
                text += f"\n## STDOUT\n\n{result.output}\n"
        else:
            text = result.output

        path = round_dir / f"{slug}.md"
        path.write_text(text, encoding="utf-8")

        if parsed_json is not None:
            (round_dir / f"{slug}.json").write_text(
                json.dumps(parsed_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return path

    def write_round(
        self,
        round_name: str,
        results: Dict[str, AgentResult],
        json_blocks: Optional[Dict[str, dict]] = None,
    ) -> None:
        json_blocks = json_blocks or {}

        for name, result in results.items():
            self.write_agent_result(round_name, name, result, json_blocks.get(name))

    def write_vote(self, summary: dict) -> None:
        (self.dir / "vote.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_citation_mismatches(self, mismatches: List[str]) -> None:
        """Write citation verification mismatches to session for verdict.md."""
        (self.dir / "citation_mismatches.json").write_text(
            json.dumps({"mismatches": mismatches}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_claims_map(self, claims_map: dict) -> None:
        """The CLM x round map (council.aggregate_claims) to disk, separate
        from verdict.md — so the GUI can re-render the map without
        re-running the council."""
        (self.dir / "claims.json").write_text(
            json.dumps(claims_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_meta(self, meta: dict) -> None:
        """Wall-time, number of agent calls, command versions — without
        token pricing (CLIs report it inconsistently)."""
        (self.dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_event(self, event: str, **data: object) -> None:
        """Append one timestamped machine-readable event to the run journal."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def write_console(self, line: str) -> None:
        """Append one exact stdout/stderr line to the session console log."""
        with (self.dir / "console.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def write_vote_trajectory(self, trajectory: dict) -> None:
        """Per-agent R2 -> R3 score trajectory (council.compute_vote_trajectory).
        Separate from vote.json — different semantics (dynamics, not the final)."""
        (self.dir / "vote_trajectory.json").write_text(
            json.dumps(trajectory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_verdict(self, markdown: str) -> Path:
        """The main run artifact — the claims map + the secondary voting
        metric, in one human-readable file (auto-opened after the run)."""
        path = self.dir / "verdict.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def write_executor(self, executor: dict) -> None:
        """executor.json — votes, the chosen one, and the reason. The single
        source of truth for GUI/CLI (both must show an identical result)."""
        (self.dir / "executor.json").write_text(
            json.dumps(executor, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def write_work(self, text: str) -> Path:
        """work.md — the executor's stdout verbatim (Python does not touch
        the content)."""
        path = self.dir / "work.md"
        path.write_text(text, encoding="utf-8")
        return path

    def write_task_review(
        self, iteration: int, reviews: dict, missing: List[str]
    ) -> None:
        """review-N.json — verdicts/critical_flaws/suggested_edits +
        unrecognized votes; one file read by both the GUI and the CLI."""
        payload = {"iteration": iteration, "reviews": reviews, "missing": missing}
        (self.dir / f"review-{iteration}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def write_task_verdict(self, markdown: str) -> Path:
        """task-verdict.md — the main artifact of task mode (for humans;
        the machine-readable version is task_verdict.json)."""
        path = self.dir / "task-verdict.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def write_task_verdict_json(self, task_out: dict) -> None:
        """task_verdict.json — the single JSON file read by the GUI
        (markdown is the human-facing version)."""
        (self.dir / "task_verdict.json").write_text(
            json.dumps(task_out, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class SessionTee(io.TextIOBase):
    """Mirror console output to its original stream and the session log."""

    def __init__(self, stream: TextIO, session: SessionWriter):
        self._stream = stream
        self._write_console = getattr(session, "write_console", None)
        self._buffer = ""

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if self._write_console is not None:
                self._write_console(line)
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

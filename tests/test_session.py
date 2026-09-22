"""Tests for src/session.py — writing council moves to disk (sessions/run-NNN/...)."""

import json
from pathlib import Path

import pytest

from src.core.models import AgentResult
from src.core.session import SessionWriter, next_session_dir, slugify


@pytest.fixture
def session(tmp_path):
    return SessionWriter(tmp_path / "sessions")


class TestSlugify:
    def test_lowercases_and_spaces_to_dashes(self):
        assert slugify("Gemini CLI") == "gemini-cli"

    def test_strips_edge_punctuation(self):
        assert slugify("  DeepSeek Harness!! ") == "deepseek-harness"

    def test_empty_falls_back_to_agent(self):
        assert slugify("") == "agent"
        assert slugify("---") == "agent"


class TestNextSessionDir:
    def test_first_run_is_001(self, tmp_path):
        base = tmp_path / "sessions"

        result = next_session_dir(base)

        assert result == base / "run-001"
        assert result.is_dir()

    def test_increments_past_existing_runs(self, tmp_path):
        base = tmp_path / "sessions"
        (base / "run-001").mkdir(parents=True)
        (base / "run-002").mkdir(parents=True)

        result = next_session_dir(base)

        assert result == base / "run-003"

    def test_ignores_non_matching_directories(self, tmp_path):
        base = tmp_path / "sessions"
        (base / "run-001").mkdir(parents=True)
        (base / "not-a-run").mkdir(parents=True)
        (base / "run-abc").mkdir(parents=True)

        result = next_session_dir(base)

        assert result == base / "run-002"

    def test_creates_base_when_missing(self, tmp_path):
        base = tmp_path / "does" / "not" / "exist"

        result = next_session_dir(base)

        assert result == base / "run-001"


class TestSessionWriter:
    def test_write_idea(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")

        session.write_idea("моя идея")

        assert (session.dir / "idea.md").read_text(encoding="utf-8") == "моя идея"

    def test_two_writers_get_separate_run_dirs(self, tmp_path):
        base = tmp_path / "sessions"

        first = SessionWriter(base=base)
        second = SessionWriter(base=base)

        assert first.dir != second.dir
        assert first.dir.name == "run-001"
        assert second.dir.name == "run-002"

    def test_write_round_creates_md_per_agent(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        results = {
            "Claude Code": AgentResult(name="Claude Code", output="ответ claude"),
            "Gemini CLI": AgentResult(name="Gemini CLI", output="ответ gemini"),
        }

        session.write_round("round1", results)

        round_dir = session.dir / "round1"
        assert (round_dir / "claude-code.md").read_text(
            encoding="utf-8"
        ) == "ответ claude"
        assert (round_dir / "gemini-cli.md").read_text(
            encoding="utf-8"
        ) == "ответ gemini"

    def test_write_round_error_goes_into_md_with_marker(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        results = {
            "A": AgentResult(name="A", output="", error="упс"),
        }

        session.write_round("round1", results)

        text = (session.dir / "round1" / "a.md").read_text(encoding="utf-8")
        assert "ERROR" in text
        assert "упс" in text

    def test_write_round_error_with_output_includes_stdout(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        results = {
            "A": AgentResult(name="A", output="partial output", error="код 1"),
        }

        session.write_round("round1", results)

        text = (session.dir / "round1" / "a.md").read_text(encoding="utf-8")
        assert "код 1" in text
        assert "STDOUT" in text
        assert "partial output" in text

    def test_write_round_saves_json_block_when_given(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        results = {"A": AgentResult(name="A", output="text with json")}
        json_blocks = {"A": {"revised_position": "xyz"}}

        session.write_round("round2", results, json_blocks)

        round_dir = session.dir / "round2"
        assert (round_dir / "a.md").read_text(encoding="utf-8") == "text with json"
        saved = json.loads((round_dir / "a.json").read_text(encoding="utf-8"))
        assert saved == {"revised_position": "xyz"}

    def test_write_round_skips_json_file_when_not_given(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        results = {"A": AgentResult(name="A", output="no structured part")}

        session.write_round("round2", results)

        assert not (session.dir / "round2" / "a.json").exists()

    def test_write_vote_writes_pretty_json(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        summary = {"votes": {"A": {"score": 7, "verdict": "ok"}}, "missing": []}

        session.write_vote(summary)

        saved = json.loads((session.dir / "vote.json").read_text(encoding="utf-8"))
        assert saved == summary

    def test_default_base_is_sessions_relative_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        session = SessionWriter()

        assert session.dir == Path("sessions") / "run-001"
        assert (tmp_path / "sessions" / "run-001").is_dir()

    def test_write_citation_mismatches_writes_pretty_json(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")

        session.write_citation_mismatches(["[A R2 CLM-1] QUOTE MISMATCH: ..."])

        saved = json.loads(
            (session.dir / "citation_mismatches.json").read_text(encoding="utf-8")
        )
        assert saved == {"mismatches": ["[A R2 CLM-1] QUOTE MISMATCH: ..."]}

    def test_write_claims_map_writes_pretty_json(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        claims_map = {
            "claims": [{"id": "CLM-1", "final_status": "RESOLVED"}],
            "untracked_r2": [],
        }

        session.write_claims_map(claims_map)

        saved = json.loads((session.dir / "claims.json").read_text(encoding="utf-8"))
        assert saved == claims_map

    def test_write_meta_writes_pretty_json(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        meta = {"wall_time_seconds": 12.3, "agent_calls": 5}

        session.write_meta(meta)

        saved = json.loads((session.dir / "meta.json").read_text(encoding="utf-8"))
        assert saved == meta

    def test_write_vote_trajectory_writes_pretty_json(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")
        trajectory = {
            "trajectory": [{"agent": "A", "r2_score": 8, "r3_score": 6}],
            "r3_mean": 6,
        }

        session.write_vote_trajectory(trajectory)

        saved = json.loads(
            (session.dir / "vote_trajectory.json").read_text(encoding="utf-8")
        )
        assert saved == trajectory

    def test_write_verdict_writes_file_and_returns_path(self, tmp_path):
        session = SessionWriter(base=tmp_path / "sessions")

        path = session.write_verdict("# Verdict\n\nsome content\n")

        assert path == session.dir / "verdict.md"
        assert path.read_text(encoding="utf-8") == "# Verdict\n\nsome content\n"


class TestTaskModeArtifacts:
    def test_write_executor_and_verdict(self, session):
        session.write_executor(
            {"winner": "claude", "reason": "majority", "votes": {}, "missing": []}
        )
        assert (session.dir / "executor.json").exists()

        session.write_work("work text")
        assert (session.dir / "work.md").read_text(encoding="utf-8") == "work text"

        session.write_task_review(1, {"a": None}, ["b"])
        data = json.loads((session.dir / "review-1.json").read_text(encoding="utf-8"))
        assert data == {"iteration": 1, "reviews": {"a": None}, "missing": ["b"]}

        session.write_task_verdict_json({"status": "APPROVED"})
        assert (
            json.loads((session.dir / "task_verdict.json").read_text(encoding="utf-8"))[
                "status"
            ]
            == "APPROVED"
        )

        p = session.write_task_verdict("# md")
        assert p.exists()

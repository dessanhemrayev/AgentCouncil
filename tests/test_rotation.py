"""Tests for the Round 3 first-mover rotation across runs via council.json.

run_council_async runs rounds itself (not through ask_agents_manually/input, as
in test_main_flow.py), so rounds are substituted directly and council.json
lives in an isolated tmp-cwd (monkeypatch.chdir), not in the repository.
"""

import asyncio
import json

import pytest

import main
from src.core.models import AgentResult


def run(coro):
    return asyncio.run(coro)


class _FakeSession:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "sessions" / "run-001"
        self.dir.mkdir(parents=True)

    def write_idea(self, idea):
        pass

    def write_vote(self, summary):
        pass

    def write_citation_mismatches(self, mismatches):
        pass

    def write_claims_map(self, claims_map):
        pass

    def write_vote_trajectory(self, trajectory):
        pass

    def write_meta(self, meta):
        pass

    def write_verdict(self, markdown):
        return self.dir / "verdict.md"


@pytest.fixture
def patched_rounds(monkeypatch):
    move_orders = []

    async def fake_round1(
        idea,
        council,
        session,
        evidence_dir=None,
        quick_mode=False,
        on_agent_status=None,
        round_timeout=600.0,
    ):
        return (
            {name: AgentResult(name=name, output=f"r1 {name}") for name, _ in council},
            {},
            "degraded",
        )

    async def fake_round2(
        idea,
        council,
        r1,
        session,
        evidence_dir=None,
        preset=None,
        adversarial=False,
        on_agent_status=None,
        round_timeout=600.0,
    ):
        return {
            name: AgentResult(name=name, output=f"r2 {name}") for name, _ in council
        }

    async def fake_round3(
        idea,
        council,
        r1,
        r2,
        session,
        start_index=0,
        on_move=None,
        evidence_dir=None,
        on_agent_status=None,
        round_timeout=600.0,
    ):
        rotated = council[start_index:] + council[:start_index]
        move_orders.append([name for name, _ in rotated])
        return {
            name: AgentResult(name=name, output=f"r3 {name}") for name, _ in council
        }

    monkeypatch.setattr("src.core.orchestrator.run_round1", fake_round1)
    monkeypatch.setattr("src.core.orchestrator.run_round2", fake_round2)
    monkeypatch.setattr("src.core.orchestrator.run_round3", fake_round3)

    return move_orders


class TestRound3Rotation:
    def test_first_run_starts_at_zero_and_writes_rotation_one(
        self, tmp_path, monkeypatch, patched_rounds
    ):
        monkeypatch.chdir(tmp_path)
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"])]

        run(
            main.run_council_async(
                "idea", council, session, quick_mode=False, preset=None, no_open=True
            )
        )

        assert patched_rounds[-1] == ["A", "B"]
        saved = json.loads((tmp_path / "council.json").read_text(encoding="utf-8"))
        assert saved["round3_rotation"] == 1

    def test_second_run_rotates_and_increments(
        self, tmp_path, monkeypatch, patched_rounds
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "council.json").write_text(
            json.dumps({"round3_rotation": 1}), encoding="utf-8"
        )
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"])]

        run(
            main.run_council_async(
                "idea", council, session, quick_mode=False, preset=None, no_open=True
            )
        )

        assert patched_rounds[-1] == ["B", "A"]
        saved = json.loads((tmp_path / "council.json").read_text(encoding="utf-8"))
        assert saved["round3_rotation"] == 2

    def test_rotation_preserves_other_config_keys(
        self, tmp_path, monkeypatch, patched_rounds
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "council.json").write_text(
            json.dumps({"round3_rotation": 0, "mode": "full"}), encoding="utf-8"
        )
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"])]

        run(
            main.run_council_async(
                "idea", council, session, quick_mode=False, preset=None, no_open=True
            )
        )

        saved = json.loads((tmp_path / "council.json").read_text(encoding="utf-8"))
        assert saved["mode"] == "full"
        assert saved["round3_rotation"] == 1

    def test_missing_council_json_defaults_to_zero(
        self, tmp_path, monkeypatch, patched_rounds
    ):
        monkeypatch.chdir(tmp_path)
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]

        run(
            main.run_council_async(
                "idea", council, session, quick_mode=False, preset=None, no_open=True
            )
        )

        assert patched_rounds[-1] == ["A", "B", "C"]

    def test_rotation_wraps_around_council_size(
        self, tmp_path, monkeypatch, patched_rounds
    ):
        monkeypatch.chdir(tmp_path)
        # 3 agents, counter is already 5 -> 5 % 3 == 2
        (tmp_path / "council.json").write_text(
            json.dumps({"round3_rotation": 5}), encoding="utf-8"
        )
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]

        run(
            main.run_council_async(
                "idea", council, session, quick_mode=False, preset=None, no_open=True
            )
        )

        assert patched_rounds[-1] == ["C", "A", "B"]

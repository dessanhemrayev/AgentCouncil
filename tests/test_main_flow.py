"""End-to-end tests for main(): round gating and final voting.

The real scenario "agent fails with stderr error → ERROR printed,
Round 2 skipped" lives in main()'s orchestration, not in separate functions:
run_agent/ok()/print_results individually covered, but no test exercised
main() branches like "not all agents gave meaningful Round 1".

External effects (CLI execution, input waiting) are substituted via
monkeypatch; agent selection and idea come through substituted input.
"""

import asyncio
import tempfile
from pathlib import Path

from main import main as main_coro
from src.core.models import AgentResult

OK_A = AgentResult(name="A", output="ответ A")
OK_B = AgentResult(name="B", output="ответ B")


def run(coro):
    return asyncio.run(coro)


def feed_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def setup_flow(
    monkeypatch,
    *,
    agents,
    answers,
    round1=None,
    round2=None,
    round3=None,
    votes=None,
):
    """Substitutes discovery, input, and rounds, runs main(), returns the call counter.

    answers — sequence of input() answers:
    1) "Add another agent manually?"; 2) agent selection; 3) idea.
    """
    # run_council_async reads/writes council.json (R3 rotation) relative to
    # cwd — isolate in tmp so it is not left in the repo directory.
    monkeypatch.chdir(tempfile.mkdtemp())

    calls = {"round1": [], "round2": [], "round3": [], "votes": 0}

    async def fake_round1(
        idea,
        council,
        session,
        evidence_dir=None,
        quick_mode=False,
        on_agent_status=None,
        round_timeout=600.0,
    ):
        calls["round1"].append([m.name for m in council])
        result_map = round1 if round1 is not None else {}
        return result_map, {}, "degraded"

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
        calls["round2"].append([m.name for m in council])
        return round2 if round2 is not None else {}

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
        names = [m.name for m in council]
        calls["round3"].append(names)
        # Sequential moves: the callback fires for each.
        if on_move is not None:
            for name in names:
                on_move(name)
        return round3 if round3 is not None else {}

    def fake_votes(r3):
        calls["votes"] += 1
        return (
            votes
            if votes is not None
            else {
                "votes": {},
                "missing": [],
                "average_score": None,
                "min_score": None,
                "max_score": None,
            }
        )

    class FakeSessionWriter:
        """Substitutes SessionWriter — e2e tests for main() should not touch
        disk; writing to sessions/ is tested separately in test_session.py."""

        def __init__(self):
            self.dir = "sessions/run-fake"

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
            # The path is never created on disk (self.dir is a stub) —
            # .exists() returns False, so os.startfile is not called.
            return Path(self.dir) / "verdict.md"

    monkeypatch.setattr("main.discover_agents", lambda: agents)
    monkeypatch.setattr("main.ask_agents_manually", lambda: [])
    monkeypatch.setattr("src.core.orchestrator.run_round1", fake_round1)
    monkeypatch.setattr("src.core.orchestrator.run_round2", fake_round2)
    monkeypatch.setattr("src.core.orchestrator.run_round3", fake_round3)
    monkeypatch.setattr("src.core.orchestrator.aggregate_votes", fake_votes)
    monkeypatch.setattr("main.SessionWriter", FakeSessionWriter)
    feed_input(monkeypatch, answers)

    run(main_coro())
    return calls


class TestMainFlow:
    def test_no_agents_found_shows_error(self, monkeypatch, capsys):
        calls = setup_flow(monkeypatch, agents=[], answers=["n"])

        out = capsys.readouterr().out
        assert "не найдено и не добавлено ни одного агента" in out
        assert calls["round1"] == []
        assert calls["round2"] == []
        assert calls["votes"] == 0

    def test_empty_idea_stops_before_round1(self, monkeypatch, capsys):
        calls = setup_flow(
            monkeypatch,
            agents=[("A", ["a"]), ("B", ["b"])],
            answers=["n", "", ""],
        )

        out = capsys.readouterr().out
        assert "Идея пустая. Нечего оценивать." in out
        assert calls["round1"] == []

    def test_single_agent_runs_only_round1(self, monkeypatch, capsys):
        calls = setup_flow(
            monkeypatch,
            agents=[("A", ["a"])],
            answers=["n", "", "IDEA"],
            round1={"A": OK_A},
        )

        out = capsys.readouterr().out
        assert "ROUND 1" in out
        assert calls["round1"] == [["A"]]
        assert calls["round2"] == []
        assert calls["votes"] == 0

    def test_failed_round1_agent_with_only_two_total_stops_round2(
        self, monkeypatch, capsys
    ):
        # Scenario: dsh failed on stderr; with 2 agents total, losing one
        # leaves <2 alive — R2/R3 and voting do not start.
        broken = AgentResult(
            name="DeepSeek Harness",
            output="",
            error=(
                "error: a task is required, for example: "
                'dsh --profile headless "run the tests"'
            ),
        )
        calls = setup_flow(
            monkeypatch,
            agents=[("Claude Code", ["claude"]), ("DeepSeek Harness", ["dsh"])],
            answers=["n", "", "IDEA"],
            round1={"Claude Code": OK_A, "DeepSeek Harness": broken},
        )

        out = capsys.readouterr().out
        assert "ERROR:" in out
        assert "a task is required" in out
        assert "Выбыли после Round 1 (ошибка/пустой ответ): DeepSeek Harness" in out
        assert (
            "Меньше двух агентов дали содержательный Round 1 — "
            "дальше продолжать не с кем." in out
        )
        assert calls["round2"] == []
        assert calls["round3"] == []
        assert calls["votes"] == 0

    def test_failed_round2_agent_with_only_two_total_continues_round3_with_one(
        self, monkeypatch, capsys
    ):
        # With one alive agent Round 3 continues (with a warning), see
        # main.py alive2 filtering.
        calls = setup_flow(
            monkeypatch,
            agents=[("A", ["a"]), ("B", ["b"])],
            answers=["n", "", "IDEA"],
            round1={"A": OK_A, "B": OK_B},
            round2={
                "A": AgentResult(name="A", output="r2 A"),
                "B": AgentResult(name="B", output="", error="упс в Round 2"),
            },
        )

        out = capsys.readouterr().out
        assert "Выбыли после Round 2 (ошибка/пустой ответ): B" in out
        assert "только 1 агент дал содержательный Round 2" in out
        assert "Round 3 продолжается с одним агентом" in out
        # Round 3 happened with one agent.
        assert len(calls["round3"]) == 1
        assert calls["round3"][0] == ["A"]

    def test_failed_agent_among_three_does_not_stop_investigation(
        self, monkeypatch, capsys
    ):
        # 3 agents, one fails R1 — the run continues with the remaining two
        # through R2, R3, and voting, not stopped by one fallen agent.
        broken = AgentResult(name="C", output="", error="упс в Round 1")
        calls = setup_flow(
            monkeypatch,
            agents=[("A", ["a"]), ("B", ["b"]), ("C", ["c"])],
            answers=["n", "", "IDEA"],
            round1={"A": OK_A, "B": OK_B, "C": broken},
            round2={
                "A": AgentResult(name="A", output="r2 A"),
                "B": AgentResult(name="B", output="r2 B"),
            },
            round3={
                "A": AgentResult(name="A", output="r3 A"),
                "B": AgentResult(name="B", output="r3 B"),
            },
        )

        out = capsys.readouterr().out
        assert "Выбыли после Round 1 (ошибка/пустой ответ): C" in out
        # C drops out; A and B continue without him.
        assert calls["round2"] == [["A", "B"]]
        assert calls["round3"] == [["A", "B"]]
        assert calls["votes"] == 1

    def test_happy_path_runs_all_rounds_and_vote(self, monkeypatch, capsys):
        calls = setup_flow(
            monkeypatch,
            agents=[("A", ["a"]), ("B", ["b"])],
            answers=["n", "", "IDEA"],
            round1={"A": OK_A, "B": OK_B},
            round2={
                "A": AgentResult(name="A", output="r2 A"),
                "B": AgentResult(name="B", output="r2 B"),
            },
            round3={
                "A": AgentResult(name="A", output="r3 A"),
                "B": AgentResult(name="B", output="r3 B"),
            },
            votes={
                "votes": {
                    "A": {"score": 8, "verdict": "хорошо"},
                    "B": {"score": 6, "verdict": ""},
                },
                "missing": [],
                "average_score": 7.0,
                "min_score": 6,
                "max_score": 8,
            },
        )

        out = capsys.readouterr().out
        assert calls["round1"] == [["A", "B"]]
        assert calls["round2"] == [["A", "B"]]
        assert calls["round3"] == [["A", "B"]]
        assert calls["votes"] == 1
        # Spinner in non-TTY mode prints statuses line by line.
        assert "ROUND 1 — думают: A, B" in out
        assert "ROUND 3 — ход: A" in out
        assert "ROUND 3 — ход: B" in out
        assert "FINAL — Council Vote" in out
        assert "Средний балл совета: 7.0/10" in out

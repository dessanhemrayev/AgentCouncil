"""Tests for interactive main.py functions: agent selection, manual agent
addition, printing results and council vote summaries.

Interactivity is simulated via monkeypatch on builtins.input.
"""

from main import (
    ask_agents_manually,
    choose_agents,
)
from src.cli.render import (
    print_results,
    print_vote_summary,
    print_vote_trajectory,
)
from src.core.models import AgentResult

CANDIDATES = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]


def feed_input(monkeypatch, answers):
    """Substitutes input with a sequence of pre-set answers."""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def forbid_input(monkeypatch):
    def boom(prompt=""):
        raise AssertionError("input не должен вызываться")

    monkeypatch.setattr("builtins.input", boom)


class TestChooseAgents:
    def test_no_candidates_returns_empty(self, monkeypatch):
        forbid_input(monkeypatch)

        assert choose_agents([]) == []

    def test_empty_selection_uses_all(self, monkeypatch):
        feed_input(monkeypatch, [""])

        assert choose_agents(CANDIDATES) == CANDIDATES

    def test_numeric_selection(self, monkeypatch):
        feed_input(monkeypatch, ["1,3"])

        assert choose_agents(CANDIDATES) == [("A", ["a"]), ("C", ["c"])]

    def test_selection_with_spaces(self, monkeypatch):
        feed_input(monkeypatch, [" 2 , 3 "])

        assert choose_agents(CANDIDATES) == [("B", ["b"]), ("C", ["c"])]

    def test_invalid_tokens_fallback_to_all(self, monkeypatch):
        feed_input(monkeypatch, ["a,b"])

        assert choose_agents(CANDIDATES) == CANDIDATES

    def test_out_of_range_fallback_to_all(self, monkeypatch):
        feed_input(monkeypatch, ["0,99"])

        assert choose_agents(CANDIDATES) == CANDIDATES

    def test_single_selection(self, monkeypatch):
        feed_input(monkeypatch, ["2"])

        assert choose_agents(CANDIDATES) == [("B", ["b"])]


class TestAskAgentsManually:
    def test_empty_name_finishes_immediately(self, monkeypatch):
        feed_input(monkeypatch, [""])

        assert ask_agents_manually() == []

    def test_missing_command_skips_agent(self, monkeypatch):
        # Empty command -> agent skipped, loop continues polling; "" ends input
        feed_input(monkeypatch, ["X", "", ""])

        assert ask_agents_manually() == []

    def test_adds_available_agent(self, monkeypatch):
        monkeypatch.setattr("main.check_agent_available", lambda command: True)
        feed_input(monkeypatch, ["X", "claude -p x", ""])

        assert ask_agents_manually() == [("X", ["claude", "-p", "x"])]

    def test_unavailable_declined(self, monkeypatch):
        monkeypatch.setattr("main.check_agent_available", lambda command: False)
        feed_input(monkeypatch, ["X", "cmd", "n", ""])

        assert ask_agents_manually() == []

    def test_unavailable_confirmed(self, monkeypatch):
        monkeypatch.setattr("main.check_agent_available", lambda command: False)
        feed_input(monkeypatch, ["X", "cmd", "y", ""])

        assert ask_agents_manually() == [("X", ["cmd"])]


class TestPrintResults:
    def test_error_with_stdout(self, capsys):
        results = {"A": AgentResult(name="A", output="partial", error="failed")}

        print_results(results)

        out = capsys.readouterr().out
        assert "### A" in out
        assert "ERROR:" in out
        assert "failed" in out
        assert "STDOUT:" in out
        assert "partial" in out

    def test_success(self, capsys):
        print_results({"A": AgentResult(name="A", output="good answer")})

        out = capsys.readouterr().out
        assert "good answer" in out
        assert "ERROR:" not in out

    def test_show_json_prints_structured_block(self, capsys):
        text = 'бла\n```json\n{"revised_position": "xyz"}\n```\n'
        print_results({"A": AgentResult(name="A", output=text)}, show_json=True)

        out = capsys.readouterr().out
        assert "[structured]" in out
        assert '"revised_position": "xyz"' in out

    def test_show_json_without_json_is_silent(self, capsys):
        print_results(
            {"A": AgentResult(name="A", output="no json here")}, show_json=True
        )

        out = capsys.readouterr().out
        assert "[structured]" not in out


class TestPrintVoteSummary:
    def test_prints_individual_votes_and_average(self, capsys):
        summary = {
            "votes": {
                "A": {"score": 8, "verdict": "хорошо"},
                "B": {"score": 6, "verdict": ""},
            },
            "missing": [],
            "average_score": 7.0,
            "min_score": 6,
            "max_score": 8,
        }

        print_vote_summary(summary)

        out = capsys.readouterr().out
        assert "Индивидуальные голоса:" in out
        assert "A: 8/10 — хорошо" in out
        # Empty verdict — no dash
        assert "B: 6/10" in out
        assert "B: 6/10 —" not in out
        assert "Средний балл совета: 7.0/10 (min 6, max 8, голосов: 2/2)" in out

    def test_prints_missing_votes(self, capsys):
        summary = {
            "votes": {"A": {"score": 8, "verdict": "ok"}},
            "missing": ["B", "C"],
            "average_score": 8.0,
            "min_score": 8,
            "max_score": 8,
        }

        print_vote_summary(summary)

        out = capsys.readouterr().out
        assert "Не удалось извлечь голос: B, C" in out
        assert "голосов: 1/3" in out

    def test_no_votes_at_all(self, capsys):
        summary = {
            "votes": {},
            "missing": ["A"],
            "average_score": None,
            "min_score": None,
            "max_score": None,
        }

        print_vote_summary(summary)

        out = capsys.readouterr().out
        # No agents give a recognizable vote
        assert "Ни один агент не дал распознаваемый голос" in out
        assert "Средний балл совета" not in out


class TestPrintVoteTrajectory:
    def test_prints_direction_toward_mean(self, capsys):
        trajectory = {
            "trajectory": [
                {"agent": "A", "r2_score": 9, "r3_score": 8, "moved_toward_mean": True}
            ],
            "r3_mean": 8,
        }

        print_vote_trajectory(trajectory)

        out = capsys.readouterr().out
        assert "A: 9 → 8 (к среднему R3)" in out

    def test_prints_direction_away_from_mean(self, capsys):
        trajectory = {
            "trajectory": [
                {"agent": "B", "r2_score": 6, "r3_score": 4, "moved_toward_mean": False}
            ],
            "r3_mean": 8,
        }

        print_vote_trajectory(trajectory)

        out = capsys.readouterr().out
        assert "B: 6 → 4 (от среднего R3)" in out

    def test_incomplete_data_reported(self, capsys):
        trajectory = {
            "trajectory": [
                {
                    "agent": "A",
                    "r2_score": 9,
                    "r3_score": None,
                    "moved_toward_mean": None,
                }
            ]
        }

        print_vote_trajectory(trajectory)

        out = capsys.readouterr().out
        assert "неполные данные" in out

    def test_empty_trajectory_prints_nothing(self, capsys):
        print_vote_trajectory({"trajectory": []})

        assert capsys.readouterr().out == ""

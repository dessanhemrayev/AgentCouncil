"""Tests for the non-interactive CLI layer of main.py: argument parsing,
idea/agent selection, council.json, and key run_noninteractive branches.

Before this file the CLI had no unit tests at all — only a manual smoke
test found a real bug (--list-agents failed on the "idea not provided"
check before it could print the list and exit). This file closes the gap.
"""

import io
import json

import pytest

import main
from src.config import load_config, save_config


class TestParseArgs:
    def test_positional_idea(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "My idea"])
        args = main.parse_args()

        assert args.idea == "My idea"
        assert args.idea_file is None

    def test_idea_file_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "--idea", "@idea.md"])
        args = main.parse_args()

        assert args.idea is None
        assert args.idea_file == "@idea.md"

    def test_evidence_multiple_files(self, monkeypatch):
        # Idea BEFORE --evidence: nargs="+" swallows a positional idea
        # placed after the flag (see the comment on --evidence in parse_args()).
        monkeypatch.setattr(
            "sys.argv", ["council", "idea text", "--evidence", "a.md", "b.md"]
        )
        args = main.parse_args()

        assert args.evidence == ["a.md", "b.md"]
        assert args.idea == "idea text"

    def test_evidence_after_idea_swallows_it_known_argparse_gotcha(self, monkeypatch):
        # Pins the epilog behavior: --evidence before the idea swallows it.
        # If this ever changes, this test should fail and remind you to
        # update the epilog/README.
        monkeypatch.setattr(
            "sys.argv", ["council", "--evidence", "a.md", "b.md", "idea text"]
        )
        args = main.parse_args()

        assert args.evidence == ["a.md", "b.md", "idea text"]
        assert args.idea is None

    def test_quick_and_full_are_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "--quick", "--full"])

        with pytest.raises(SystemExit):
            main.parse_args()

    def test_defaults_when_no_args(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council"])
        args = main.parse_args()

        assert args.idea is None
        assert args.quick is False
        assert args.full is False
        assert args.adversarial is False
        assert args.preset is None
        assert args.gui is False

    def test_unknown_args_are_ignored(self, monkeypatch):
        # parse_known_args — unknown flags (pytest -v etc.) must not fail it.
        monkeypatch.setattr("sys.argv", ["council", "--some-unknown-flag", "idea"])
        args = main.parse_args()

        assert args.idea == "idea"


class TestTaskModeArgs:
    def test_task_flags(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["council", "--task", "т", "--max-reviews", "3", "--work-timeout", "600"],
        )
        args = main.parse_args()
        assert args.task == "т"
        assert args.max_reviews == 3
        assert args.work_timeout == 600

    def test_task_file_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "--task", "@task.md"])
        args = main.parse_args()
        assert args.task == "@task.md"

    def test_task_and_idea_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "idea", "--task", "т"])
        with pytest.raises(SystemExit):
            main.parse_args()

    def test_task_default_none(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["council", "idea"])
        args = main.parse_args()
        assert args.task is None
        assert args.max_reviews is None
        assert args.work_timeout is None


class TestLoadSaveConfig:
    def test_load_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        assert load_config() == {}

    def test_load_invalid_json_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "council.json").write_text("{not valid json", encoding="utf-8")

        assert load_config() == {}

    def test_save_then_load_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        save_config({"mode": "quick", "round3_rotation": 3})

        assert load_config() == {"mode": "quick", "round3_rotation": 3}

    def test_custom_config_path(self, tmp_path):
        path = tmp_path / "custom.json"
        save_config({"a": 1}, str(path))

        assert load_config(str(path)) == {"a": 1}
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_save_does_not_raise_on_unwritable_path(self, tmp_path):
        # best-effort: a path in a nonexistent directory must not crash the run
        bad_path = tmp_path / "no-such-dir" / "council.json"

        save_config({"a": 1}, str(bad_path))  # не должно кинуть исключение


class TestReadIdeaFromSource:
    def _args(self, **overrides):
        defaults = {"idea": None, "idea_file": None, "gui": False}
        defaults.update(overrides)
        return main.argparse.Namespace(**defaults)

    def test_positional_idea_wins(self, monkeypatch):
        monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)

        assert main.read_idea_from_source(self._args(idea="from arg")) == "from arg"

    def test_idea_file_reads_file_with_at_prefix(self, tmp_path, monkeypatch):
        idea_path = tmp_path / "idea.md"
        idea_path.write_text("idea from file", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        result = main.read_idea_from_source(self._args(idea_file="@idea.md"))

        assert result == "idea from file"

    def test_idea_file_reads_file_without_at_prefix(self, tmp_path, monkeypatch):
        idea_path = tmp_path / "idea.md"
        idea_path.write_text("idea from file", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        result = main.read_idea_from_source(self._args(idea_file="idea.md"))

        assert result == "idea from file"

    def test_reads_from_piped_stdin_when_no_idea_given(self, monkeypatch):
        fake_stdin = io.StringIO("piped idea\n")
        fake_stdin.isatty = lambda: False
        monkeypatch.setattr(main.sys, "stdin", fake_stdin)

        assert main.read_idea_from_source(self._args()) == "piped idea"

    def test_returns_empty_when_interactive_tty_and_no_idea(self, monkeypatch):
        monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)

        assert main.read_idea_from_source(self._args()) == ""


class TestRunNoninteractive:
    """Ключевые ветки run_noninteractive — раунды подменяются, чтобы не
    гонять реальных агентов; council.json/sessions изолированы в tmp-cwd."""

    def _args(self, **overrides):
        defaults = dict(
            idea="test idea",
            idea_file=None,
            evidence=None,
            agents=None,
            list_agents=False,
            quick=False,
            full=False,
            preset=None,
            adversarial=False,
            no_open=True,
            output_dir=None,
            gui=False,
            config=None,
            task=None,
            max_reviews=None,
            work_timeout=None,
            round_timeout=None,
        )
        defaults.update(overrides)
        return main.argparse.Namespace(**defaults)

    @pytest.fixture(autouse=True)
    def isolate_cwd_and_rounds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        async def noop_council_async(*args, **kwargs):
            pass

        monkeypatch.setattr(main, "run_council_async", noop_council_async)

    def test_list_agents_exits_zero_without_requiring_idea(self, monkeypatch, capsys):
        # Regression: --list-agents previously failed on "idea is empty" before
        # it could print the list and exit.
        monkeypatch.setattr(
            main, "discover_agents", lambda: [("Claude Code", ["claude"])]
        )

        exit_code = main.run_noninteractive(self._args(idea=None, list_agents=True), {})

        assert exit_code == 0
        assert "Claude Code" in capsys.readouterr().out

    def test_list_agents_with_none_discovered(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "discover_agents", lambda: [])

        exit_code = main.run_noninteractive(self._args(idea=None, list_agents=True), {})

        assert exit_code == 0
        assert "No agents discovered" in capsys.readouterr().out

    def test_no_idea_and_not_gui_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(
            main, "discover_agents", lambda: [("Claude Code", ["claude"])]
        )
        # Otherwise read_idea_from_source() reads the stdin that pytest
        # replaces with an object throwing OSError.
        monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)

        exit_code = main.run_noninteractive(self._args(idea=None), {})

        assert exit_code == 1
        assert "no idea provided" in capsys.readouterr().out

    def test_no_agents_discovered_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "discover_agents", lambda: [])

        exit_code = main.run_noninteractive(self._args(), {})

        assert exit_code == 1
        assert "no agents discovered" in capsys.readouterr().out

    def test_quick_mode_caps_council_at_two_agents(self, monkeypatch, capsys):
        monkeypatch.setattr(
            main,
            "discover_agents",
            lambda: [("A", ["a"]), ("B", ["b"]), ("C", ["c"])],
        )
        # Default selection filters members by availability (a real run only
        # ever sees agents discover_agents() found on PATH); stub the check —
        # availability semantics are pinned in test_roster.py.
        monkeypatch.setattr("src.config.agents.member_available", lambda member: True)

        exit_code = main.run_noninteractive(self._args(quick=True), {})

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Quick mode" in out
        assert "Council participants: A, B" in out

    def test_default_mode_is_full_without_explicit_flags(self, monkeypatch, capsys):
        # Regression (T5): a one-shot idea WITHOUT --quick/--full used to default
        # to QUICK (old main.py:250-254); the README promises --full is the
        # default once an idea is given.
        monkeypatch.setattr(
            main,
            "discover_agents",
            lambda: [("A", ["a"]), ("B", ["b"]), ("C", ["c"])],
        )
        # Same as above: availability is stubbed, semantics live in test_roster.
        monkeypatch.setattr("src.config.agents.member_available", lambda member: True)
        captured = {}

        async def capture_council_async(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        monkeypatch.setattr(main, "run_council_async", capture_council_async)

        exit_code = main.run_noninteractive(self._args(), {})

        out = capsys.readouterr().out
        assert exit_code == 0
        # run_council_async(idea, council, session, quick_mode, ...) — positional.
        assert captured["args"][3] is False
        # No quick cap: all three agents take part.
        assert "Council participants: A, B, C" in out
        assert "Quick mode" not in out

    def test_config_mode_quick_still_runs_quick(self, monkeypatch, capsys):
        # council.json "mode": "quick" remains a valid quick selector.
        monkeypatch.setattr(
            main,
            "discover_agents",
            lambda: [("A", ["a"]), ("B", ["b"]), ("C", ["c"])],
        )
        # Same as above: availability is stubbed, semantics live in test_roster.
        monkeypatch.setattr("src.config.agents.member_available", lambda member: True)

        exit_code = main.run_noninteractive(self._args(), {"mode": "quick"})

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Quick mode" in out
        assert "Council participants: A, B" in out

    def test_evidence_files_copied_into_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            main, "discover_agents", lambda: [("A", ["a"]), ("B", ["b"])]
        )
        monkeypatch.setattr("src.config.agents.member_available", lambda member: True)
        evidence_file = tmp_path / "paper.md"
        evidence_file.write_text("evidence content", encoding="utf-8")

        exit_code = main.run_noninteractive(
            self._args(evidence=[str(evidence_file)]), {}
        )

        assert exit_code == 0
        copied = list((tmp_path / "sessions" / "run-001" / "evidence").glob("*"))
        assert [p.name for p in copied] == ["paper.md"]
        assert (tmp_path / "sessions" / "run-001" / "evidence" / "paper.md").read_text(
            encoding="utf-8"
        ) == "evidence content"

    def test_missing_evidence_file_warns_but_continues(self, monkeypatch, capsys):
        monkeypatch.setattr(
            main, "discover_agents", lambda: [("A", ["a"]), ("B", ["b"])]
        )
        monkeypatch.setattr("src.config.agents.member_available", lambda member: True)

        exit_code = main.run_noninteractive(
            self._args(evidence=["no-such-file.md"]), {}
        )

        assert exit_code == 0
        assert "not found" in capsys.readouterr().out

    def test_agents_flag_restricts_council(self, monkeypatch, capsys):
        monkeypatch.setattr(
            main, "discover_agents", lambda: [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        )

        exit_code = main.run_noninteractive(self._args(agents="a,c"), {})

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Council participants: A, C" in out


def test_list_agents_shows_openai_key_status(monkeypatch, capsys):
    """T9: API members appear with model + live key status (KEY OK / NO KEY)."""
    monkeypatch.setattr(main, "discover_agents", lambda: [])
    config = {
        "members": [
            {
                "id": "gpt-5-api",
                "name": "GPT-5 API",
                "type": "openai",
                "model": "gpt-5",
                "api_key_env": "AGENTCOUNCIL_NO_SUCH_KEY",
            }
        ]
    }
    monkeypatch.setattr("sys.argv", ["council", "--list-agents"])

    exit_code = main.run_noninteractive(main.parse_args(), config)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GPT-5 API [openai] model=gpt-5, NO KEY" in out


def test_list_agents_shows_key_ok_when_env_var_set(monkeypatch, capsys):
    monkeypatch.setattr(main, "discover_agents", lambda: [])
    monkeypatch.setenv("AGENTCOUNCIL_TEST_KEY_OK", "sk-test")
    config = {
        "members": [
            {
                "id": "gpt-5-api",
                "name": "GPT-5 API",
                "type": "openai",
                "model": "gpt-5",
                "api_key_env": "AGENTCOUNCIL_TEST_KEY_OK",
            }
        ]
    }
    monkeypatch.setattr("sys.argv", ["council", "--list-agents"])

    exit_code = main.run_noninteractive(main.parse_args(), config)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GPT-5 API [openai] model=gpt-5, KEY OK" in out


def test_list_agents_shows_cli_members_with_path_state(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(
        main,
        "discover_agents",
        lambda: [("A", [sys.executable, "-c", "pass", "{prompt}"])],
    )
    monkeypatch.setattr("sys.argv", ["council", "--list-agents"])

    exit_code = main.run_noninteractive(main.parse_args(), {})

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "A [cli]" in out
    assert "available" in out

"""Tests for src/config.py — known agents, prompt builders, agent discovery in PATH."""

import sys
from pathlib import Path

from src.config import (
    KNOWN_AGENTS,
    PRESET_EMPHASIS,
    check_agent_available,
    discover_agents,
    get_adversarial_suffix,
    get_evaluation_prompt,
    get_fix_prompt,
    get_preset_emphasis,
    get_review_prompt,
    get_round2_prompt,
    get_round2_prompt_files,
    get_round3_prompt,
    get_round3_prompt_files,
    get_work_prompt,
)


class TestKnownAgents:
    def test_every_command_has_prompt_placeholder(self):
        # Invariant from the comment in config.py: without {prompt} cli_runner
        # sends the prompt via stdin, and the non-interactive CLI hangs until timeout.
        for _name, command in KNOWN_AGENTS:
            assert any("{prompt}" in item for item in command), command

    def test_commands_are_nonempty(self):
        for _name, command in KNOWN_AGENTS:
            assert command, f"Empty command for {_name}"


class TestPromptsHaveNoLeadingWhitespace:
    # Regression: `pi -p <prompt>` silently answers empty (exit 0) when the
    # prompt starts with "\n" — hence .strip() in each builder.
    def test_evaluation_prompt(self):
        prompt = get_evaluation_prompt("idea")

        assert not prompt.startswith(("\n", " ", "\t"))
        assert not prompt.endswith(("\n", " ", "\t"))

    def test_round2_prompt(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1")

        assert not prompt.startswith(("\n", " ", "\t"))
        assert not prompt.endswith(("\n", " ", "\t"))

    def test_round2_prompt_files(self):
        prompt = get_round2_prompt_files(
            Path("idea.md"), {"A": Path("round1/a.md")}, "A"
        )

        assert not prompt.startswith(("\n", " ", "\t"))
        assert not prompt.endswith(("\n", " ", "\t"))

    def test_round3_prompt(self):
        prompt = get_round3_prompt("idea", {"A": "r1"}, {"A": "r2"}, {}, "r1", "r2")

        assert not prompt.startswith(("\n", " ", "\t"))
        assert not prompt.endswith(("\n", " ", "\t"))

    def test_round3_prompt_files(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("round1/a.md")},
            {"A": Path("round2/a.md")},
            {},
            Path("round1/a.md"),
            Path("round2/a.md"),
        )

        assert not prompt.startswith(("\n", " ", "\t"))
        assert not prompt.endswith(("\n", " ", "\t"))


class TestEvaluationPrompt:
    def test_contains_idea(self):
        prompt = get_evaluation_prompt("Моя идея про флотилию дронов")

        assert "Моя идея про флотилию дронов" in prompt

    def test_mentions_scale_and_independence(self):
        prompt = get_evaluation_prompt("idea")

        assert "от 0 до 10" in prompt
        assert (
            "независимая оценка" in prompt.lower()
            or "независимой оценке" in prompt.lower()
        )

    def test_no_needs_full_council_by_default(self):
        prompt = get_evaluation_prompt("idea")

        assert "needs_full_council" not in prompt

    def test_quick_mode_requires_needs_full_council_in_same_json_block(self):
        # Must land in THE SAME json block as claims — otherwise
        # extract_json_block (the first ```json block) won't see it.
        prompt = get_evaluation_prompt("idea", quick_mode=True)

        assert "needs_full_council" in prompt
        json_block = prompt.split("```json", 1)[1].split("```", 1)[0]
        assert "needs_full_council" in json_block
        assert '"claims"' in json_block


class TestPresetEmphasis:
    def test_known_presets_return_nonempty_text(self):
        for preset in PRESET_EMPHASIS:
            assert get_preset_emphasis(preset)

    def test_unknown_or_none_preset_returns_empty(self):
        assert get_preset_emphasis(None) == ""
        assert get_preset_emphasis("no-such-preset") == ""


class TestAdversarialSuffix:
    def test_true_returns_nonempty_text(self):
        assert get_adversarial_suffix(True)

    def test_false_returns_empty(self):
        assert get_adversarial_suffix(False) == ""


class TestRound2Prompt:
    # Default context is INLINE; file-based variant — TestRound2PromptFiles.
    def test_contains_idea_all_agents_and_own_round1(self):
        round1_all = {"Bob": "БОБ Р1", "Carol": "КАРОЛ Р1"}

        prompt = get_round2_prompt("ИДЕЯ", round1_all, "МОЙ Р1")

        assert "ИДЕЯ" in prompt
        assert "### Bob" in prompt and "БОБ Р1" in prompt
        assert "### Carol" in prompt and "КАРОЛ Р1" in prompt
        assert "YOUR ROUND 1:" in prompt
        assert "МОЙ Р1" in prompt

    def test_preset_emphasis_included_when_given(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1", preset="hypothesis")

        assert "PRESET: hypothesis pre-validation" in prompt

    def test_no_preset_means_no_emphasis(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1")

        assert "PRESET:" not in prompt

    def test_adversarial_suffix_included_when_flag_set(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1", adversarial=True)

        assert "ADVERSARIAL MODE" in prompt
        assert "fatal flaw" in prompt

    def test_no_adversarial_suffix_by_default(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1")

        assert "ADVERSARIAL MODE" not in prompt

    def test_own_entry_present_in_all_agents_block(self):
        round1_all = {"Я": "МОЙ Р1", "Bob": "БОБ Р1"}

        prompt = get_round2_prompt("idea", round1_all, "МОЙ Р1")

        assert "### Я" in prompt
        assert "МОЙ Р1" in prompt

    def test_agents_in_given_order(self):
        round1_all = {"Bob": "b", "Carol": "c"}

        prompt = get_round2_prompt("idea", round1_all, "mine")

        assert prompt.index("Bob") < prompt.index("Carol")

    def test_empty_round1_block(self):
        prompt = get_round2_prompt("idea", {}, "mine")

        assert "mine" in prompt

    def test_requires_json_block(self):
        prompt = get_round2_prompt("idea", {"Bob": "b"}, "mine")

        assert "```json" in prompt
        assert "revised_position" in prompt

    def test_round2_prompt_includes_executor_field(self):
        prompt = get_round2_prompt("idea", {"A": "r1"}, "r1")
        assert '"executor": "agent-name" | "none"' in prompt
        assert "executor field" in prompt  # инструкция ниже json-блока

    def test_round2_prompt_files_mode_includes_executor_field(self):
        from pathlib import Path

        prompt = get_round2_prompt_files(
            Path("idea.md"), {"A": Path("round1/a.md")}, "A"
        )
        assert '"executor": "agent-name" | "none"' in prompt


class TestRound2PromptFiles:
    # Fallback mode: context is delivered VIA FILE PATHS, not text —
    # used only when the inline version doesn't fit within the command-line
    # limit (see council.INLINE_CONTEXT_THRESHOLD).
    def test_contains_idea_path_and_agent_paths(self):
        round1_paths = {"Bob": Path("round1/bob.md"), "Carol": Path("round1/carol.md")}

        prompt = get_round2_prompt_files(Path("idea.md"), round1_paths, "Bob")

        assert "idea.md" in prompt
        assert "Bob: round1" in prompt and "bob.md" in prompt
        assert "Carol: round1" in prompt and "carol.md" in prompt
        assert "YOUR ROUND 1:" in prompt

    def test_own_entry_present_in_all_agents_block(self):
        round1_paths = {"Я": Path("round1/ya.md"), "Bob": Path("round1/bob.md")}

        prompt = get_round2_prompt_files(Path("idea.md"), round1_paths, "Я")

        assert prompt.count("ya.md") == 2  # own path: list + YOUR ROUND 1 line

    def test_agents_in_given_order(self):
        round1_paths = {"Bob": Path("round1/bob.md"), "Carol": Path("round1/carol.md")}

        prompt = get_round2_prompt_files(Path("idea.md"), round1_paths, "Bob")

        assert prompt.index("Bob") < prompt.index("Carol")

    def test_empty_round1_paths_raises_for_unknown_self(self):
        try:
            get_round2_prompt_files(Path("idea.md"), {}, "mine")
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError for missing your_name")

    def test_requires_json_block(self):
        prompt = get_round2_prompt_files(
            Path("idea.md"), {"Bob": Path("round1/bob.md")}, "Bob"
        )

        assert "```json" in prompt
        assert "revised_position" in prompt

    def test_instructs_to_actually_read_not_just_plan(self):
        # Regression: some CLIs announce a plan to read files and stop.
        prompt = get_round2_prompt_files(
            Path("idea.md"), {"Bob": Path("round1/bob.md")}, "Bob"
        )

        assert "actually call your file" in prompt
        assert "not just describe a plan" in prompt


class TestRound3Prompt:
    def test_contains_all_contexts(self):
        prompt = get_round3_prompt(
            "ИДЕЯ",
            {"A": "Р1_A", "B": "Р1_B"},
            {"A": "Р2_A", "B": "Р2_B"},
            {},
            "МОЙ Р1",
            "МОЙ Р2",
        )

        assert "ИДЕЯ" in prompt
        assert "### A" in prompt and "Р1_A" in prompt and "Р2_A" in prompt
        assert "### B" in prompt and "Р1_B" in prompt and "Р2_B" in prompt
        assert "МОЙ Р1" in prompt
        assert "МОЙ Р2" in prompt

    def test_empty_history_marks_first_mover(self):
        prompt = get_round3_prompt("idea", {"A": "a1"}, {"A": "a2"}, {}, "a1", "a2")

        assert "no previous moves yet" in prompt

    def test_history_included_when_present(self):
        prompt = get_round3_prompt(
            "idea",
            {"A": "a1", "B": "b1"},
            {"A": "a2", "B": "b2"},
            {"A": "ХОД_A"},
            "a1",
            "a2",
        )

        assert "### A" in prompt
        assert "ХОД_A" in prompt
        assert "no previous moves yet" not in prompt

    def test_mentions_resolution_statuses_and_final_vote(self):
        prompt = get_round3_prompt("idea", {"A": "a1"}, {"A": "a2"}, {}, "a1", "a2")

        for status in ("RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED"):
            assert status in prompt
        assert "FINAL VOTE" in prompt


class TestRound3PromptFiles:
    def test_contains_all_path_contexts(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("round1/a.md"), "B": Path("round1/b.md")},
            {"A": Path("round2/a.md"), "B": Path("round2/b.md")},
            {},
            Path("round1/a.md"),
            Path("round2/a.md"),
        )

        assert "idea.md" in prompt
        assert "round1" in prompt and "a.md" in prompt and "b.md" in prompt
        assert "round2" in prompt

    def test_empty_history_marks_first_mover(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("r1/a.md")},
            {"A": Path("r2/a.md")},
            {},
            Path("r1/a.md"),
            Path("r2/a.md"),
        )

        assert "no previous moves yet" in prompt

    def test_history_included_when_present(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("r1/a.md"), "B": Path("r1/b.md")},
            {"A": Path("r2/a.md"), "B": Path("r2/b.md")},
            {"A": Path("round3/a.md")},
            Path("r1/a.md"),
            Path("r2/a.md"),
        )

        assert "round3" in prompt and "a.md" in prompt
        assert "no previous moves yet" not in prompt

    def test_mentions_resolution_statuses_and_final_vote(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("r1/a.md")},
            {"A": Path("r2/a.md")},
            {},
            Path("r1/a.md"),
            Path("r2/a.md"),
        )

        for status in ("RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED"):
            assert status in prompt
        assert "FINAL VOTE" in prompt

    def test_instructs_to_actually_read_not_just_plan(self):
        prompt = get_round3_prompt_files(
            Path("idea.md"),
            {"A": Path("r1/a.md")},
            {"A": Path("r2/a.md")},
            {},
            Path("r1/a.md"),
            Path("r2/a.md"),
        )

        assert "actually call your file" in prompt
        assert "not just describe a plan" in prompt


class TestCheckAgentAvailable:
    def test_empty_command(self):
        assert check_agent_available([]) is False

    def test_available_real_binary(self):
        assert check_agent_available([sys.executable]) is True

    def test_unavailable_command(self):
        assert check_agent_available(["no-such-command-xyz-123"]) is False

    def test_uses_shutil_which_on_first_token(self, monkeypatch):
        seen = []

        def fake_which(cmd):
            seen.append(cmd)
            return "C:/bin/" + cmd

        monkeypatch.setattr("src.config.agents.shutil.which", fake_which)

        assert check_agent_available(["claude", "-p"]) is True
        assert seen == ["claude"]


class TestDiscoverAgents:
    def test_filters_unavailable(self, monkeypatch):
        available = {"claude", "gemini"}

        monkeypatch.setattr(
            "src.config.agents.shutil.which",
            lambda cmd: f"C:/bin/{cmd}" if cmd in available else None,
        )

        found = discover_agents()

        assert found == [
            ("Claude Code", ["claude", "-p", "{prompt}"]),
            ("Gemini CLI", ["gemini", "-p", "{prompt}"]),
        ]

    def test_returns_empty_when_nothing_found(self, monkeypatch):
        monkeypatch.setattr("src.config.agents.shutil.which", lambda cmd: None)

        assert discover_agents() == []


class TestTaskModePrompts:
    """Task-mode prompts: EN by convention, JSON schemas in single curly
    braces (not an f-string where substitution is not needed)."""

    def test_work_prompt_anti_lazy(self):
        p = get_work_prompt(
            "Напиши список из 5 пунктов", {"claude": "A1 text", "codex": "A2 text"}
        )
        assert "EXECUTE THE TASK" in p
        assert "FULL TEXT" in p
        assert "NOT a plan" in p
        assert "Напиши список из 5 пунктов" in p
        assert "### claude" in p and "### codex" in p

    def test_work_prompt_truncates_long_discussion(self):
        p = get_work_prompt("t", {"claude": "x" * 5000})
        assert (
            len(p) < 4500
        )  # summary is limited to WORK_DISCUSSION_MAX per agent (+ fixed overhead SKILLS_INSTRUCTION)

    def test_fix_prompt_has_draft_and_flaws(self):
        p = get_fix_prompt(
            "t", "OLD DRAFT BODY", ["нет раздела 2", "факты не сходятся"]
        )
        assert "EXECUTE THE TASK" in p  # same marker as work (e2e/mock)
        assert "FIX ITERATION" in p
        assert "OLD DRAFT BODY" in p
        assert "нет раздела 2" in p and "факты не сходятся" in p
        assert "FULL UPDATED TEXT" in p

    def test_fix_prompt_file_fallback(self):
        from pathlib import Path

        p = get_fix_prompt("t", "OLD", ["flaw"], draft_path=Path("/tmp/work.md"))
        assert "/tmp/work.md" in p
        assert "OLD" not in p  # draft is not inlined when a path is given

    def test_review_prompt_draft_and_schema(self):
        p = get_review_prompt("t", "DRAFT BODY", "own_r1", "own_r2")
        assert "REVIEW THE COUNCIL" in p
        assert "DRAFT BODY" in p
        assert '"verdict": "APPROVED" | "REQUIRED_FIXES"' in p
        assert "critical_flaws" in p
        assert "does not contradict" in p  # consistency prompt for memo
        assert "own_r1" in p and "own_r2" in p

    def test_review_prompt_change_log(self):
        p = get_review_prompt(
            "t", "D", "r1", "r2", change_log=["- claude: нужен раздел 2"]
        )
        assert "CHANGES REQUESTED IN THE PREVIOUS REVIEW ITERATION" in p
        assert "MUST verify that these fixes were actually implemented" in p
        assert "нужен раздел 2" in p

    def test_review_prompt_long_draft_file_fallback(self):
        from pathlib import Path

        p = get_review_prompt(
            "t", "x" * 25_000, "r1", "r2", draft_path=Path("/tmp/work.md")
        )
        assert "x" * 25_000 not in p
        assert "/tmp/work.md" in p
        assert "read this file FIRST" in p

"""Tests for src/core/council.py — JSON parsing and council round logic.

Rounds are tested with a substituted run_member: what prompts
each agent receives and how result dictionaries are formed is verified.
"""

import asyncio
import json

import pytest

from src.core.aggregation import (
    aggregate_claims,
    aggregate_votes,
    compute_vote_trajectory,
    extract_json_block,
    ok,
)
from src.core.council import run_round1, run_round2, run_round3
from src.core.models import AgentResult
from src.core.session import SessionWriter


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_run_agent(monkeypatch):
    """Substitutes run_member inside council: records calls, returns a stub."""
    calls = []

    async def _fake(member, prompt, timeout=300.0, session_dir=None, on_status=None):
        calls.append(
            {
                "name": member.name,
                "command": list(member.command or []),
                "prompt": prompt,
            }
        )
        return AgentResult(name=member.name, output=f"out of {member.name}")

    monkeypatch.setattr("src.core.council.run_member", _fake)
    return calls


@pytest.fixture
def session(tmp_path):
    """Context between rounds is now passed via files (not text inside
    the argument), so rounds need a real SessionWriter — on tmp_path,
    so tests don't write to the real sessions/.
    """
    return SessionWriter(base=tmp_path / "sessions")


class TestExtractJsonBlock:
    def test_fenced_json_block(self):
        text = 'Ответ:\n```json\n{"revised_position": "xyz"}\n```\nКонец.'

        assert extract_json_block(text) == {"revised_position": "xyz"}

    def test_fenced_json_with_nested_dict(self):
        text = '```json\n{"major_errors": ["a"], "meta": {"deep": 1}}\n```'

        assert extract_json_block(text) == {"major_errors": ["a"], "meta": {"deep": 1}}

    def test_bare_dict_in_prose(self):
        text = 'Мой ответ: {"strongest_counterargument": "нет"} — вот такой.'

        assert extract_json_block(text) == {"strongest_counterargument": "нет"}

    def test_bare_multiline_dict(self):
        text = 'текст\n{\n  "a": 1,\n  "b": 2\n}\nконец'

        assert extract_json_block(text) == {"a": 1, "b": 2}

    def test_no_braces_returns_none(self):
        assert extract_json_block("Просто текст без JSON") is None

    def test_empty_string_returns_none(self):
        assert extract_json_block("") is None

    def test_invalid_json_in_fenced_block_returns_none(self):
        # The only ```json block is invalid — fall back to the "bare" regex
        # which finds the same {bad}, also invalid -> None.
        assert extract_json_block("```json\n{bad}\n```") is None

    def test_two_fenced_blocks_prefers_last(self):
        # Last block is the real answer (prompts require it at the very end);
        # the first may be a format example.
        text = (
            'Формат такой:\n```json\n{"verdict": "EXAMPLE"}\n```\n'
            'Мой ответ:\n```json\n{"verdict": "APPROVED", "critical_flaws": []}\n```'
        )
        assert extract_json_block(text) == {"verdict": "APPROVED", "critical_flaws": []}

    def test_two_fenced_blocks_last_invalid_falls_back_to_earlier(self):
        # The last block is truncated/invalid (e.g., cut by the CLI
        # response length limit) — we should not lose the entire vote if an
        # earlier block parses normally.
        text = '```json\n{"verdict": "APPROVED", "critical_flaws": []}\n```\n```json\n{truncated\n```'
        assert extract_json_block(text) == {"verdict": "APPROVED", "critical_flaws": []}

    def test_invalid_bare_json_returns_none(self):
        assert extract_json_block("текст {не json} конец") is None

    def test_two_separate_dicts_returns_none(self):
        # Greedy regex takes from the first { to the last } — the concatenation is invalid.
        assert extract_json_block('{"a": 1} между {"b": 2}') is None


class TestOk:
    @pytest.mark.parametrize(
        ("output", "error", "expected"),
        [
            ("text", None, True),
            ("", None, False),
            ("text", "err", False),
            ("", "err", False),
        ],
    )
    def test_ok(self, output, error, expected):
        assert ok(AgentResult(name="A", output=output, error=error)) is expected


class TestRound1:
    def test_runs_all_agents_and_keys_by_name(self, fake_run_agent, session):
        agents = [("A", ["a-cmd"]), ("B", ["b-cmd"])]

        results, clm_inventory, degradation_status = run(
            run_round1("IDEA-1", agents, session)
        )

        assert set(results) == {"A", "B"}
        assert results["A"].output == "out of A"
        assert results["B"].output == "out of B"
        assert results["A"].error is None
        assert set(clm_inventory) == {"A", "B"}
        assert degradation_status == "degraded"  # fake outputs carry no CLM JSON block

    def test_passes_commands_and_idea_prompt(self, fake_run_agent, session):
        agents = [("A", ["a-cmd", "-p"]), ("B", ["b-cmd"])]

        run(run_round1("IDEA-1", agents, session))

        assert [c["name"] for c in fake_run_agent] == ["A", "B"]
        assert fake_run_agent[0]["command"] == ["a-cmd", "-p"]
        assert fake_run_agent[1]["command"] == ["b-cmd"]
        for call in fake_run_agent:
            assert "IDEA-1" in call["prompt"]

    def test_writes_round1_files_to_disk(self, fake_run_agent, session):
        # Rounds 2/3 read context from files — Round 1 must write them.
        agents = [("A", ["a-cmd"])]

        run(run_round1("IDEA-1", agents, session))

        assert (
            session.agent_path("round1", "A").read_text(encoding="utf-8") == "out of A"
        )

    def test_quick_mode_adds_needs_full_council_to_prompt(
        self, fake_run_agent, session
    ):
        # The mandatory final question appears in the R1 prompt only
        # in quick mode — not in normal mode.
        agents = [("A", ["a-cmd"])]

        run(run_round1("IDEA-1", agents, session, quick_mode=True))

        assert "needs_full_council" in fake_run_agent[0]["prompt"]

    def test_full_mode_prompt_has_no_needs_full_council(self, fake_run_agent, session):
        agents = [("A", ["a-cmd"])]

        run(run_round1("IDEA-1", agents, session, quick_mode=False))

        assert "needs_full_council" not in fake_run_agent[0]["prompt"]


class TestRound2:
    # Context is INLINE by default; files on overflow — TestRound2FilesFallback.
    def test_every_agent_sees_all_round1_outputs(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"])]
        round1 = {
            "A": AgentResult(name="A", output="R1_A"),
            "B": AgentResult(name="B", output="R1_B"),
        }
        session.write_round("round1", round1)

        results = run(run_round2("IDEA-2", council, round1, session))

        assert set(results) == {"A", "B"}
        assert results["B"].output == "out of B"

        for call in fake_run_agent:
            prompt = call["prompt"]
            assert "IDEA-2" in prompt
            # Everyone sees everyone (including themselves), but not other Round 2s.
            assert "### A" in prompt and "R1_A" in prompt
            assert "### B" in prompt and "R1_B" in prompt

    def test_excludes_other_round2_outputs(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"])]
        round1 = {
            "A": AgentResult(name="A", output="R1_A"),
            "B": AgentResult(name="B", output="R1_B"),
        }
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        # R2 answers form in parallel — none of them is in the prompts yet.
        for call in fake_run_agent:
            assert "out of A" not in call["prompt"]
            assert "out of B" not in call["prompt"]

    def test_failed_agent_is_skipped_and_not_leaked_into_context(
        self, fake_run_agent, session
    ):
        # C failed R1: no R2 turn, and his empty response must not leak
        # into the remaining agents' context.
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        round1 = {
            "A": AgentResult(name="A", output="R1_A"),
            "B": AgentResult(name="B", output="R1_B"),
            "C": AgentResult(name="C", output="", error="упс в Round 1"),
        }
        session.write_round("round1", round1)

        results = run(run_round2("idea", council, round1, session))

        assert set(results) == {"A", "B"}  # C was not queried
        assert [c["name"] for c in fake_run_agent] == ["A", "B"]
        for call in fake_run_agent:
            assert "### C" not in call["prompt"]

    def test_three_agents_each_see_full_council(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        round1 = {
            name: AgentResult(name=name, output=f"R1_{name}") for name, _ in council
        }
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        for call in fake_run_agent:
            for name in ("A", "B", "C"):
                assert f"### {name}" in call["prompt"]
                assert f"R1_{name}" in call["prompt"]

    def test_writes_round2_files_to_disk(self, fake_run_agent, session):
        # Files are always written (persistence/audit), inline or file mode.
        council = [("A", ["a"]), ("B", ["b"])]
        round1 = {
            name: AgentResult(name=name, output=f"R1_{name}") for name, _ in council
        }
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        assert (
            session.agent_path("round2", "A").read_text(encoding="utf-8") == "out of A"
        )
        assert (
            session.agent_path("round2", "B").read_text(encoding="utf-8") == "out of B"
        )

    def test_preset_emphasis_included_in_prompt(self, fake_run_agent, session):
        # The preset changes only the emphasis in the header, not the JSON schema.
        council = [("A", ["a"])]
        round1 = {"A": AgentResult(name="A", output="R1_A")}
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session, preset="lit-review"))

        assert "PRESET: literature conflict resolution" in fake_run_agent[0]["prompt"]

    def test_no_preset_means_no_emphasis_line(self, fake_run_agent, session):
        council = [("A", ["a"])]
        round1 = {"A": AgentResult(name="A", output="R1_A")}
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        assert "PRESET:" not in fake_run_agent[0]["prompt"]

    def test_adversarial_flag_included_in_prompt(self, fake_run_agent, session):
        # Experimental flag, not part of the default protocol.
        council = [("A", ["a"])]
        round1 = {"A": AgentResult(name="A", output="R1_A")}
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session, adversarial=True))

        assert "ADVERSARIAL MODE" in fake_run_agent[0]["prompt"]

    def test_no_adversarial_flag_by_default(self, fake_run_agent, session):
        council = [("A", ["a"])]
        round1 = {"A": AgentResult(name="A", output="R1_A")}
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        assert "ADVERSARIAL MODE" not in fake_run_agent[0]["prompt"]


class TestRound2FilesFallback:
    # Inline prompt over INLINE_CONTEXT_THRESHOLD -> file-based prompts.
    def test_switches_to_files_when_inline_too_large(self, fake_run_agent, session):
        from src.core.council import INLINE_CONTEXT_THRESHOLD

        council = [("A", ["a"]), ("B", ["b"])]
        huge = "x" * (INLINE_CONTEXT_THRESHOLD + 1)
        round1 = {
            "A": AgentResult(name="A", output=huge),
            "B": AgentResult(name="B", output="R1_B"),
        }
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        path_a = str(session.agent_path("round1", "A"))
        for call in fake_run_agent:
            # File mode: path is present, the huge text is not
            assert path_a in call["prompt"]
            assert huge not in call["prompt"]

    def test_stays_inline_when_under_threshold(self, fake_run_agent, session):
        council = [("A", ["a"])]
        round1 = {"A": AgentResult(name="A", output="короткий R1")}
        session.write_round("round1", round1)

        run(run_round2("idea", council, round1, session))

        assert "короткий R1" in fake_run_agent[0]["prompt"]
        assert str(session.agent_path("round1", "A")) not in fake_run_agent[0]["prompt"]


class TestRound3:
    def test_agents_move_sequentially_in_council_order(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B", "C")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        results = run(
            run_round3("IDEA-3", council, dict(rounds), dict(rounds), session)
        )

        # Moves are strictly sequential, not parallel
        assert [c["name"] for c in fake_run_agent] == ["A", "B", "C"]
        assert set(results) == {"A", "B", "C"}
        assert results["C"].output == "out of C"

    def test_start_index_rotates_order(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B", "C")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        run(
            run_round3(
                "IDEA-3", council, dict(rounds), dict(rounds), session, start_index=1
            )
        )

        assert [c["name"] for c in fake_run_agent] == ["B", "C", "A"]

    def test_first_mover_has_empty_history(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        run(run_round3("idea", council, dict(rounds), dict(rounds), session))

        prompt_a = fake_run_agent[0]["prompt"]
        assert "no previous moves yet" in prompt_a
        assert "out of A" not in prompt_a

    def test_later_movers_see_previous_moves(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B", "C")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        run(run_round3("idea", council, dict(rounds), dict(rounds), session))

        prompt_b = fake_run_agent[1]["prompt"]
        prompt_c = fake_run_agent[2]["prompt"]

        # B sees A's move but not his own; C sees both previous
        assert "### A" in prompt_b and "out of A" in prompt_b
        assert "out of B" not in prompt_b
        assert "out of A" in prompt_c and "out of B" in prompt_c

    def test_writes_round3_files_to_disk(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        run(run_round3("idea", council, dict(rounds), dict(rounds), session))

        assert (
            session.agent_path("round3", "A").read_text(encoding="utf-8") == "out of A"
        )
        assert (
            session.agent_path("round3", "B").read_text(encoding="utf-8") == "out of B"
        )

    def test_agent_missing_round1_or_round2_skips_turn(self, fake_run_agent, session):
        # C is in the R3 list but has no valid R2 — skips the turn, the
        # rest proceed, his empty response stays out of the public history.
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        round1 = {
            name: AgentResult(name=name, output=f"R1_{name}")
            for name in ("A", "B", "C")
        }
        round2 = {
            "A": AgentResult(name="A", output="R2_A"),
            "B": AgentResult(name="B", output="R2_B"),
            "C": AgentResult(name="C", output="", error="упс в Round 2"),
        }
        session.write_round("round1", round1)
        session.write_round("round2", round2)

        results = run(run_round3("idea", council, round1, round2, session))

        assert [c["name"] for c in fake_run_agent] == ["A", "B"]  # C пропустил ход
        assert set(results) == {"A", "B"}

        # A valid Round 1 means C legitimately stays in the general block —
        # but his (failed) Round 2 should not leak in.
        prompt_b = fake_run_agent[1]["prompt"]
        assert "R1_C" in prompt_b
        assert "R2_C" not in prompt_b

    def test_failed_move_is_marked_in_history(self, monkeypatch, session):
        calls = []

        async def _fake(
            member, prompt, timeout=300.0, session_dir=None, on_status=None
        ):
            calls.append({"name": member.name, "prompt": prompt})
            if member.name == "A":
                return AgentResult(name=member.name, error="упс")
            return AgentResult(name=member.name, output=f"out of {member.name}")

        monkeypatch.setattr("src.core.council.run_member", _fake)

        council = [("A", ["a"]), ("B", ["b"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)

        run(run_round3("idea", council, dict(rounds), dict(rounds), session))

        prompt_b = calls[1]["prompt"]
        # A failed move is marked in history and on disk, not dropped.
        assert "[нет ответа: упс]" in prompt_b
        assert "out of A" not in prompt_b

        history_text = session.agent_path("round3", "A").read_text(encoding="utf-8")
        assert "ERROR" in history_text
        assert "упс" in history_text


class TestRound3FilesFallback:
    def test_switches_to_files_when_inline_too_large(self, fake_run_agent, session):
        from src.core.council import INLINE_CONTEXT_THRESHOLD

        council = [("A", ["a"]), ("B", ["b"])]
        huge = "x" * (INLINE_CONTEXT_THRESHOLD + 1)
        round1 = {
            "A": AgentResult(name="A", output=huge),
            "B": AgentResult(name="B", output="R1_B"),
        }
        round2 = {
            "A": AgentResult(name="A", output="R2_A"),
            "B": AgentResult(name="B", output="R2_B"),
        }
        session.write_round("round1", round1)
        session.write_round("round2", round2)

        run(run_round3("idea", council, round1, round2, session))

        path_a1 = str(session.agent_path("round1", "A"))
        prompt_a = fake_run_agent[0]["prompt"]
        assert path_a1 in prompt_a
        assert huge not in prompt_a


class TestAggregateVotes:
    @staticmethod
    def _vote_output(score, verdict="вердикт"):
        payload = json.dumps(
            {"final_vote": {"score": score, "verdict": verdict}},
            ensure_ascii=False,
        )
        return f"Текст ответа...\n```json\n{payload}\n```\nКонец."

    def test_all_votes_valid(self):
        round3 = {
            "A": AgentResult(name="A", output=self._vote_output(8, "хорошо")),
            "B": AgentResult(name="B", output=self._vote_output(6, "так себе")),
        }

        summary = aggregate_votes(round3)

        assert summary["votes"] == {
            "A": {"score": 8, "verdict": "хорошо"},
            "B": {"score": 6, "verdict": "так себе"},
        }
        assert summary["missing"] == []
        assert summary["average_score"] == 7.0
        assert summary["min_score"] == 6
        assert summary["max_score"] == 8

    def test_float_and_boundary_scores(self):
        round3 = {
            "A": AgentResult(name="A", output=self._vote_output(7.5)),
            "B": AgentResult(name="B", output=self._vote_output(0)),
            "C": AgentResult(name="C", output=self._vote_output(10)),
        }

        summary = aggregate_votes(round3)

        assert summary["missing"] == []
        assert summary["average_score"] == (7.5 + 0 + 10) / 3
        assert summary["min_score"] == 0
        assert summary["max_score"] == 10

    @pytest.mark.parametrize(
        "output",
        [
            "нет JSON вообще",
            "```json\n{bad}\n```",
            '```json\n{"final_vote": 8}\n```',
            '```json\n{"final_vote": {"score": "8"}}\n```',
            '```json\n{"final_vote": {"score": 11}}\n```',
            '```json\n{"final_vote": {"score": -1}}\n```',
            '```json\n{"final_vote": {"score": true}}\n```',
            '```json\n{"final_vote": {"verdict": "без score"}}\n```',
            "",
        ],
    )
    def test_unparseable_votes_go_to_missing(self, output):
        round3 = {"A": AgentResult(name="A", output=output)}

        summary = aggregate_votes(round3)

        assert summary["votes"] == {}
        assert summary["missing"] == ["A"]
        assert summary["average_score"] is None
        assert summary["min_score"] is None
        assert summary["max_score"] is None

    def test_result_with_error_is_missing_even_with_json(self):
        payload = json.dumps({"final_vote": {"score": 9, "verdict": "v"}})
        result = AgentResult(name="A", output=f"```json\n{payload}\n```", error="упс")

        summary = aggregate_votes({"A": result})

        assert summary["missing"] == ["A"]
        assert summary["votes"] == {}

    def test_empty_council(self):
        summary = aggregate_votes({})

        assert summary == {
            "votes": {},
            "missing": [],
            "average_score": None,
            "min_score": None,
            "max_score": None,
        }

    def test_mixed_valid_and_invalid(self):
        round3 = {
            "A": AgentResult(name="A", output=self._vote_output(8)),
            "B": AgentResult(name="B", output="no json"),
        }

        summary = aggregate_votes(round3)

        assert set(summary["votes"]) == {"A"}
        assert summary["missing"] == ["B"]
        assert summary["average_score"] == 8.0


class TestAggregateClaims:
    """aggregate_claims — CLM x round map, pure aggregation without LLM."""

    @staticmethod
    def _json_result(name, payload):
        return AgentResult(
            name=name,
            output=f"текст\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```\nконец.",
        )

    def test_empty_inputs_produce_empty_map(self):
        result = aggregate_claims({}, {}, {})

        assert result == {
            "claims": [],
            "untracked_r2": [],
            "untracked_r3": [],
            "unsupported_assumptions": [],
        }

    def test_pools_r1_variants_by_bare_id(self):
        # CLM ids are per-agent: the same id from two agents are two claims
        # — both go into variants, not merged.
        clm_inventory = {
            "A": [
                {
                    "id": "CLM-1",
                    "statement": "X ускоряет Y",
                    "status": "FACT",
                    "evidence_ref": "e.md",
                }
            ],
            "B": [
                {
                    "id": "CLM-1",
                    "statement": "X вообще не влияет на Y",
                    "status": "ASSUMPTION",
                }
            ],
        }

        result = aggregate_claims(clm_inventory, {}, {})

        assert len(result["claims"]) == 1
        variants = result["claims"][0]["variants"]
        assert {v["agent"] for v in variants} == {"A", "B"}

    def test_r2_claim_status_attaches_to_matching_claim(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round2 = {
            "B": self._json_result(
                "B",
                {
                    "claim_status": [
                        {"id": "CLM-1", "status": "SUPPORTED", "reason": "ok"}
                    ]
                },
            ),
        }

        result = aggregate_claims(clm_inventory, round2, {})

        claim = result["claims"][0]
        assert len(claim["r2"]) == 1
        assert claim["r2"][0]["agent"] == "B"
        assert claim["r2"][0]["raw_status"] == "SUPPORTED"
        assert result["untracked_r2"] == []

    def test_r2_unknown_id_goes_to_untracked(self):
        round2 = {
            "B": self._json_result(
                "B",
                {
                    "claim_status": [
                        {"id": "CLM-404", "status": "SUPPORTED", "reason": "ok"}
                    ]
                },
            ),
        }

        result = aggregate_claims({}, round2, {})

        assert result["claims"] == []
        assert len(result["untracked_r2"]) == 1
        assert result["untracked_r2"][0]["claim_id"] == "CLM-404"

    def test_unsupported_assumptions_linked_by_claim_id(self):
        round2 = {
            "B": self._json_result(
                "B",
                {
                    "unsupported_assumptions": [
                        {
                            "agent": "B",
                            "assumption": "a",
                            "reason": "r",
                            "claim_id": "CLM-1",
                        }
                    ]
                },
            ),
        }

        result = aggregate_claims({}, round2, {})

        assert result["unsupported_assumptions"] == [
            {"agent": "B", "assumption": "a", "reason": "r", "claim_id": "CLM-1"}
        ]

    def test_r3_responses_attach_and_untracked_id_is_flagged(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round3 = {
            "A": self._json_result(
                "A",
                {
                    "responses_to_claims": [
                        {"claim_id": "CLM-1", "status": "resolved"},
                        {"claim_id": "CLM-404", "status": "unresolved"},
                    ]
                },
            ),
        }

        result = aggregate_claims(clm_inventory, {}, round3)

        assert len(result["claims"][0]["r3"]) == 1
        assert result["claims"][0]["r3"][0]["raw_status"] == "resolved"
        assert len(result["untracked_r3"]) == 1
        assert result["untracked_r3"][0]["claim_id"] == "CLM-404"

    def test_final_status_all_agents_resolved(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round3 = {
            "A": self._json_result(
                "A",
                {"responses_to_claims": [{"claim_id": "CLM-1", "status": "resolved"}]},
            ),
            "B": self._json_result(
                "B",
                {"responses_to_claims": [{"claim_id": "CLM-1", "status": "resolved"}]},
            ),
        }

        result = aggregate_claims(clm_inventory, {}, round3)

        assert result["claims"][0]["final_status"] == "RESOLVED"

    def test_final_status_any_falsified_wins(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round3 = {
            "A": self._json_result(
                "A",
                {"responses_to_claims": [{"claim_id": "CLM-1", "status": "resolved"}]},
            ),
            "B": self._json_result(
                "B",
                {"responses_to_claims": [{"claim_id": "CLM-1", "status": "falsified"}]},
            ),
        }

        result = aggregate_claims(clm_inventory, {}, round3)

        assert result["claims"][0]["final_status"] == "FALSIFIED"

    def test_final_status_mixed_resolved_and_unresolved_is_partial(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round3 = {
            "A": self._json_result(
                "A",
                {"responses_to_claims": [{"claim_id": "CLM-1", "status": "resolved"}]},
            ),
            "B": self._json_result(
                "B",
                {
                    "responses_to_claims": [
                        {"claim_id": "CLM-1", "status": "unresolved"}
                    ]
                },
            ),
        }

        result = aggregate_claims(clm_inventory, {}, round3)

        assert result["claims"][0]["final_status"] == "PARTIALLY_RESOLVED"

    def test_final_status_falls_back_to_r2_when_no_r3(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round2 = {
            "A": self._json_result(
                "A", {"claim_status": [{"id": "CLM-1", "status": "CONTRADICTED"}]}
            )
        }

        result = aggregate_claims(clm_inventory, round2, {})

        assert result["claims"][0]["final_status"] == "FALSIFIED"

    def test_final_status_unknown_for_unevidenced_fact_with_no_rounds(self):
        clm_inventory = {
            "A": [
                {
                    "id": "CLM-1",
                    "statement": "s",
                    "status": "FACT",
                    "evidence_ref": None,
                }
            ]
        }

        result = aggregate_claims(clm_inventory, {}, {})

        assert result["claims"][0]["final_status"] == "UNKNOWN"

    def test_final_status_unresolved_for_bare_claim_with_no_rounds(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}

        result = aggregate_claims(clm_inventory, {}, {})

        assert result["claims"][0]["final_status"] == "UNRESOLVED"

    def test_failed_quote_verification_overrides_to_possible_hallucination(
        self, tmp_path
    ):
        # Quote verification overrides the agent's self-declared status.
        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / "e.md").write_text(
            "Настоящий текст источника.", encoding="utf-8"
        )

        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round2 = {
            "A": self._json_result(
                "A",
                {
                    "claim_status": [
                        {
                            "id": "CLM-1",
                            "status": "SUPPORTED",
                            "evidence_quote": "цитата, которой нет в файле",
                            "evidence_ref": "e.md",
                        }
                    ]
                },
            ),
        }

        result = aggregate_claims(clm_inventory, round2, {}, evidence_dir)

        assert result["claims"][0]["r2"][0]["quote_verified"] is False
        assert result["claims"][0]["final_status"] == "POSSIBLE_HALLUCINATION"

    def test_successful_quote_verification_does_not_override(self, tmp_path):
        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / "e.md").write_text(
            "Настоящая цитата из источника.", encoding="utf-8"
        )

        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round2 = {
            "A": self._json_result(
                "A",
                {
                    "claim_status": [
                        {
                            "id": "CLM-1",
                            "status": "SUPPORTED",
                            "evidence_quote": "Настоящая цитата из источника.",
                            "evidence_ref": "e.md",
                        }
                    ]
                },
            ),
        }

        result = aggregate_claims(clm_inventory, round2, {}, evidence_dir)

        assert result["claims"][0]["r2"][0]["quote_verified"] is True
        assert result["claims"][0]["final_status"] == "RESOLVED"

    def test_ignores_agents_with_no_parseable_json(self):
        clm_inventory = {"A": [{"id": "CLM-1", "statement": "s", "status": "CLAIMED"}]}
        round2 = {"A": AgentResult(name="A", output="просто текст без JSON")}
        round3 = {"A": AgentResult(name="A", output="", error="упс")}

        result = aggregate_claims(clm_inventory, round2, round3)

        assert result["claims"][0]["r2"] == []
        assert result["claims"][0]["r3"] == []
        assert result["claims"][0]["final_status"] == "UNRESOLVED"


class TestRound3MoveCallback:
    def test_on_move_fires_before_each_move_in_order(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)
        moves = []

        run(
            run_round3(
                "idea",
                council,
                dict(rounds),
                dict(rounds),
                session,
                on_move=moves.append,
            )
        )

        assert moves == ["A", "B"]
        # The callback fires before the corresponding agent call
        assert [c["name"] for c in fake_run_agent] == moves

    def test_on_move_respects_start_index(self, fake_run_agent, session):
        council = [("A", ["a"]), ("B", ["b"]), ("C", ["c"])]
        rounds = {
            name: AgentResult(name=name, output=f"R_{name}") for name in ("A", "B", "C")
        }
        session.write_round("round1", rounds)
        session.write_round("round2", rounds)
        moves = []

        run(
            run_round3(
                "idea",
                council,
                dict(rounds),
                dict(rounds),
                session,
                start_index=2,
                on_move=moves.append,
            )
        )

        assert moves == ["C", "A", "B"]


class TestComputeVoteTrajectory:
    """The score trajectory R2 -> R3, both points are already in JSON (final_vote in
    R2), no new LLM call needed."""

    @staticmethod
    def _vote_output(score):
        payload = json.dumps({"final_vote": {"score": score, "verdict": "v"}})
        return f"текст\n```json\n{payload}\n```\nконец."

    def test_delta_computed(self):
        round2 = {"A": AgentResult(name="A", output=self._vote_output(8))}
        round3 = {"A": AgentResult(name="A", output=self._vote_output(6))}

        result = compute_vote_trajectory(round2, round3)

        # Sole agent: r3_mean = his own score, so "toward the mean" is
        # trivially true — only meaningful with >= 2 agents.
        assert result["trajectory"] == [
            {
                "agent": "A",
                "r2_score": 8,
                "r3_score": 6,
                "delta": -2,
                "moved_toward_mean": True,
            }
        ]
        assert result["r3_mean"] == 6

    def test_moved_toward_mean_true_when_closer_to_r3_mean(self):
        # R3 mean is 7. A: 9 -> 8 (closer to 7) — moved toward the mean.
        round2 = {
            "A": AgentResult(name="A", output=self._vote_output(9)),
            "B": AgentResult(name="B", output=self._vote_output(6)),
        }
        round3 = {
            "A": AgentResult(name="A", output=self._vote_output(8)),
            "B": AgentResult(name="B", output=self._vote_output(6)),
        }

        result = compute_vote_trajectory(round2, round3)

        by_agent = {row["agent"]: row for row in result["trajectory"]}
        assert result["r3_mean"] == 7
        assert by_agent["A"]["moved_toward_mean"] is True

    def test_moved_away_from_mean_is_false(self):
        # R3 mean is 7. B: 6 -> 4 (farther from 7) — moved away from the mean.
        round2 = {
            "A": AgentResult(name="A", output=self._vote_output(9)),
            "B": AgentResult(name="B", output=self._vote_output(6)),
        }
        round3 = {
            "A": AgentResult(name="A", output=self._vote_output(10)),
            "B": AgentResult(name="B", output=self._vote_output(4)),
        }

        result = compute_vote_trajectory(round2, round3)

        by_agent = {row["agent"]: row for row in result["trajectory"]}
        assert by_agent["B"]["moved_toward_mean"] is False

    def test_agent_missing_from_one_round_has_none_delta(self):
        round2 = {"A": AgentResult(name="A", output=self._vote_output(8))}
        round3 = {"B": AgentResult(name="B", output=self._vote_output(6))}

        result = compute_vote_trajectory(round2, round3)

        by_agent = {row["agent"]: row for row in result["trajectory"]}
        assert by_agent["A"]["r3_score"] is None
        assert by_agent["A"]["delta"] is None
        assert by_agent["B"]["r2_score"] is None

    def test_empty_inputs(self):
        result = compute_vote_trajectory({}, {})

        assert result == {"trajectory": [], "r3_mean": None}

    def test_unparseable_vote_excluded_like_aggregate_votes(self):
        round2 = {"A": AgentResult(name="A", output="нет JSON")}
        round3 = {"A": AgentResult(name="A", output=self._vote_output(6))}

        result = compute_vote_trajectory(round2, round3)

        row = result["trajectory"][0]
        assert row["r2_score"] is None
        assert row["r3_score"] == 6

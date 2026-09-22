"""Tests for task mode: select_executor, work gates,
extract_review/count_approvals, run_work/run_review/run_fix (mock run_member),
run_task_pipeline (mock run_member). E2E with stub agents — separate file
tests/test_task_mode_e2e.py.

Mock pattern: monkeypatch "src.core.council.run_member" — real CLIs
don't run, 0 tokens.
"""

import asyncio
import json
from pathlib import Path

import pytest

from src.core.council import select_executor
from src.core.models import AgentResult
from src.core.session import SessionWriter

COUNCIL = [
    ("claude", ["claude", "-p", "{prompt}"]),
    ("codex", ["codex", "exec", "{prompt}"]),
    ("gemini", ["gemini", "-p", "{prompt}"]),
]
ROSTER = [n for n, _ in COUNCIL]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def session(tmp_path):
    return SessionWriter(tmp_path / "sessions")


def r2_json(executor):
    return AgentResult(
        name="claude", output='```json\n{"executor": %s}\n```' % json.dumps(executor)
    )


class TestSelectExecutor:
    def test_majority_wins(self):
        round2 = {
            "claude": r2_json("codex"),
            "codex": r2_json("codex"),
            "gemini": r2_json("claude"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] == "codex"
        assert out["reason"] == "majority"
        assert out["votes"] == {"claude": "codex", "codex": "codex", "gemini": "claude"}
        assert out["missing"] == []

    def test_tie_first_in_call_order(self):
        round2 = {
            "claude": r2_json("claude"),
            "codex": r2_json("codex"),
            "gemini": r2_json("none"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        # 1:1 — winner is the first in council CALL ORDER (deterministic).
        assert out["winner"] == "claude"
        assert out["reason"] == "tie_first_in_call_order"

    def test_tie_with_missing_still_deterministic(self):
        round2 = {
            "claude": r2_json("claude"),
            "codex": AgentResult(name="codex", output="без json"),
            "gemini": r2_json("gemini"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] == "claude"
        assert out["missing"] == ["codex"]

    def test_single_vote_rest_none(self):
        # 1:0:0 — sole candidate, deterministic reason (not "majority").
        round2 = {
            "claude": r2_json("codex"),
            "codex": r2_json("none"),
            "gemini": r2_json("none"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] == "codex"
        assert out["reason"] == "tie_first_in_call_order"

    def test_all_none_aborts(self):
        round2 = {n: r2_json("none") for n in ROSTER}
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] is None
        assert out["reason"] == "all_none"

    def test_vote_case_insensitive_but_returns_canonical_name(self):
        # Bug (run-006): the agent writes its roster name in a different case
        # ("hermes" vs "Hermes") — the vote must not be lost, and the winner
        # must be the canonical name (downstream exec_cmd lookup).
        round2 = {
            "claude": r2_json("CODEX"),
            "codex": r2_json("codex"),
            "gemini": r2_json("none"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] == "codex"
        assert out["reason"] == "majority"
        assert out["votes"] == {"claude": "codex", "codex": "codex", "gemini": "none"}
        assert out["missing"] == []

    def test_unknown_names_missing_none_counts(self):
        round2 = {
            "claude": r2_json("Claude Code"),  # не из roster
            "codex": r2_json("gpt-5"),
            "gemini": r2_json("none"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] is None
        assert out["reason"] == "all_none"
        assert out["missing"] == ["claude", "codex"]

    def test_non_string_executor_missing(self):
        round2 = {
            "claude": AgentResult(name="claude", output='{"executor": 5}'),
            "codex": AgentResult(name="codex", output='{"executor": null}'),
            "gemini": r2_json("gemini"),
        }
        out = select_executor(round2, COUNCIL, ROSTER)
        assert out["winner"] == "gemini"
        assert out["missing"] == ["claude", "codex"]


class TestWorkGates:
    def test_gate_short_output_aborted(self):
        from src.core.council import check_work_gate

        assert check_work_gate("кратко") == "aborted"

    def test_gate_ok(self):
        from src.core.council import check_work_gate

        assert check_work_gate("x" * 250) == "ok"

    def test_intent_flag_not_blocking(self):
        from src.core.council import check_work_gate

        text = (
            "План работы:"
            + " " * 220
            + "следующим шагом я напишу полный текст и продолжу в следующем ответе"
        )
        assert check_work_gate(text) == "ok"  # does NOT block
        # but marks it

    def test_real_draft_not_intent(self):
        from src.core.council import looks_like_intent_only

        assert (
            looks_like_intent_only("# Заголовок\n\nАбзац один.\n\nАбзац два.") is False
        )
        assert looks_like_intent_only("") is False

    def test_run_work_prompt_and_timeout(self, monkeypatch):
        from src.core.council import run_work

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append({"name": member.name, "prompt": prompt, "timeout": timeout})
            return AgentResult(name=member.name, output="W" * 300)

        monkeypatch.setattr("src.core.council.run_member", fake)
        result = run(
            run_work(
                ("claude", ["claude", "-p", "{prompt}"]),
                "задача",
                {"claude": "ctx"},
                timeout=99.0,
            )
        )
        assert result.output == "W" * 300
        assert calls[0]["timeout"] == 99.0
        assert "EXECUTE THE TASK" in calls[0]["prompt"]
        assert "задача" in calls[0]["prompt"]

    def test_run_fix_prompt_carries_draft_and_flaws(self, monkeypatch):
        from src.core.council import run_fix

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append(prompt)
            return AgentResult(name=member.name, output="W" * 300)

        monkeypatch.setattr("src.core.council.run_member", fake)
        run(
            run_fix(
                ("claude", ["claude", "-p", "{prompt}"]),
                "задача",
                "OLD DRAFT",
                ["нет раздела 2"],
                timeout=99.0,
            )
        )
        assert "EXECUTE THE TASK" in calls[0]
        assert "OLD DRAFT" in calls[0]
        assert "нет раздела 2" in calls[0]


class TestParseReview:
    def test_valid_approved(self):
        from src.core.council import extract_review

        out = '```json\n{"verdict": "APPROVED", "critical_flaws": [], "suggested_edits": ["typos"]}\n```'
        r = extract_review(AgentResult(name="gemini", output=out))
        assert r["verdict"] == "APPROVED"
        assert r["suggested_edits"] == ["typos"]
        assert r["critical_flaws"] == []

    def test_required_fixes(self):
        from src.core.council import extract_review

        out = '{"verdict": "REQUIRED_FIXES", "critical_flaws": ["нет раздела 2"]}'
        r = extract_review(AgentResult(name="codex", output=out))
        assert r["verdict"] == "REQUIRED_FIXES"
        assert r["critical_flaws"] == ["нет раздела 2"]

    def test_bad_json_none(self):
        from src.core.council import extract_review

        assert (
            extract_review(AgentResult(name="a", output="согласен, отличная работа"))
            is None
        )

    def test_bad_verdict_none(self):
        from src.core.council import extract_review

        assert (
            extract_review(
                AgentResult(
                    name="a", output='{"verdict": "MAYBE", "critical_flaws": []}'
                )
            )
            is None
        )

    def test_verdict_case_insensitive_but_returns_canonical(self):
        # Same bug class as select_executor: the schema does not guarantee
        # the model's case ("approved") — the vote must not be lost.
        from src.core.council import extract_review

        out = '{"verdict": "approved", "critical_flaws": []}'
        r = extract_review(AgentResult(name="a", output=out))
        assert r["verdict"] == "APPROVED"

        out2 = '{"verdict": "Required_Fixes", "critical_flaws": ["x"]}'
        r2 = extract_review(AgentResult(name="a", output=out2))
        assert r2["verdict"] == "REQUIRED_FIXES"

    def test_error_result_none(self):
        from src.core.council import extract_review

        assert extract_review(AgentResult(name="a", output="", error="timeout")) is None

    def test_flaws_normalized_to_strings(self):
        from src.core.council import extract_review

        r = extract_review(
            AgentResult(
                name="a",
                output='{"verdict": "REQUIRED_FIXES", "critical_flaws": ["x", 1], "suggested_edits": "y"}',
            )
        )
        assert r["critical_flaws"] == ["x", "1"]
        assert r["suggested_edits"] == ["y"]

    def test_missing_lists_become_empty(self):
        from src.core.council import extract_review

        r = extract_review(AgentResult(name="a", output='{"verdict": "APPROVED"}'))
        assert (
            r is not None and r["critical_flaws"] == [] and r["suggested_edits"] == []
        )


class TestCountApprovals:
    def test_majority_over_half_of_reviewers(self):
        from src.core.council import count_approvals

        # 2 APPROVED out of 2 reviewers (council of 3) -> 2 > (3-1)/2 = 1 -> True
        assert (
            count_approvals(
                {
                    "a": {"verdict": "APPROVED", "critical_flaws": []},
                    "b": {"verdict": "APPROVED", "critical_flaws": []},
                },
                council_size=3,
            )
            is True
        )

    def test_half_is_not_majority(self):
        from src.core.council import count_approvals

        # 1 APPROVED out of 2 reviewers -> 1 > 1? no -> False
        assert (
            count_approvals(
                {
                    "a": {"verdict": "APPROVED", "critical_flaws": []},
                    "b": {"verdict": "REQUIRED_FIXES", "critical_flaws": ["x"]},
                },
                council_size=3,
            )
            is False
        )

    def test_unparsed_is_not_vote(self):
        from src.core.council import count_approvals

        # 1 APPROVED, 1 unparsed (None — not a vote) -> 1 > 2? no
        assert (
            count_approvals(
                {"a": {"verdict": "APPROVED", "critical_flaws": []}, "b": None},
                council_size=3,
            )
            is False
        )

    def test_no_reviewers_false(self):
        from src.core.council import count_approvals

        assert count_approvals({}, council_size=1) is False


class TestRunReview:
    R1 = {n: AgentResult(name=n, output=n.upper() + "_R1") for n in ROSTER}
    R2 = {n: AgentResult(name=n, output=n.upper() + "_R2") for n in ROSTER}

    def test_review_excludes_executor_own_context(self, monkeypatch):
        from src.core.council import run_review

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append({"name": member.name, "prompt": prompt})
            return AgentResult(
                name=member.name,
                output='{"verdict": "APPROVED", "critical_flaws": [], "suggested_edits": []}',
            )

        monkeypatch.setattr("src.core.council.run_member", fake)
        out = run(
            run_review("Задача", "DRAFT-BODY", "codex", self.R1, self.R2, COUNCIL)
        )
        assert sorted(out.keys()) == ["claude", "gemini"]  # исполнитель исключён
        assert all(v is not None and v["verdict"] == "APPROVED" for v in out.values())
        assert len(calls) == 2
        # each sees the other's draft, but their OWN R1/R2 (not others' — bias)
        for c in calls:
            assert "DRAFT-BODY" in c["prompt"]
            assert c["name"].upper() + "_R1" in c["prompt"]
            assert c["name"].upper() + "_R2" in c["prompt"]
            assert "REVIEW THE COUNCIL" in c["prompt"]

    def test_review_change_log_forwarded(self, monkeypatch):
        from src.core.council import run_review

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append(prompt)
            return AgentResult(
                name=member.name, output='{"verdict": "APPROVED", "critical_flaws": []}'
            )

        monkeypatch.setattr("src.core.council.run_member", fake)
        run(
            run_review(
                "t",
                "D",
                "codex",
                self.R1,
                self.R2,
                COUNCIL,
                change_log=["- claude: нужен раздел 2"],
            )
        )
        assert all("CHANGES REQUESTED" in p and "нужен раздел 2" in p for p in calls)

    def test_review_long_draft_sends_file_path(self, monkeypatch):
        from src.core.council import run_review

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append(prompt)
            return AgentResult(
                name=member.name, output='{"verdict": "APPROVED", "critical_flaws": []}'
            )

        monkeypatch.setattr("src.core.council.run_member", fake)
        big = "x" * 25_000
        # draft > REVIEW_DRAFT_MAX_CHARS -> path in the prompt, not text.
        run(
            run_review(
                "t",
                big,
                "codex",
                self.R1,
                self.R2,
                COUNCIL,
                draft_path=Path("DRAFTFILE"),
            )
        )
        assert all("x" * 25_000 not in p for p in calls)
        assert all("DRAFTFILE" in p for p in calls)

    def test_review_dropped_agents_skipped(self, monkeypatch):
        from src.core.council import run_review

        calls = []

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append(member.name)
            return AgentResult(
                name=member.name, output='{"verdict": "APPROVED", "critical_flaws": []}'
            )

        monkeypatch.setattr("src.core.council.run_member", fake)
        r1 = dict(self.R1)
        r1["gemini"] = AgentResult(name="gemini", output="", error="crash")
        r2 = dict(self.R2)
        r2["gemini"] = AgentResult(name="gemini", output="", error="crash")
        out = run(run_review("t", "D", "codex", r1, r2, COUNCIL))
        assert sorted(out.keys()) == ["claude"]  # упавший в R1+R2 — не рецензент
        assert calls == ["claude"]

    def test_review_unparsed_kept_as_none(self, monkeypatch):
        from src.core.council import run_review

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            return AgentResult(name=member.name, output="отличная работа, одобрено")

        monkeypatch.setattr("src.core.council.run_member", fake)
        out = run(run_review("t", "D", "codex", self.R1, self.R2, COUNCIL))
        assert out == {"claude": None, "gemini": None}  # голос не засчитан


class TestRunTaskPipeline:
    """run_member мок: work/fix различаются от review по маркеру промпта
    ("EXECUTE THE TASK" vs "REVIEW THE COUNCIL"); review-поведение по имени
    агента и наличию "FIX ITERATION" в промпте."""

    APPROVED_JSON = (
        '{"verdict": "APPROVED", "critical_flaws": [], "suggested_edits": []}'
    )
    FIXES_JSON = '{"verdict": "REQUIRED_FIXES", "critical_flaws": ["нет раздела 2"], "suggested_edits": []}'

    def _rounds(self, executor="claude"):
        round1 = {n: AgentResult(name=n, output=n.upper() + "_R1") for n in ROSTER}
        round2 = {n: r2_json(executor) for n in ROSTER}
        return round1, round2

    def _fake(self, monkeypatch, review_behavior, work_behavior="worker"):
        """review_behavior: name -> str (JSON-ответ); work_behavior:
        'worker' | 'lazy' | 'crash' | 'fix_ok' (2-й EXECUTE-вызов — длиннее)."""
        calls = []
        work_calls = [0]

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            calls.append({"name": member.name, "prompt": prompt, "timeout": timeout})
            if "EXECUTE THE TASK" in prompt:
                work_calls[0] += 1
                if work_behavior == "crash":
                    return AgentResult(
                        name=member.name,
                        output="",
                        error="Таймаут: агент не ответил за 5 сек.",
                    )
                if work_behavior == "lazy":
                    return AgentResult(
                        name=member.name,
                        output="хорошо, я сейчас напишу полный текст работы",
                    )
                if work_behavior == "fix_ok" and work_calls[0] == 2:
                    return AgentResult(
                        name=member.name,
                        output="Обновлённый деливер. " + "Текст с правками. " * 40,
                    )
                return AgentResult(name=member.name, output="W" * 300)
            return AgentResult(
                name=member.name,
                output=review_behavior.get(member.name, self.APPROVED_JSON),
            )

        monkeypatch.setattr("src.core.council.run_member", fake)
        return calls

    def test_full_pipeline_approved_first_iteration(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        self._fake(
            monkeypatch, {"codex": self.APPROVED_JSON, "gemini": self.APPROVED_JSON}
        )
        out = run(
            run_task_pipeline(
                "задача",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "APPROVED"
        assert out["executor"] == "claude"
        assert out["iterations"] == 1
        for f in (
            "work.md",
            "work-final.md",
            "executor.json",
            "review-1.json",
            "task_verdict.json",
            "task-verdict.md",
        ):
            assert (session.dir / f).exists(), f
        assert not (session.dir / "review-2.json").exists()  # цикл остановился

    def test_approved_when_one_council_member_dead_both_rounds(
        self, monkeypatch, session
    ):
        # Bug: an agent dead in R1+R2 is not a reviewer, but count_approvals
        # used to divide by len(council) — the "dead" vote made APPROVED
        # unreachable no matter how many review->fix cycles ran.
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        round1["gemini"] = AgentResult(name="gemini", output="", error="crash")
        round2["gemini"] = AgentResult(name="gemini", output="", error="crash")
        self._fake(monkeypatch, {"codex": self.APPROVED_JSON})
        out = run(
            run_task_pipeline(
                "задача",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "APPROVED"
        assert out["reviews"][1]["missing"] == []

    def test_cap_partial_with_fix_iteration(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        calls = self._fake(
            monkeypatch, {"codex": self.FIXES_JSON, "gemini": self.FIXES_JSON}
        )
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "PARTIAL"
        assert out["iterations"] == 2
        # 1 work + 1 fix, 2 iterations over 2 reviewers.
        work_calls = [c for c in calls if "EXECUTE THE TASK" in c["prompt"]]
        review_calls = [c for c in calls if "REVIEW THE COUNCIL" in c["prompt"]]
        assert len(work_calls) == 2
        assert len(review_calls) == 4
        # Fix call: carries draft v1 + critical_flaws of iteration 1.
        assert "нет раздела 2" in work_calls[1]["prompt"]
        assert "W" * 100 in work_calls[1]["prompt"]  # previous draft
        assert "FIX ITERATION" in work_calls[1]["prompt"]
        # Change log in review iteration 2:
        review2 = [c for c in review_calls if "CHANGES REQUESTED" in c["prompt"]]
        assert len(review2) == 2
        assert all("нет раздела 2" in c["prompt"] for c in review2)
        verdict = (session.dir / "task-verdict.md").read_text(encoding="utf-8")
        assert "PARTIAL" in verdict
        assert "Council-Reviewed: PARTIAL" in verdict

    def test_cap_zero_review_only(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        calls = self._fake(
            monkeypatch, {"codex": self.FIXES_JSON, "gemini": self.FIXES_JSON}
        )
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=0,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "PARTIAL"
        assert out["iterations"] == 1
        assert (
            len([c for c in calls if "EXECUTE THE TASK" in c["prompt"]]) == 1
        )  # правки нет
        assert len([c for c in calls if "REVIEW THE COUNCIL" in c["prompt"]]) == 2

    def test_aborted_all_none(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("none")
        calls = self._fake(monkeypatch, {})
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "ABORT"
        assert out["executor"] is None
        assert not any(
            "EXECUTE THE TASK" in c["prompt"] for c in calls
        )  # work не вызывался
        assert (session.dir / "executor.json").exists()
        assert (session.dir / "task-verdict.md").exists()
        assert not (session.dir / "work-final.md").exists()

    def test_aborted_executor_crash_before_artifact(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        self._fake(monkeypatch, {}, work_behavior="crash")
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "ABORT"
        assert "crash" in (out["aborted_reason"] or "") or out["aborted_reason"]
        assert not (session.dir / "work-final.md").exists()

    def test_degraded_short_work_no_retry(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        calls = self._fake(monkeypatch, {}, work_behavior="lazy")
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "DEGRADED"
        assert out["iterations"] == 0
        # No auto-retry: exactly one EXECUTE call.
        assert len([c for c in calls if "EXECUTE THE TASK" in c["prompt"]]) == 1
        assert not (session.dir / "work-final.md").exists()

    def test_degraded_all_reviews_unparsed(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        self._fake(
            monkeypatch, {"codex": "отличная работа, одобрено", "gemini": "согласен"}
        )
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "DEGRADED"
        assert out["iterations"] == 1
        assert (
            session.dir / "work-final.md"
        ).exists()  # артефакт опубликован (с пометкой)

    def test_fix_empty_output_degraded(self, monkeypatch, session):
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")

        async def fake(member, prompt, timeout=600.0, session_dir=None, on_status=None):
            if "EXECUTE THE TASK" in prompt:
                if "FIX ITERATION" in prompt:
                    return AgentResult(
                        name=member.name, output="коротко"
                    )  # пустой/короткий fix
                return AgentResult(name=member.name, output="W" * 300)
            return AgentResult(name=member.name, output=self.FIXES_JSON)

        monkeypatch.setattr("src.core.council.run_member", fake)
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "DEGRADED"

    def test_required_fixes_without_flaws_stops_as_partial(self, monkeypatch, session):
        # REQUIRED_FIXES with EMPTY critical_flaws -> PARTIAL, not an
        # infinite loop (no mechanical basis for a fix).
        from src.core.council import run_task_pipeline

        round1, round2 = self._rounds("claude")
        empty_flaws = '{"verdict": "REQUIRED_FIXES", "critical_flaws": [], "suggested_edits": ["попроще фраза"]}'
        self._fake(monkeypatch, {"codex": empty_flaws, "gemini": empty_flaws})
        out = run(
            run_task_pipeline(
                "т",
                COUNCIL,
                ROSTER,
                round1,
                round2,
                session,
                max_review_iterations=2,
                work_timeout=99.0,
            )
        )
        assert out["status"] == "PARTIAL"
        assert out["iterations"] == 1  # правка не запускалась

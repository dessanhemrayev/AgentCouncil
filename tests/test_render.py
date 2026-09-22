"""Tests for src/cli/render.py — rendering CLM map and verdict.md.

A single render point for CLI and GUI: we verify that structures from
aggregate_claims/aggregate_votes turn into readable text, not what
exactly "looks nice".
"""

from src.cli.render import (
    render_claims_map_lines,
    render_task_verdict_markdown,
    render_verdict_markdown,
)

EMPTY_VOTES = {
    "votes": {},
    "missing": [],
    "average_score": None,
    "min_score": None,
    "max_score": None,
}


class TestRenderClaimsMapLines:
    def test_empty_map_says_unavailable(self):
        lines = render_claims_map_lines({"claims": []})

        assert any("недоступна" in line for line in lines)

    def test_renders_id_status_and_statement(self):
        claims_map = {
            "claims": [
                {
                    "id": "CLM-1",
                    "final_status": "RESOLVED",
                    "statement": "утверждение",
                    "variants": [],
                    "r2": [],
                    "r3": [],
                }
            ],
            "untracked_r2": [],
            "untracked_r3": [],
        }

        lines = render_claims_map_lines(claims_map)

        assert "CLM-1 [RESOLVED]: утверждение" in lines

    def test_conflict_category_shown_when_present(self):
        claims_map = {
            "claims": [
                {
                    "id": "CLM-1",
                    "final_status": "FALSIFIED",
                    "statement": "s",
                    "conflict_category": "method",
                    "variants": [],
                    "r2": [],
                    "r3": [],
                }
            ],
        }

        lines = render_claims_map_lines(claims_map)

        assert "[конфликт: method]" in lines[0]

    def test_multiple_variants_flagged(self):
        claims_map = {
            "claims": [
                {
                    "id": "CLM-1",
                    "final_status": "UNRESOLVED",
                    "statement": "s",
                    "variants": [{"agent": "A"}, {"agent": "B"}],
                    "r2": [],
                    "r3": [],
                }
            ],
        }

        lines = render_claims_map_lines(claims_map)

        assert any("также использовали: B" in line for line in lines)

    def test_failed_quote_verification_flagged_in_output(self):
        claims_map = {
            "claims": [
                {
                    "id": "CLM-1",
                    "final_status": "POSSIBLE_HALLUCINATION",
                    "statement": "s",
                    "variants": [],
                    "r2": [
                        {
                            "agent": "A",
                            "raw_status": "SUPPORTED",
                            "quote_verified": False,
                        }
                    ],
                    "r3": [],
                }
            ],
        }

        lines = render_claims_map_lines(claims_map)

        assert any("ЦИТАТА НЕ НАЙДЕНА" in line for line in lines)

    def test_untracked_count_reported(self):
        claims_map = {
            "claims": [],
            "untracked_r2": [{"claim_id": "CLM-9"}],
            "untracked_r3": [{"claim_id": "CLM-8"}],
        }

        lines = render_claims_map_lines(claims_map)

        assert any("Untracked" in line for line in lines)


class TestRenderVerdictMarkdown:
    def test_includes_idea_and_headers(self):
        markdown = render_verdict_markdown(
            "Моя идея", {"claims": []}, EMPTY_VOTES, "full"
        )

        assert "# AgentCouncil — вердикт" in markdown
        assert "Моя идея" in markdown
        assert "## Карта выживания утверждений" in markdown
        assert "## Вторичная метрика: голосование совета" in markdown

    def test_degraded_banner_shown(self):
        markdown = render_verdict_markdown(
            "idea", {"claims": []}, EMPTY_VOTES, "degraded"
        )

        assert "DEGRADED" in markdown

    def test_partial_banner_shown(self):
        markdown = render_verdict_markdown(
            "idea", {"claims": []}, EMPTY_VOTES, "partial"
        )

        assert "PARTIAL" in markdown

    def test_claims_table_rendered(self):
        claims_map = {
            "claims": [
                {
                    "id": "CLM-1",
                    "final_status": "RESOLVED",
                    "statement": "s",
                    "variants": [],
                    "r2": [],
                    "r3": [],
                }
            ]
        }

        markdown = render_verdict_markdown("idea", claims_map, EMPTY_VOTES, "full")

        assert "| CLM-1 | RESOLVED |" in markdown

    def test_unsupported_assumptions_section(self):
        claims_map = {
            "claims": [],
            "unsupported_assumptions": [
                {"agent": "A", "assumption": "a", "reason": "r", "claim_id": "CLM-1"}
            ],
        }

        markdown = render_verdict_markdown("idea", claims_map, EMPTY_VOTES, "full")

        assert "## Неподтверждённые допущения" in markdown
        assert "CLM-1" in markdown

    def test_citation_mismatches_section(self):
        markdown = render_verdict_markdown(
            "idea", {"claims": []}, EMPTY_VOTES, "full", ["mismatch text"]
        )

        assert "## Несовпадения цитат" in markdown
        assert "mismatch text" in markdown

    def test_vote_average_and_secondary_caveat_present(self):
        votes = {
            "votes": {"A": {"score": 8, "verdict": "ok"}},
            "missing": [],
            "average_score": 8.0,
            "min_score": 8,
            "max_score": 8,
        }

        markdown = render_verdict_markdown("idea", {"claims": []}, votes, "full")

        assert "Средний балл: 8.0/10" in markdown
        assert "коррелированы" in markdown

    def test_degraded_vote_banner_when_votes_missing(self):
        votes = {
            "votes": {"A": {"score": 8, "verdict": "ok"}},
            "missing": ["B"],
            "average_score": 8.0,
            "min_score": 8,
            "max_score": 8,
        }

        markdown = render_verdict_markdown("idea", {"claims": []}, votes, "full")

        assert "DEGRADED" in markdown

    def test_meta_section_rendered_when_given(self):
        meta = {
            "wall_time_seconds": 42.5,
            "agent_calls": 6,
            "agents": [{"name": "A", "command": ["a"]}],
        }

        markdown = render_verdict_markdown(
            "idea", {"claims": []}, EMPTY_VOTES, "full", meta=meta
        )

        assert "## Метаданные прогона" in markdown
        assert "42.5 сек" in markdown
        assert "Вызовов агентов: 6" in markdown

    def test_no_meta_section_when_not_given(self):
        markdown = render_verdict_markdown("idea", {"claims": []}, EMPTY_VOTES, "full")

        assert "## Метаданные прогона" not in markdown

    def test_trajectory_section_rendered_when_given(self):
        trajectory = {
            "trajectory": [
                {"agent": "A", "r2_score": 9, "r3_score": 8, "moved_toward_mean": True}
            ],
            "r3_mean": 8,
        }

        markdown = render_verdict_markdown(
            "idea", {"claims": []}, EMPTY_VOTES, "full", vote_trajectory=trajectory
        )

        assert "### Траектория балла R2 → R3" in markdown
        assert "A: 9 → 8" in markdown
        assert "сжатия к консенсусу" in markdown

    def test_no_trajectory_section_when_empty(self):
        markdown = render_verdict_markdown("idea", {"claims": []}, EMPTY_VOTES, "full")

        assert "Траектория балла" not in markdown


class TestRenderTaskVerdictMarkdown:
    """task-verdict.md — honesty: status, banner, full list of
    unresolved critical_flaws for non-APPROVED statuses."""

    def _base(self, status, **overrides):
        out = {
            "status": status,
            "executor": "claude",
            "executor_reason": "majority",
            "executor_votes": {
                "claude": "claude",
                "codex": "claude",
                "gemini": "codex",
            },
            "executor_missing": [],
            "iterations": 1,
            "reviews": {},
            "work_chars": 500,
            "intent_only": False,
            "aborted_reason": None,
        }
        out.update(overrides)
        return out

    def test_approved_banner_and_no_unresolved_section(self):
        out = self._base(
            "APPROVED",
            reviews={
                1: {
                    "reviews": {
                        "codex": {
                            "verdict": "APPROVED",
                            "critical_flaws": [],
                            "suggested_edits": [],
                        }
                    },
                    "missing": [],
                }
            },
        )
        md = render_task_verdict_markdown("Задача X", out)
        assert "Council-Reviewed: APPROVED" in md
        assert "Задача X" in md
        assert "claude" in md  # исполнитель назван
        assert "Нерешённые замечания" not in md

    def test_partial_banner_and_full_unresolved_list(self):
        out = self._base(
            "PARTIAL",
            reviews={
                1: {
                    "reviews": {
                        "codex": {
                            "verdict": "REQUIRED_FIXES",
                            "critical_flaws": ["нет раздела 2"],
                            "suggested_edits": [],
                        },
                        "gemini": {
                            "verdict": "REQUIRED_FIXES",
                            "critical_flaws": ["факты не сходятся"],
                            "suggested_edits": [],
                        },
                    },
                    "missing": [],
                }
            },
        )
        md = render_task_verdict_markdown("t", out)
        assert "⚠ Council-Reviewed: PARTIAL" in md
        assert "PARTIAL" in md
        # invariant 6: FULL list — both flaws, not a sample
        assert "нет раздела 2" in md
        assert "факты не сходятся" in md
        assert "одобрено советом" not in md.lower()

    def test_degraded_shows_aborted_reason(self):
        out = self._base(
            "DEGRADED",
            aborted_reason="work output < 200 chars — artifact not published as done",
        )
        md = render_task_verdict_markdown("t", out)
        assert "⚠ Council-Reviewed: DEGRADED" in md
        assert "work output < 200 chars" in md

    def test_abort_no_executor(self):
        out = self._base(
            "ABORT",
            executor=None,
            executor_reason="all_none",
            executor_votes={},
            executor_missing=["claude", "codex", "gemini"],
            aborted_reason="no executor chosen (all votes none/invalid)",
        )
        md = render_task_verdict_markdown("t", out)
        assert "⚠ Council-Reviewed: ABORT" in md
        assert "не выбран" in md
        assert "no executor chosen" in md

    def test_intent_only_flag_noted(self):
        out = self._base("PARTIAL", intent_only=True)
        md = render_task_verdict_markdown("t", out)
        assert "намерени" in md

    def test_meta_section_included_when_given(self):
        out = self._base("APPROVED")
        meta = {
            "wall_time_seconds": 12.3,
            "agent_calls": 5,
            "agents": [{"name": "claude", "command": []}],
        }
        md = render_task_verdict_markdown("t", out, meta)
        assert "12.3" in md
        assert "claude" in md

    def test_no_meta_section_when_absent(self):
        out = self._base("APPROVED")
        md = render_task_verdict_markdown("t", out)
        assert "Метаданные прогона" not in md

"""E2E-прогон режима задания через реальные субпроцессы — 0 токенов,
без единого настоящего LLM-вызова: "агенты" — inline-скрипты `python -c ...`
(тот же паттерн, что и tests/test_agent_runner.py), которые читают промпт из
argv[1] или stdin и отвечают в зависимости от того, какой это ход
(различают фазы по маркерам промпта: "ROUND 2", "REVIEW THE COUNCIL",
"EXECUTE THE TASK" — те же маркеры, что проверяют юнит-тесты промптов в
tests/test_config.py).

Цель — не мокать run_agent/run_task_pipeline (это уже сделано в
tests/test_task_mode.py), а прогнать ВЕСЬ путь main.run_council_async
(R1 -> R2 -> run_task_pipeline) целиком, как это происходит в реальном
запуске, и проверить итоговые артефакты на диске для трёх исходов:
APPROVED, PARTIAL (потолок правок исчерпан) и ABORT (все голоса "none").
"""

import asyncio
import json
import sys

from src.core.session import SessionWriter

PYTHON = sys.executable


def run(coro):
    return asyncio.run(coro)


def agent_script(executor_vote: str, review_verdict: str) -> str:
    """One universal agent script: branches by marker in the prompt just
    like real Round1/Round2/work/review prompts differ
    (see src/config.py). executor_vote — what to return in the "executor" field of R2;
    review_verdict — what to return as "verdict" in review
    (the executor's review branch simply isn't called — it isn't reviewed)."""
    return f"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8")  # child's own codepage may not be UTF-8 on Windows
prompt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()

if "REVIEW THE COUNCIL" in prompt:
    payload = {{
        "verdict": {review_verdict!r},
        "critical_flaws": [] if {review_verdict!r} == "APPROVED" else ["раздел 2 отсутствует"],
        "suggested_edits": [],
    }}
    print("```json")
    print(json.dumps(payload, ensure_ascii=False))
    print("```")
elif "EXECUTE THE TASK" in prompt:
    print("# Результат работы")
    print()
    print("Полный текст решения задачи. " * 30)
elif "ROUND 2" in prompt:
    payload = {{
        "major_errors": [], "unsupported_assumptions": [], "strongest_arguments": [],
        "disagreements": [], "self_corrections": [], "missing_information": [],
        "critical_claims": [], "revised_position": "ok",
        "executor": {executor_vote!r},
        "claim_status": [], "final_vote": {{"score": 7, "verdict": "ok"}},
    }}
    print("```json")
    print(json.dumps(payload, ensure_ascii=False))
    print("```")
else:
    payload = {{"claims": [{{"id": "CLM-1", "statement": "утверждение", "status": "ASSUMPTION", "evidence_ref": None, "falsification_test": "t"}}]}}
    print("```json")
    print(json.dumps(payload, ensure_ascii=False))
    print("```")
"""


def make_agent(name: str, executor_vote: str, review_verdict: str = "APPROVED"):
    script = agent_script(executor_vote, review_verdict)
    return (name, [PYTHON, "-c", script, "{prompt}"])


class TestTaskModeE2E:
    def _run(self, tmp_path, council, max_review_iterations=2, work_timeout=30.0):
        from main import run_council_async

        session = SessionWriter(tmp_path / "sessions")
        session.write_idea("Тестовая задача e2e")

        run(
            run_council_async(
                "Тестовая задача e2e",
                council,
                session,
                quick_mode=False,
                preset=None,
                no_open=True,
                task_mode=True,
                max_reviews=max_review_iterations,
                work_timeout=work_timeout,
            )
        )
        return session

    def test_approved_end_to_end(self, tmp_path):
        # All three vote for claude; codex/gemini review and approve immediately.
        council = [
            make_agent("claude", executor_vote="claude"),
            make_agent("codex", executor_vote="claude", review_verdict="APPROVED"),
            make_agent("gemini", executor_vote="claude", review_verdict="APPROVED"),
        ]
        session = self._run(tmp_path, council)

        task_out = json.loads(
            (session.dir / "task_verdict.json").read_text(encoding="utf-8")
        )
        assert task_out["status"] == "APPROVED"
        assert task_out["executor"] == "claude"
        assert task_out["iterations"] == 1

        assert (session.dir / "work-final.md").exists()
        verdict = (session.dir / "task-verdict.md").read_text(encoding="utf-8")
        assert "Council-Reviewed: APPROVED" in verdict

    def test_partial_after_cap_end_to_end(self, tmp_path):
        # codex/gemini always ask for the same fix — the cap of 2 is
        # exhausted without a majority -> honest PARTIAL, full list of
        # unresolved flaws in task-verdict.md.
        council = [
            make_agent("claude", executor_vote="claude"),
            make_agent(
                "codex", executor_vote="claude", review_verdict="REQUIRED_FIXES"
            ),
            make_agent(
                "gemini", executor_vote="claude", review_verdict="REQUIRED_FIXES"
            ),
        ]
        session = self._run(tmp_path, council, max_review_iterations=2)

        task_out = json.loads(
            (session.dir / "task_verdict.json").read_text(encoding="utf-8")
        )
        assert task_out["status"] == "PARTIAL"
        assert task_out["iterations"] == 2
        assert (session.dir / "review-2.json").exists()

        verdict = (session.dir / "task-verdict.md").read_text(encoding="utf-8")
        assert "Council-Reviewed: PARTIAL" in verdict
        assert (
            "раздел 2 отсутствует" in verdict
        )  # инвариант 6: замечания названы, не скрыты
        assert "одобрено советом" not in verdict.lower()

    def test_abort_all_none_end_to_end(self, tmp_path):
        # Nobody takes the job — honest abort before the executor call
        # (0 work calls); the R1/R2 discussion stays on disk.
        council = [
            make_agent("claude", executor_vote="none"),
            make_agent("codex", executor_vote="none"),
            make_agent("gemini", executor_vote="none"),
        ]
        session = self._run(tmp_path, council)

        task_out = json.loads(
            (session.dir / "task_verdict.json").read_text(encoding="utf-8")
        )
        assert task_out["status"] == "ABORT"
        assert task_out["executor"] is None

        assert not (session.dir / "work.md").exists()
        assert not (session.dir / "work-final.md").exists()
        assert (session.dir / "executor.json").exists()
        assert (session.dir / "round1").is_dir()  # дискуссия R1/R2 сохранена
        assert (session.dir / "round2").is_dir()

        verdict = (session.dir / "task-verdict.md").read_text(encoding="utf-8")
        assert "Council-Reviewed: ABORT" in verdict

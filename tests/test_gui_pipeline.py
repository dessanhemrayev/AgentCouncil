"""Tests for the GUI pipeline panel (agent x stage matrix + progress) and
orchestrator stage callbacks (on_stage / on_agent_status with stage).

The UI part builds on a hidden CTkToplevel (as in test_gui.py) — no real
council run; the orchestrator part patches rounds (as in test_rotation.py).
"""

import asyncio

import pytest

ctk = pytest.importorskip("customtkinter", exc_type=ImportError)

from src.gui import pipeline  # noqa: E402
from src.core.models import AgentResult  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def pump(root, times=5):
    """Pumps the root.after(0, ...) queue — callbacks come from the worker."""
    for _ in range(times):
        root.update()


@pytest.fixture(scope="module")
def ctk_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def gui(ctk_root):
    from src.gui import CouncilGUI

    top = ctk.CTkToplevel(ctk_root)
    top.withdraw()
    app = CouncilGUI(top)
    yield app
    top.destroy()


class TestStageDisplayName:
    def test_static_stages(self):
        assert pipeline.stage_display_name("round1") == "R1 Analysis"
        assert pipeline.stage_display_name("round3") == "R3 Round"
        assert pipeline.stage_display_name("vote") == "Voting"

    def test_task_stage(self):
        assert pipeline.stage_display_name("task") == "Work + Review"

    def test_dynamic_review_and_fix_stages(self):
        assert pipeline.stage_display_name("task-review-2") == "Review No. 2"
        assert pipeline.stage_display_name("task-fix-1") == "Fix No. 1"


class TestMatrix:
    def test_council_mode_columns(self, gui):
        pipeline.set_pipeline_agents(gui, ["A", "B"], task_mode=False)

        assert gui.pipeline_columns == ["round1", "round2", "round3"]
        assert set(gui.agent_cells) == {
            (name, stage) for name in ("A", "B") for stage in gui.pipeline_columns
        }

    def test_task_mode_columns_keep_round1_round2(self, gui):
        """Task mode matrix must contain R1 and R2.

        Regression (sessions/run-002): the column was one ("task") — statuses
        from R1/R2 landed in it too and got overwritten by the work phase: layout became soup.
        """
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)

        assert gui.pipeline_columns == ["round1", "round2", "task"]
        assert ("A", "round1") in gui.agent_cells
        assert ("A", "round2") in gui.agent_cells
        assert ("A", "task") in gui.agent_cells

    def test_task_mode_r1_r2_not_overwritten_by_task_phase(self, gui):
        """R1/R2 statuses live in their own cells, task phase in its own."""
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)

        pipeline.update_agent_cell(gui, "A", "round1", "done", "42с")
        pipeline.update_agent_cell(gui, "A", "round2", "done", "95с")
        pipeline.update_agent_cell(gui, "A", "task", "running", "300с")

        assert gui.agent_cells[("A", "round1")].cget("text") == "✓ 42с"
        assert gui.agent_cells[("A", "round2")].cget("text") == "✓ 95с"
        assert gui.agent_cells[("A", "task")].cget("text").startswith("●")

    def test_vote_stage_has_no_cells(self, gui):
        """Voting is aggregation without agent calls: no cells, the stage
        doesn't overwrite existing statuses (the "Verdict" chip is only in chips)."""
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)

        pipeline.update_agent_cell(gui, "A", "vote", "done", "5с")

        assert gui.agent_cells[("A", "task")].cget("text") == "—"

    def test_dynamic_task_stage_maps_to_task_column(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)

        pipeline.update_agent_cell(gui, "A", "task-review-1", "running", "5с")
        cell = gui.agent_cells[("A", "task")]
        assert "●" in cell.cget("text")

    def test_cell_glyphs_per_status(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=False)

        pipeline.update_agent_cell(gui, "A", "round1", "done", "42с")
        assert gui.agent_cells[("A", "round1")].cget("text") == "✓ 42с"

        pipeline.update_agent_cell(gui, "A", "round2", "error")
        assert gui.agent_cells[("A", "round2")].cget("text").startswith("✗")

        pipeline.update_agent_cell(gui, "A", "round3", "timeout")
        assert gui.agent_cells[("A", "round3")].cget("text").startswith("⏱")


class TestStageChips:
    def test_set_stage_highlights_active_and_previous_done(self, gui):
        pipeline.set_stage(gui, "round2")

        assert gui.current_stage == "round2"
        active = gui.stage_chip_labels["round2"].cget("text")
        done = gui.stage_chip_labels["round1"].cget("text")
        pending = gui.stage_chip_labels["vote"].cget("text")
        assert active.startswith("●")
        assert done.startswith("✓")
        assert pending.startswith("○")

    def test_task_stage_maps_to_round3_chip(self, gui):
        pipeline.set_stage(gui, "task-review-1")

        assert gui.current_stage == "task-review-1"
        assert gui.stage_chip_labels["round3"].cget("text").startswith("●")
        assert gui.stage_chip_labels["round1"].cget("text").startswith("✓")
        assert gui.stage_chip_labels["round2"].cget("text").startswith("✓")

    def test_mark_all_done(self, gui):
        pipeline.mark_all_done(gui)

        assert all(
            chip.cget("text").startswith("✓") for chip in gui.stage_chip_labels.values()
        )


class TestTaskModeChips:
    """Chip labels depend on the mode (regression sessions/run-002)."""

    def test_task_mode_relabels_r3_and_vote(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)

        texts = {key: chip.cget("text") for key, chip in gui.stage_chip_labels.items()}
        assert "Работа+Ревью" in texts["round3"]
        assert "Вердикт" in texts["vote"]
        # R1/R2 aren't renamed
        assert "R1 Анализ" in texts["round1"]
        assert "R2 Критика" in texts["round2"]

    def test_council_mode_restores_labels(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=False)

        texts = {key: chip.cget("text") for key, chip in gui.stage_chip_labels.items()}
        assert "R3 Раунд" in texts["round3"]
        assert "Голосование" in texts["vote"]

    def test_task_stage_highlights_renamed_chip(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)
        pipeline.set_stage(gui, "task-review-1")

        chip3 = gui.stage_chip_labels["round3"].cget("text")
        chip1 = gui.stage_chip_labels["round1"].cget("text")
        assert chip3.startswith("●")
        assert "Работа+Ревью" in chip3
        assert chip1.startswith("✓")

    def test_task_mode_finishes_with_verdict_chip_done(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)
        pipeline.set_stage(gui, "vote")

        chip4 = gui.stage_chip_labels["vote"].cget("text")
        assert chip4.startswith("●")
        assert "Вердикт" in chip4


class TestAgentStatusWithStage:
    def test_running_then_done_counts_call(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=False)
        gui.call_total = 3

        gui._update_agent_status("A", "running", "round1")
        pump(gui.root)
        assert gui.cell_status[("A", "round1")] == "running"

        gui._update_agent_status("A", "done", "round1")
        pump(gui.root)
        assert gui.call_done == 1
        cell = gui.agent_cells[("A", "round1")]
        assert cell.cget("text").startswith("✓")

    def test_task_phase_running_extends_total(self, gui):
        """Task phases: the number of calls is unknown in advance (review iterations) —
        each starting call adds itself to the progress denominator."""
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=True)
        gui.call_total = 7

        gui._update_agent_status("A", "running", "task-review-1")
        pump(gui.root)
        assert gui.call_total == 8

        # R1/R2 don't expand the denominator
        gui.call_total = 8
        gui._update_agent_status("A", "running", "round1")
        pump(gui.root)
        assert gui.call_total == 8

    def test_status_without_stage_keeps_sidebar_only(self, gui):
        pipeline.set_pipeline_agents(gui, ["A"], task_mode=False)
        before = gui.call_done

        gui._update_agent_status("A", "done")
        pump(gui.root)

        assert gui.call_done == before


class _FakeSession:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "sessions" / "run-001"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write_idea(self, idea):
        pass

    def write_agent_result(self, *args, **kwargs):
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

    def write_task_verdict(self, markdown):
        path = self.dir / "task-verdict.md"
        path.write_text(markdown, encoding="utf-8")
        return path


class TestOrchestratorStageCallbacks:
    def test_emits_stages_and_stage_tagged_statuses(self, tmp_path, monkeypatch):
        import main

        async def fake_round1(
            idea,
            council,
            session,
            evidence_dir=None,
            quick_mode=False,
            on_agent_status=None,
            round_timeout=600.0,
        ):
            if on_agent_status is not None:
                on_agent_status("A", "running")
            return (
                {
                    name: AgentResult(name=name, output=f"r1 {name}")
                    for name, _ in council
                },
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
            if on_agent_status is not None:
                on_agent_status("A", "running")
            return {
                name: AgentResult(name=name, output=f"r3 {name}") for name, _ in council
            }

        monkeypatch.setattr("src.core.orchestrator.run_round1", fake_round1)
        monkeypatch.setattr("src.core.orchestrator.run_round2", fake_round2)
        monkeypatch.setattr("src.core.orchestrator.run_round3", fake_round3)
        monkeypatch.chdir(tmp_path)

        stages = []
        statuses = []
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"])]

        run(
            main.run_council_async(
                "idea",
                council,
                session,
                quick_mode=False,
                preset=None,
                no_open=True,
                on_agent_status=lambda name, status, stage: statuses.append(
                    (name, status, stage)
                ),
                on_stage=stages.append,
            )
        )

        assert stages == ["round1", "round2", "round3", "vote"]
        assert ("A", "running", "round1") in statuses
        assert ("A", "running", "round3") in statuses

    def test_task_mode_emits_stages_and_vote_verdict(self, tmp_path, monkeypatch):
        """Task mode: round1 → round2 → task phases → vote (verdict).

        Regression (sessions/run-002): task mode didn't emit "vote" — the
        "Verdict" chip in GUI stayed gray until the end of the run.
        """
        import main

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
                {
                    name: AgentResult(name=name, output=f"r1 {name}")
                    for name, _ in council
                },
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

        async def fake_task_pipeline(
            idea,
            council,
            roster,
            r1,
            r2,
            session,
            max_review_iterations=2,
            work_timeout=1800.0,
            on_agent_status=None,
            on_stage=None,
        ):
            if on_stage is not None:
                on_stage("task")
                on_stage("task-review-1")
            return {
                "status": "APPROVED",
                "executor": "A",
                "executor_reason": "majority",
                "executor_votes": {"B": "A"},
                "executor_missing": [],
                "iterations": 1,
                "reviews": {"1": {"reviews": {}, "missing": []}},
                "work_chars": 100,
                "intent_only": False,
                "aborted_reason": None,
            }

        monkeypatch.setattr("src.core.orchestrator.run_round1", fake_round1)
        monkeypatch.setattr("src.core.orchestrator.run_round2", fake_round2)
        monkeypatch.setattr(
            "src.core.orchestrator.run_task_pipeline", fake_task_pipeline
        )
        monkeypatch.chdir(tmp_path)

        stages = []
        session = _FakeSession(tmp_path)
        council = [("A", ["a"]), ("B", ["b"])]

        run(
            main.run_council_async(
                "idea",
                council,
                session,
                quick_mode=False,
                preset=None,
                no_open=True,
                task_mode=True,
                on_stage=stages.append,
            )
        )

        assert stages == ["round1", "round2", "task", "task-review-1", "vote"]


class TestMarkAllDoneError:
    """Final run with error: "✗" chips are red (error badge), not success."""

    def test_error_variant_marks_chips_failed(self, gui):
        pipeline.mark_all_done(gui, ok=False)

        fg_expected = pipeline.STAGE_COLORS["error"][0]
        for chip in gui.stage_chip_labels.values():
            assert chip.cget("text").startswith("✗")
            assert chip.cget("fg_color") == fg_expected

    def test_default_ok_variant_unchanged(self, gui):
        """No arguments — previous behavior (green "✓"), compatibility."""
        pipeline.mark_all_done(gui)

        fg_expected = pipeline.STAGE_COLORS["done"][0]
        for chip in gui.stage_chip_labels.values():
            assert chip.cget("text").startswith("✓")
            assert chip.cget("fg_color") == fg_expected

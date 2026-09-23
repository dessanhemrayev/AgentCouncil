"""Council run orchestration: rounds 1/2/3, task mode, verdict.

Orchestration lives in core and is equally accessible to the CLI and the
GUI (previously both imported run_council_async from the CLI entry point);
root main.py handles only arguments, interactive mode, and dispatch.

load_config/save_config (rotation) — moved to src/config/agents.py;
picked up here for backward compatibility (root main still uses them
via re-export).
"""

import os
import time
from pathlib import Path
from typing import Callable, List, Optional

from ..cli.render import (
    print_results,
    print_vote_summary,
    print_vote_trajectory,
    render_claims_map_lines,
    render_task_verdict_markdown,
    render_verdict_markdown,
    report_dropped,
)
from ..cli.spinner import Spinner
from ..config.agents import load_config, save_config
from .aggregation import (
    aggregate_claims,
    aggregate_votes,
    compute_vote_trajectory,
    extract_json_block,
)
from .council import (
    _coerce_member,
    ok,
    run_round1,
    run_round2,
    run_round3,
    run_task_pipeline,
)
from .models import KIND_OPENAI, AgentResult, CouncilMember
from .session import SessionWriter
from .verification import verify_claims_quotes
from .web_utils import search_web_async, format_search_results_for_prompt


def _member_name(member) -> str:
    if hasattr(member, "name"):
        return member.name
    if (
        isinstance(member, (tuple, list))
        and len(member) >= 2
        and isinstance(member[0], str)
    ):
        return member[0]
    raise TypeError(f"unsupported council member: {member!r}")


def _member_meta(member: CouncilMember) -> dict:
    """meta.json entry — outward `type`, never `kind` (plan правка 2):
    cli keeps its command as today; openai carries model + key env."""
    member = _coerce_member(member)
    if member.kind == KIND_OPENAI:
        meta = {
            "name": member.name,
            "type": "openai",
            "model": member.model,
            "api_key_env": member.api_key_env,
        }
        if member.base_url:
            meta["base_url"] = member.base_url
        return meta
    return {"name": member.name, "type": "cli", "command": member.command}


def _make_stage_cb(
    on_agent_status: Optional[Callable[[str, str, str], None]], stage: str
) -> Optional[Callable[[str, str], None]]:
    """Wraps on_agent_status, adding a stage label to each event.

    The external callback (GUI) receives (agent_name, status, stage) — using
    the label it builds an "Agent × Stage" matrix and highlights the active
    pipeline stage. Internal calls (council.run_member → runner) remain
    two-argument — the wrapper supplies the stage itself. CLI
    (on_agent_status=None) is unaffected — returns None.
    """
    if on_agent_status is None:
        return None

    def cb(agent_name: str, status: str) -> None:
        on_agent_status(agent_name, status, stage)

    return cb


def _emit_stage(on_stage: Optional[Callable[[str], None]], stage: str) -> None:
    """Notifies UI of transition to a new stage (for pipeline chip highlighting)."""
    if on_stage is not None:
        on_stage(stage)


async def _run_web_search(
    query: str,
    session: SessionWriter,
    evidence_dir: Optional[Path],
    config: dict,
) -> Optional[Path]:
    """Run web search and save results as virtual evidence.

    Returns the path to the saved search results file, or None if disabled/failed.
    """
    web_search_config = config.get("web_search", {})
    if not web_search_config.get("enabled", False):
        return None
    if evidence_dir is None:
        # Belt-and-suspenders: run_council_async only leaves evidence_dir
        # None when web_search is disabled, so this should be unreachable —
        # but this function must stay safe to call with evidence_dir=None
        # regardless of what the caller's config says.
        return None

    max_results = web_search_config.get("max_results", 5)
    timeout = web_search_config.get("timeout", 30.0)

    try:
        print(f"\n[web_search] Searching: {query[:80]}...")
        results = await search_web_async(
            query, max_results=max_results, timeout=timeout
        )
        if not results:
            print("[web_search] No results found")
            return None

        formatted = format_search_results_for_prompt(results)
        # Save as virtual evidence file
        search_file = evidence_dir / "web_search_results.md"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        search_file.write_text(formatted, encoding="utf-8")
        print(f"[web_search] Saved {len(results)} results to {search_file.name}")
        return search_file
    except Exception as exc:
        print(f"[web_search] Search failed: {exc}")
        return None


async def run_council_async(
    idea: str,
    council: List[CouncilMember],
    session: SessionWriter,
    quick_mode: bool,
    preset: Optional[str],
    no_open: bool,
    evidence_dir: Optional[Path] = None,
    config_path: Optional[str] = None,
    adversarial: bool = False,
    task_mode: bool = False,
    max_reviews: int = 2,
    work_timeout: float = 1800.0,
    round_timeout: float = 1200.0,
    on_agent_status: Optional[Callable[[str, str, str], None]] = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> None:
    """Run the council rounds asynchronously.

    on_agent_status: optional callback (agent_name, status, stage) for live GUI status.
        Status values: "running", "done", "error", "timeout".
        Stage values: "round1", "round2", "round3", "task".
    on_stage: optional callback (stage) — fired when the orchestrator moves
        to the next stage (including the pure-Python "vote" aggregation).
    """
    wall_start = time.monotonic()

    # Load config for web_search settings
    config = load_config(config_path)
    web_search_enabled = config.get("web_search", {}).get("enabled", False)

    # Auto-provision an evidence dir only when web search will actually write
    # into it. Leaving evidence_dir exactly as the caller passed it otherwise
    # (None included) matters: session.dir isn't guaranteed to be a Path —
    # tests substitute a plain string session stub that must never hit disk
    # (tests/test_main_flow.py), and `str / "evidence"` raises TypeError.
    if web_search_enabled and evidence_dir is None:
        session_dir = Path(session.dir) if isinstance(session.dir, str) else session.dir
        evidence_dir = session_dir / "evidence"

    # Web search for Round 1: search based on the idea
    await _run_web_search(idea, session, evidence_dir, config)

    status_cb_r1 = _make_stage_cb(on_agent_status, "round1")

    _emit_stage(on_stage, "round1")
    print("\n" + "=" * 80)
    print("ROUND 1 — Independent Analysis")
    print("=" * 80)

    with Spinner(f"ROUND 1 — thinking: {', '.join(_member_name(m) for m in council)}"):
        round1_results, clm_inventory, degradation_status = await run_round1(
            idea,
            council,
            session,
            evidence_dir,
            quick_mode,
            status_cb_r1,
            round_timeout,
        )
    print_results(round1_results)

    # Report CLM degradation status
    if degradation_status == "partial":
        print("\n⚠ R1: PARTIAL — CLM inventory parsed from some agents only")
    elif degradation_status == "degraded":
        print("\n⚠ R1: DEGRADED — CLM inventory unavailable, proceeding on raw texts")

    if len(council) < 2:
        return

    alive1 = [m for m in council if ok(round1_results[_member_name(m)])]
    report_dropped(council, alive1, "Round 1")

    if len(alive1) < 2:
        print(
            "\nFewer than two agents provided a meaningful Round 1 response — cannot continue."
        )
        return

    if quick_mode:
        # R1 only, with the mandatory needs_full_council question.
        print("\n" + "=" * 80)
        print("QUICK MODE — Round 1 only")
        print("=" * 80)
        for name, result in round1_results.items():
            if not ok(result):
                continue
            parsed = extract_json_block(result.output)
            nfc = parsed.get("needs_full_council") if isinstance(parsed, dict) else None
            if isinstance(nfc, dict):
                print(
                    f"{name}: is the full council needed? {nfc.get('answer')} — {nfc.get('reason')}"
                )
            else:
                print(f"{name}: did not return needs_full_council")
        print("\nQuick mode complete. Run with --full for complete 3-round evaluation.")
        return

    status_cb_r2 = _make_stage_cb(on_agent_status, "round2")
    _emit_stage(on_stage, "round2")

    # Web search for Round 2: search for key claims from Round 1
    r1_query = (
        idea
        + " "
        + " ".join(r.output[:200] for r in round1_results.values() if ok(r))[:500]
    )
    await _run_web_search(r1_query, session, evidence_dir, config)

    print("\n" + "=" * 80)
    print("ROUND 2 — Cross-Critique")
    print("=" * 80)

    with Spinner("ROUND 2 — Cross-Critique: everyone reads everyone else's Round 1"):
        round2 = await run_round2(
            idea,
            alive1,
            round1_results,
            session,
            evidence_dir,
            preset,
            adversarial,
            status_cb_r2,
            round_timeout,
        )
    print_results(round2, show_json=True)

    alive2 = [
        m
        for m in alive1
        if ok(round2.get(_member_name(m), AgentResult(name=_member_name(m))))
    ]
    report_dropped(alive1, alive2, "Round 2")

    if len(alive2) < 1:
        print(
            "\nNo agent provided a meaningful Round 2 response — cannot continue."
        )
        return

    if len(alive2) < 2:
        print(
            f"\n⚠ Warning: only {len(alive2)} agent provided a meaningful Round 2 response — Round 3 continues with one agent."
        )

    # Task mode: after R1/R2 -> executor/work/review pipeline (instead of R3)
    if task_mode:
        roster = [_member_name(m) for m in alive1]
        print("\n" + "=" * 80)
        print("TASK MODE — executor vote (R2) + work + review loop")
        print("=" * 80)
        with Spinner("TASK MODE — work + review"):
            task_out = await run_task_pipeline(
                idea,
                council,
                roster,
                round1_results,
                round2,
                session,
                max_review_iterations=max_reviews,
                work_timeout=work_timeout,
                on_agent_status=_make_stage_cb(on_agent_status, "task"),
                on_stage=on_stage,
            )
        task_meta = {
            "wall_time_seconds": time.monotonic() - wall_start,
            "agent_calls": len(council)
            + len(alive1)
            + len(alive2)
            + 1
            + task_out["iterations"] * (len(alive2) - 1)
            + max(0, task_out["iterations"] - 1),
            "agents": [_member_meta(m) for m in council],
            "preset": preset,
            "quick_mode": False,
            "task_mode": True,
            "degradation_status": degradation_status,
        }
        # Task-mode verdict: the GUI "Verdict" chip reuses the "vote" event.
        _emit_stage(on_stage, "vote")
        session.write_meta(task_meta)
        verdict_markdown = render_task_verdict_markdown(idea, task_out, task_meta)
        vpath = session.write_task_verdict(verdict_markdown)
        print(f"\nTask status: {task_out['status']}")
        if task_out.get("aborted_reason"):
            print(f"  reason: {task_out['aborted_reason']}")
        print(f"task-verdict: {vpath}")
        if vpath.exists() and not no_open:
            try:
                os.startfile(vpath)
            except Exception:
                pass
        return

    # R3 first-move rotation between runs — the counter lives in
    # council.json (must survive this session and affect the next one).
    rotation_config = load_config(config_path)
    round3_rotation = rotation_config.get("round3_rotation", 0)
    if not isinstance(round3_rotation, int) or round3_rotation < 0:
        round3_rotation = 0
    start_index = round3_rotation % len(alive2)
    rotated_order = alive2[start_index:] + alive2[:start_index]

    # Web search for Round 3: search for key disputes from R2
    r2_query = (
        idea + " " + " ".join(r.output[:200] for r in round2.values() if ok(r))[:500]
    )
    await _run_web_search(r2_query, session, evidence_dir, config)

    status_cb_r3 = _make_stage_cb(on_agent_status, "round3")
    _emit_stage(on_stage, "round3")
    print("\n" + "=" * 80)
    print("ROUND 3 — Sequential Rebuttal")
    print(f"Move order: {' → '.join(_member_name(m) for m in rotated_order)}")
    print("=" * 80)

    with Spinner("ROUND 3 — Sequential Rebuttal") as spinner:
        round3 = await run_round3(
            idea,
            alive2,
            round1_results,
            round2,
            session,
            start_index=start_index,
            on_move=lambda name: spinner.update(f"ROUND 3 — turn: {name}"),
            evidence_dir=evidence_dir,
            on_agent_status=status_cb_r3,
            round_timeout=round_timeout,
        )
    print_results(round3, show_json=True)

    rotation_config["round3_rotation"] = round3_rotation + 1
    save_config(rotation_config, config_path)

    # Citation mechanics: verify quotes in evidence
    citation_mismatches: List[str] = []
    if evidence_dir:
        citation_mismatches = verify_claims_quotes(evidence_dir, round2, round3)
        if citation_mismatches:
            print("\n" + "=" * 80)
            print("CITATION VERIFICATION — MISMATCHES FOUND")
            print("=" * 80)
            for m in citation_mismatches:
                print(m)
            # Write mismatches to session for verdict.md
            session.write_citation_mismatches(citation_mismatches)

    # Claim-level map — CLM x round matrix, pure aggregation (no LLM calls)
    claims_map = aggregate_claims(clm_inventory, round2, round3, evidence_dir)

    print("\n" + "=" * 80)
    print("CLAIM MAP — Survival of Claims")
    print("=" * 80)
    for line in render_claims_map_lines(claims_map):
        print(line)

    _emit_stage(on_stage, "vote")
    print("\n" + "=" * 80)
    print("FINAL — Council Vote")
    print("=" * 80)

    vote_summary = aggregate_votes(round3)
    print_vote_summary(vote_summary)
    session.write_vote(vote_summary)
    session.write_claims_map(claims_map)

    # R2 -> R3 score trajectory — mechanical signal of compression toward consensus
    vote_trajectory = compute_vote_trajectory(round2, round3)
    print_vote_trajectory(vote_trajectory)
    session.write_vote_trajectory(vote_trajectory)

    meta = {
        "wall_time_seconds": time.monotonic() - wall_start,
        "agent_calls": len(council) + len(alive1) + len(alive2),
        "agents": [_member_meta(m) for m in council],
        "preset": preset,
        "quick_mode": quick_mode,
        "degradation_status": degradation_status,
    }
    session.write_meta(meta)

    verdict_markdown = render_verdict_markdown(
        idea,
        claims_map,
        vote_summary,
        degradation_status,
        citation_mismatches,
        meta,
        vote_trajectory,
    )
    verdict_path = session.write_verdict(verdict_markdown)
    if verdict_path.exists() and not no_open:
        try:
            os.startfile(verdict_path)
        except Exception:
            pass

"""Council result rendering — the CLM map and verdict.md.

Single rendering entry point for the CLI and the GUI: both sides call these
functions over the same structures (aggregate_claims/aggregate_votes)
rather than recomputing the map — the GUI and the CLI must produce an
identical verdict.md.
"""

import json
from typing import Dict, List, Optional

from ..core.aggregation import extract_json_block
from ..core.models import AgentResult, CouncilMember


def render_claims_map_lines(claims_map: dict) -> List[str]:
    """Text CLM map for the console: one row per claim, with its status
    and per-round interpretation, plus id mismatches between agents."""
    lines: List[str] = []
    claims = claims_map.get("claims", [])
    untracked = claims_map.get("untracked_r2", []) + claims_map.get("untracked_r3", [])

    if not claims and not untracked:
        lines.append("(claims map unavailable — no CLM inventory was recognized)")
        return lines

    for claim in sorted(claims, key=lambda c: c["id"]):
        statement = claim.get("statement") or "(no text)"
        status = claim.get("final_status", "UNRESOLVED")
        conflict = (
            f" [conflict: {claim['conflict_category']}]"
            if claim.get("conflict_category")
            else ""
        )
        lines.append(f"{claim['id']} [{status}]{conflict}: {statement}")

        variants = claim.get("variants", [])
        if len(variants) > 1:
            others = ", ".join(v["agent"] for v in variants[1:])
            lines.append(
                f"    (the same id was also used by: {others} — wording may differ)"
            )

        for row in claim.get("r2", []):
            note = (
                " ⚠ QUOTE NOT FOUND IN EVIDENCE"
                if row.get("quote_verified") is False
                else ""
            )
            lines.append(f"    R2 {row['agent']}: {row.get('raw_status')}{note}")

        for row in claim.get("r3", []):
            note = (
                " ⚠ QUOTE NOT FOUND IN EVIDENCE"
                if row.get("quote_verified") is False
                else ""
            )
            lines.append(f"    R3 {row['agent']}: {row.get('raw_status')}{note}")

    if untracked:
        lines.append("")
        lines.append(
            f"Untracked (reference to a CLM id missing from the R1 inventory): {len(untracked)}"
        )

    return lines


def render_verdict_markdown(
    idea: str,
    claims_map: dict,
    vote_summary: dict,
    degradation_status: str,
    citation_mismatches: Optional[List[str]] = None,
    meta: Optional[dict] = None,
    vote_trajectory: Optional[dict] = None,
) -> str:
    """verdict.md — the main run artifact: the claims map is the primary
    verdict, the average vote score is the secondary metric, kept as a
    section below ("the score is downgraded, not deleted")."""
    lines: List[str] = ["# AgentCouncil — verdict", "", "## Idea", "", idea.strip(), ""]

    lines.append("## Claim survival map")
    lines.append("")
    if degradation_status == "degraded":
        lines.append(
            "⚠ R1: DEGRADED — claims map unavailable; rounds continued using raw text."
        )
        lines.append("")
    elif degradation_status == "partial":
        lines.append(
            "⚠ R1: PARTIAL — CLM inventory was not recognized for every agent; the map is partial."
        )
        lines.append("")

    claims = claims_map.get("claims", [])
    if claims:
        lines.append("| CLM | Status | Conflict | Claim |")
        lines.append("|---|---|---|---|")
        for claim in sorted(claims, key=lambda c: c["id"]):
            statement = (
                (claim.get("statement") or "").replace("|", "\\|").replace("\n", " ")
            )
            lines.append(
                f"| {claim['id']} | {claim.get('final_status', 'UNRESOLVED')} | "
                f"{claim.get('conflict_category') or ''} | {statement} |"
            )
        lines.append("")

        for claim in sorted(claims, key=lambda c: c["id"]):
            lines.append(f"### {claim['id']}")
            lines.append("")
            lines.append(f"- Status: **{claim.get('final_status', 'UNRESOLVED')}**")
            if claim.get("conflict_category"):
                lines.append(f"- Conflict type: {claim['conflict_category']}")
            for v in claim.get("variants", []):
                lines.append(
                    f"- R1 ({v['agent']}): {v.get('status')} — {v.get('statement')}"
                )
            for row in claim.get("r2", []):
                quote = (
                    f" — quote: '{row['evidence_quote']}' ({row['evidence_ref']})"
                    if row.get("evidence_quote")
                    else ""
                )
                bad = (
                    " ⚠ quote not found in evidence"
                    if row.get("quote_verified") is False
                    else ""
                )
                lines.append(
                    f"- R2 ({row['agent']}): {row.get('raw_status')}{quote}{bad}"
                )
            for row in claim.get("r3", []):
                quote = (
                    f" — quote: '{row['evidence_quote']}' ({row['evidence_ref']})"
                    if row.get("evidence_quote")
                    else ""
                )
                bad = (
                    " ⚠ quote not found in evidence"
                    if row.get("quote_verified") is False
                    else ""
                )
                lines.append(
                    f"- R3 ({row['agent']}): {row.get('raw_status')}{quote}{bad}"
                )
            lines.append("")
    else:
        lines.append(
            "(map unavailable — no agent returned a recognizable CLM inventory)"
        )
        lines.append("")

    untracked = claims_map.get("untracked_r2", []) + claims_map.get("untracked_r3", [])
    if untracked:
        lines.append(
            f"⚠ Untracked references to nonexistent CLM ids: {len(untracked)} (see claims.json)."
        )
        lines.append("")

    unsupported = claims_map.get("unsupported_assumptions", [])
    if unsupported:
        lines.append("## Unsupported assumptions")
        lines.append("")
        for ua in unsupported:
            ref = f" (CLM: {ua['claim_id']})" if ua.get("claim_id") else ""
            lines.append(
                f"- [{ua.get('agent')}] {ua.get('assumption')}{ref} — {ua.get('reason')}"
            )
        lines.append("")

    if citation_mismatches:
        lines.append("## Citation mismatches")
        lines.append("")
        for m in citation_mismatches:
            lines.append(f"- {m}")
        lines.append("")

    lines.append("## Secondary metric: council voting")
    lines.append("")
    votes = vote_summary.get("votes", {})
    missing = vote_summary.get("missing", [])
    total = len(votes) + len(missing)

    if votes:
        for name, vote in votes.items():
            verdict = f" — {vote['verdict']}" if vote.get("verdict") else ""
            lines.append(f"- {name}: {vote['score']}/10{verdict}")
    if missing:
        lines.append(f"- Could not extract a vote: {', '.join(missing)}")
    if vote_summary.get("average_score") is not None:
        lines.append("")
        lines.append(
            f"Average score: {vote_summary['average_score']:.1f}/10 "
            f"(min {vote_summary['min_score']}, max {vote_summary['max_score']}, "
            f"votes: {len(votes)}/{total})"
        )
    else:
        lines.append("No agent provided a recognizable vote — final score unavailable.")
    if total and len(votes) < total:
        lines.append("")
        lines.append(
            f"⚠ DEGRADED: {len(votes)}/{total} votes — part of the council dropped out."
        )
    lines.append("")
    lines.append(
        "⚠ Scores may be correlated (overlapping model families), and the scale is not calibrated — "
        "use it to track opinion drift between rounds, not as proof of idea quality."
    )

    trajectory_rows = (vote_trajectory or {}).get("trajectory", [])
    if trajectory_rows:
        lines.append("")
        lines.append("### Score trajectory R2 → R3")
        lines.append("")
        for row in trajectory_rows:
            r2, r3 = row.get("r2_score"), row.get("r3_score")
            if r2 is None or r3 is None:
                lines.append(f"- {row['agent']}: incomplete data (R2={r2}, R3={r3})")
                continue
            note = ""
            if row.get("moved_toward_mean") is True:
                note = " — toward the R3 mean (possible consensus compression)"
            elif row.get("moved_toward_mean") is False:
                note = " — away from the R3 mean"
            lines.append(f"- {row['agent']}: {r2} → {r3}{note}")

    if meta:
        lines.append("")
        lines.append("## Run metadata")
        lines.append("")
        if meta.get("wall_time_seconds") is not None:
            lines.append(f"- Runtime: {meta['wall_time_seconds']:.1f} sec")
        if meta.get("agent_calls") is not None:
            lines.append(f"- Agent calls: {meta['agent_calls']}")
        if meta.get("agents"):
            names = ", ".join(a["name"] for a in meta["agents"])
            lines.append(f"- Participants: {names}")

    return "\n".join(lines).rstrip() + "\n"


def render_task_verdict_markdown(
    task: str, task_out: dict, meta: Optional[dict] = None
) -> str:
    """task-verdict.md — the main artifact of task mode.

    Honesty: PARTIAL/DEGRADED/ABORT are published with their
    status, a banner, and the FULL list of unresolved critical_flaws. The
    "Council-Reviewed: <status>" banner is the only line visible at a
    glance; the phrase "approved by the council" is not used here for any
    status except APPROVED — and even then the status speaks for itself,
    no separate assurance phrase."""
    status = task_out.get("status", "UNKNOWN")
    lines: List[str] = ["# AgentCouncil — task verdict", ""]

    banner = (
        f"Council-Reviewed: {status}"
        if status == "APPROVED"
        else f"⚠ Council-Reviewed: {status}"
    )
    lines.append(banner)
    lines.append("")

    lines.append("## Task")
    lines.append("")
    lines.append((task or "").strip())
    lines.append("")

    lines.append("## Executor")
    lines.append("")
    executor = task_out.get("executor")
    reason = task_out.get("executor_reason")
    if executor:
        lines.append(f"- Selected: **{executor}** (reason: {reason})")
    else:
        lines.append(f"- No executor selected (reason: {reason})")
    votes = task_out.get("executor_votes") or {}
    if votes:
        lines.append("- Votes: " + ", ".join(f"{n} → {v}" for n, v in votes.items()))
    missing_exec = task_out.get("executor_missing") or []
    if missing_exec:
        lines.append(f"- Vote not recognized: {', '.join(missing_exec)}")
    lines.append("")

    if task_out.get("aborted_reason"):
        lines.append(f"## Status reason: {status}")
        lines.append("")
        lines.append(task_out["aborted_reason"])
        lines.append("")

    reviews_history = task_out.get("reviews") or {}
    if reviews_history:
        lines.append("## Review history")
        lines.append("")
        for i in sorted(reviews_history):
            iteration = reviews_history[i]
            it_reviews = iteration.get("reviews") or {}
            it_missing = iteration.get("missing") or []
            lines.append(f"### Iteration {i}")
            lines.append("")
            for name, r in it_reviews.items():
                if r is None:
                    lines.append(f"- {name}: vote not recognized")
                    continue
                lines.append(f"- {name}: {r['verdict']}")
                for flaw in r.get("critical_flaws", []):
                    lines.append(f"  - critical: {flaw}")
                for edit in r.get("suggested_edits", []):
                    lines.append(f"  - suggested: {edit}")
            if it_missing:
                lines.append(f"- No response/not recognized: {', '.join(it_missing)}")
            lines.append("")

    if status != "APPROVED":
        # The FULL list of unresolved critical_flaws from the
        # last iteration — not a summary, not "part of the notes", the
        # whole thing.
        unresolved: List[str] = []
        if reviews_history:
            last_i = max(reviews_history)
            last_reviews = reviews_history[last_i].get("reviews") or {}
            for name, r in last_reviews.items():
                if r and r.get("verdict") == "REQUIRED_FIXES":
                    unresolved.extend(
                        f"{name}: {flaw}" for flaw in r.get("critical_flaws", [])
                    )
        lines.append("## Unresolved issues")
        lines.append("")
        if unresolved:
            for u in unresolved:
                lines.append(f"- {u}")
        else:
            lines.append("(no structured issues — see the status reason above)")
        lines.append("")

    if task_out.get("intent_only"):
        lines.append(
            "⚠ The final text looks like an announcement of intent rather than completed work "
            "(mechanical heuristic; may be wrong — check manually)."
        )
        lines.append("")

    work_chars = task_out.get("work_chars")
    if work_chars is not None:
        lines.append(f"Final text length: {work_chars} characters.")
        lines.append("")

    if meta:
        lines.append("## Run metadata")
        lines.append("")
        if meta.get("wall_time_seconds") is not None:
            lines.append(f"- Runtime: {meta['wall_time_seconds']:.1f} sec")
        if meta.get("agent_calls") is not None:
            lines.append(f"- Agent calls: {meta['agent_calls']}")
        if meta.get("agents"):
            names = ", ".join(a["name"] for a in meta["agents"])
            lines.append(f"- Participants: {names}")

    return "\n".join(lines).rstrip() + "\n"


# -- console output -----------------------------------------------------
# print* helpers are display-layer code: core calls them.
# main.py re-exports them (tests patch main.print_*).
def print_vote_summary(summary: dict) -> None:
    votes = summary["votes"]
    missing = summary["missing"]
    total = len(votes) + len(missing)

    if votes:
        print("Individual votes:")
        for name, vote in votes.items():
            verdict = f" — {vote['verdict']}" if vote.get("verdict") else ""
            print(f"  {name}: {vote['score']}/10{verdict}")

    if missing:
        print(f"\nCould not extract a vote: {', '.join(missing)}")

    if total and len(votes) < total:
        print(
            f"\n⚠ DEGRADED: {len(votes)}/{total} votes — part of the council dropped out or gave no recognizable vote."
        )

    if summary["average_score"] is not None:
        print(
            f"\nCouncil average score: {summary['average_score']:.1f}/10 "
            f"(min {summary['min_score']}, max {summary['max_score']}, "
            f"votes: {len(votes)}/{total})"
        )
    else:
        print("\nNo agent provided a recognizable vote — final score unavailable.")

    print(
        "\n⚠ Secondary metric: agent scores may be correlated (overlapping model families), "
        "and the scale is not calibrated — use it to track opinion drift, not as proof of idea quality."
    )


def print_vote_trajectory(trajectory: dict) -> None:
    """R2 -> R3 score trajectory — the drift toward the mean is visible
    (anchoring), but it is only a mechanical signal, not a proof of
    contagion."""
    rows = trajectory.get("trajectory", [])
    if not rows:
        return

    print("\nScore trajectory R2 → R3:")
    for row in rows:
        r2 = row.get("r2_score")
        r3 = row.get("r3_score")
        if r2 is None or r3 is None:
            print(f"  {row['agent']}: incomplete data (R2={r2}, R3={r3})")
            continue
        arrow = ""
        if row.get("moved_toward_mean") is True:
            arrow = " (toward the R3 mean)"
        elif row.get("moved_toward_mean") is False:
            arrow = " (away from the R3 mean)"
        print(f"  {row['agent']}: {r2} → {r3}{arrow}")


def print_results(results: Dict[str, AgentResult], show_json: bool = False) -> None:
    for name, result in results.items():
        print(f"\n### {name}\n")

        if result.error:
            print("ERROR:")
            print(result.error)

            if result.output:
                print("\nSTDOUT:")
                print(result.output)
            continue

        print(result.output)

        if show_json:
            parsed = extract_json_block(result.output)
            if parsed is not None:
                print("\n[structured]")
                print(json.dumps(parsed, ensure_ascii=False, indent=2))


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


def report_dropped(
    before: List[CouncilMember], after: List[CouncilMember], round_name: str
) -> None:
    """Prints who dropped out between rounds (error/empty response) — so it is
    visible that the deliberation continues without them rather than stopping
    silently."""
    after_names = {_member_name(m) for m in after}
    dropped = [_member_name(m) for m in before if _member_name(m) not in after_names]

    if dropped:
        print(
            f"\nDropped after {round_name} (error/empty response): {', '.join(dropped)}"
        )

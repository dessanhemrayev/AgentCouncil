"""Council rounds and task mode pipeline.

run_round1/2/3 — independent evaluation, cross-critique, sequential rebuttal;
run_work/run_fix/run_review + run_task_pipeline — executor selection, work,
review loop. Run orchestration — orchestrator.py; aggregation and
verification — aggregation.py / verification.py.
"""

import asyncio
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..cli.render import render_task_verdict_markdown
from ..config.prompts import (
    REVIEW_DRAFT_MAX_CHARS,
    get_evaluation_prompt,
    get_fix_prompt,
    get_review_prompt,
    get_round2_prompt,
    get_round2_prompt_files,
    get_round3_prompt,
    get_round3_prompt_files,
    get_work_prompt,
)
from .aggregation import extract_json_block, ok
from .cli_runner import runner_for
from .models import KIND_CLI, AgentResult, CouncilMember, RunContext
from .session import SessionWriter


def _coerce_member(
    member: CouncilMember | tuple[str, list[str]] | list[str],
) -> CouncilMember:
    """Accept both the new CouncilMember objects and the older tuple-based API."""
    if isinstance(member, CouncilMember):
        return member
    if (
        isinstance(member, (tuple, list))
        and len(member) == 2
        and isinstance(member[0], str)
    ):
        name, command = member
        return CouncilMember(
            id=name,
            name=name,
            kind=KIND_CLI,
            command=list(command) if command is not None else None,
        )
    raise TypeError(f"unsupported council member: {member!r}")


def _normalize_council(council):
    return [_coerce_member(member) for member in council]


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


async def run_member(
    member: CouncilMember | tuple[str, list[str]] | list[str],
    prompt: str,
    timeout: float = 600.0,
    session_dir: Path | None = None,
    on_status: Callable[[str, str], None] | None = None,
) -> AgentResult:
    """Deliver one prompt to one member through its Runner — the single,
    kind-aware delivery seam of the core.

    runner_for(member) picks the delivery (CliRunner for cli members,
    OpenAIResponsesRunner for openai ones), so rounds and task mode never
    branch on the member kind. Tests monkeypatch this symbol to stub agents
    (it replaces the legacy ``(name, command, ...)`` run_agent adapter).
    """
    return await runner_for(_coerce_member(member)).run(
        prompt,
        RunContext(
            session_dir=session_dir,
            timeout=timeout,
            on_status=on_status,
        ),
    )


# Inline context is the primary R2/R3 mode (tested on all known CLIs);
# files are a last resort when even the inline prompt overflows (R3
# easily reaches 70K chars). Higher than cli_runner's arg limits
# because CLIs have a stdin fallback there.
INLINE_CONTEXT_THRESHOLD = 40_000


async def run_round1(
    idea: str,
    council: List[CouncilMember],
    session: SessionWriter,
    evidence_dir: Optional[Path] = None,
    quick_mode: bool = False,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
    round_timeout: float = 1200.0,
) -> Tuple[Dict[str, AgentResult], Dict[str, List[dict]], str]:
    """Round 1 — independent evaluation of the idea. No agent sees others.

    Returns (result_map, clm_inventory, degradation_status) where:
    - result_map: agent results as before
    - clm_inventory: dict of agent_name -> list of CLM claims (parsed from JSON)
    - degradation_status: one of "full", "partial", "degraded"

    quick_mode adds the mandatory needs_full_council question
    to the prompt — printed by the caller (main.py), not parsed separately here:
    it lives in the same JSON block as claims, already accessible via
    result.output/extract_json_block.

    on_agent_status: optional callback (agent_name, status) for live GUI status.
    round_timeout: timeout for a single agent call (sec) — default raised to 1200
    (run_member itself defaults to 600.0), configurable via
    --round-timeout, since Round 1/2/3 easily exceed 1200 sec
    on heavy tasks, especially if the agent actually does web_search
    (see the executor-voting rules: a lost vote due to timeout = "not counted",
    not "none", and easily turns voting into ABORT).
    """
    prompt = get_evaluation_prompt(idea, evidence_dir, quick_mode)
    council = _normalize_council(council)
    results = await asyncio.gather(
        *(
            run_member(
                member,
                prompt,
                timeout=round_timeout,
                session_dir=session.dir,
                on_status=on_agent_status,
            )
            for member in council
        )
    )
    result_map = {result.name: result for result in results}

    clm_inventory: Dict[str, List[dict]] = {}
    parsed_count = 0

    for name, result in result_map.items():
        session.write_agent_result("round1", name, result)

        parsed = extract_json_block(result.output) if ok(result) else None
        if parsed and isinstance(parsed.get("claims"), list):
            clm_inventory[name] = parsed["claims"]
            parsed_count += 1
        else:
            clm_inventory[name] = []

    # Degradation status
    total_agents = len(council)
    if parsed_count == total_agents:
        degradation_status = "full"
    elif parsed_count > 0:
        degradation_status = "partial"
    else:
        degradation_status = "degraded"

    return result_map, clm_inventory, degradation_status


async def run_round2(
    idea: str,
    council: List[CouncilMember],
    round1: Dict[str, AgentResult],
    session: SessionWriter,
    evidence_dir: Optional[Path] = None,
    preset: Optional[str] = None,
    adversarial: bool = False,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
    round_timeout: float = 1200.0,
) -> Dict[str, AgentResult]:
    """Round 2 — Cross-Critique. Each agent sees ALL Round 1 (including
    its own), but not other agents' Round 2. Formed independently and in parallel.

    Context defaults to INLINE (more reliable — see INLINE_CONTEXT_THRESHOLD);
    files are a fallback only if the inline prompt exceeds the threshold.

    round1 may contain failed agents (ok() == False) — their empty
    response must not leak into others' context, and they have nothing
    to defend in Round 2, so they are silently skipped: the investigation
    continues with those who gave a substantive Round 1.

    on_agent_status: optional callback (agent_name, status) for live GUI status.
    round_timeout: see run_round1 — Round 2 carries all Round 1 context
    (potentially tens of thousands of chars), so agents may need more time
    than in Round 1.
    """
    council = _normalize_council(council)
    round1_all = {name: result.output for name, result in round1.items() if ok(result)}
    round1_paths = {name: session.agent_path("round1", name) for name in round1_all}

    tasks = []
    for member in council:
        name = _member_name(member)
        if name not in round1_all:
            continue

        inline_prompt = get_round2_prompt(
            idea, round1_all, round1_all[name], evidence_dir, preset, adversarial
        )

        if len(inline_prompt) <= INLINE_CONTEXT_THRESHOLD:
            prompt = inline_prompt
        else:
            prompt = get_round2_prompt_files(
                session.idea_path, round1_paths, name, evidence_dir, preset, adversarial
            )

        tasks.append(
            run_member(
                member,
                prompt,
                timeout=round_timeout,
                session_dir=session.dir,
                on_status=on_agent_status,
            )
        )

    results = await asyncio.gather(*tasks) if tasks else []
    result_map = {result.name: result for result in results}

    for name, result in result_map.items():
        parsed = extract_json_block(result.output) if ok(result) else None
        session.write_agent_result("round2", name, result, parsed)

    return result_map


async def run_round3(
    idea: str,
    council: List[CouncilMember],
    round1: Dict[str, AgentResult],
    round2: Dict[str, AgentResult],
    session: SessionWriter,
    start_index: int = 0,
    on_move: Optional[Callable[[str], None]] = None,
    evidence_dir: Optional[Path] = None,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
    round_timeout: float = 1200.0,
) -> Dict[str, AgentResult]:
    """Round 3 — Sequential Rebuttal. All n agents take turns
    (A1 -> A2 -> ... -> An): each next agent sees the public history
    of moves already made in this round, so calls are strictly
    sequential (await one by one), not via gather.

    Context defaults to INLINE, as in run_round2; files are a fallback
    when the inline prompt for a specific move exceeds
    INLINE_CONTEXT_THRESHOLD (by Round 3 this is accumulated Round 1 +
    Round 2 + moves already made — grows with each next agent in
    turn order, so the decision is made anew on each move).

    Both the text and file version of a move are written to disk immediately
    after the response (session.write_agent_result inside the loop), not
    as a batch at the end of the round — otherwise the next agent would
    have nothing to read about previous moves (important for file-based fallback).

    start_index shifts who moves first — so that across multiple debate
    cycles the same model doesn't get a permanent first-move advantage
    (see "Agent Order" in idea.md).

    on_move — optional callback, called before each move with the agent
    name (for UI: spinner/status line in main).

    round1/round2 may contain failed agents (ok() == False) — their
    empty response does not leak into the public context of others, and
    they skip their turn (they have nothing to defend without their
    own Round 1/2) — the investigation continues without them, rather
    than stopping entirely.
    """
    council = _normalize_council(council)
    round1_all = {name: result.output for name, result in round1.items() if ok(result)}
    round2_all = {name: result.output for name, result in round2.items() if ok(result)}
    round1_paths = {name: session.agent_path("round1", name) for name in round1_all}
    round2_paths = {name: session.agent_path("round2", name) for name in round2_all}

    order = council[start_index:] + council[:start_index]

    history_text: Dict[str, str] = {}
    history_paths: Dict[str, Path] = {}
    results: Dict[str, AgentResult] = {}

    for member in order:
        name = _member_name(member)
        if name not in round1_all or name not in round2_all:
            continue

        if on_move is not None:
            on_move(name)

        inline_prompt = get_round3_prompt(
            idea,
            round1_all,
            round2_all,
            dict(history_text),
            round1_all[name],
            round2_all[name],
            evidence_dir,
        )

        if len(inline_prompt) <= INLINE_CONTEXT_THRESHOLD:
            prompt = inline_prompt
        else:
            prompt = get_round3_prompt_files(
                session.idea_path,
                round1_paths,
                round2_paths,
                dict(history_paths),
                round1_paths[name],
                round2_paths[name],
                evidence_dir,
            )

        result = await run_member(
            member,
            prompt,
            timeout=round_timeout,
            session_dir=session.dir,
            on_status=on_agent_status,
        )
        results[name] = result

        parsed = extract_json_block(result.output) if ok(result) else None
        written_path = session.write_agent_result("round3", name, result, parsed)

        # Failed moves stay in the public history (files are marked ERROR);
        # keep text AND file versions — the next turn's mode depends on
        # accumulated size, unknown in advance.
        history_text[name] = (
            result.output if ok(result) else f"[нет ответа: {result.error}]"
        )
        history_paths[name] = written_path

    return results


# --- Task mode: executor vote in the R2 JSON ----------------
# executor = "agent-name" | "none" — one field, zero extra calls.
# Majority wins; tie -> first in call order; all none -> ABORT.
# Unrecognized vote = missing (NOT "none" — desync channel).


def _extract_executor_vote(result: AgentResult, roster: List[str]) -> Optional[str]:
    """Valid executor vote from R2 JSON: name from roster (case-insensitive)
    or "none". Everything else (no JSON, number, list, unknown name) = None
    (vote not counted).

    Models don't guarantee case — the prompt schema gives "agent-name" as
    an example without specifying case, and agents write their name however
    they like (e.g., "hermes" instead of canonical "Hermes"). We compare
    case-insensitively but return the CANONICAL name from roster — otherwise
    downstream code (exact comparison with council names) won't find the agent.
    """
    parsed = extract_json_block(result.output) if ok(result) else None
    value = parsed.get("executor") if isinstance(parsed, dict) else None
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.lower() == "none":
        return "none"
    roster_by_lower = {r.lower(): r for r in roster}
    return roster_by_lower.get(name.lower())


def select_executor(
    round2: Dict[str, AgentResult],
    council: List[CouncilMember],
    roster: List[str],
) -> dict:
    """Executor selection by voting in R2.

    council — council in call order (for deterministic tie-breaking);
    roster — valid candidate names (R2 environment).
    Returns dict for executor.json: {winner, reason, votes, missing}.
    """
    votes: Dict[str, str] = {}
    missing: List[str] = []
    for name, result in round2.items():
        vote = _extract_executor_vote(result, roster)
        if vote is None:
            missing.append(name)
        else:
            votes[name] = vote

    if not votes:
        return {
            "winner": None,
            "reason": "all_none",
            "votes": votes,
            "missing": missing,
        }

    tally: Dict[str, int] = {}
    for v in votes.values():
        if v != "none":
            tally[v] = tally.get(v, 0) + 1

    candidates = [n for n in tally if tally[n] > 0]
    if not candidates:
        return {
            "winner": None,
            "reason": "all_none",
            "votes": votes,
            "missing": missing,
        }

    # Strict majority: >50% of ALL voters (including "none", minus missing)
    best = max(tally.values())
    top = [n for n in candidates if tally[n] == best]
    if len(top) == 1 and best > len(votes) / 2:
        return {
            "winner": top[0],
            "reason": "majority",
            "votes": votes,
            "missing": missing,
        }

    # Tie/minority: first candidate in COUNCIL CALL ORDER.
    winner = next(_member_name(m) for m in council if _member_name(m) in candidates)
    return {
        "winner": winner,
        "reason": "tie_first_in_call_order",
        "votes": votes,
        "missing": missing,
    }


# --- Task mode: execution — one call, stdout to file ---------
WORK_MIN_LENGTH = 200
# Heuristic "intent, not text": flags only, does not block.
_INTENT_MARKERS = (
    "next step",
    "next step will",
    "will continue",
    "continue",
    "will write next",
    "plan of work",
    "next step",
    "will write",
    "i will now",
    "i'll now",
    "as promised",
)


def check_work_gate(output: str) -> str:
    """Mechanical gate: <WORK_MIN_LENGTH = 'aborted' (honest abort,
    NO auto-retry — council; "lazy" agent on 2nd call will likely
    give the same lazy output)."""
    return "ok" if len((output or "").strip()) >= WORK_MIN_LENGTH else "aborted"


def looks_like_intent_only(text: str) -> bool:
    """Heuristic 'output is intent, not text': marker + no
    heading and no paragraphs. Only for flagging in task-verdict."""
    t = (text or "").strip()
    if not t:
        return False
    lowered = t.lower()
    if not any(m in lowered for m in _INTENT_MARKERS):
        return False
    has_heading = t.startswith("#")
    has_paragraphs = t.count("\n\n") >= 1
    return not has_heading and not has_paragraphs


async def run_work(
    member: CouncilMember | tuple[str, list[str]],
    task: str,
    round1_digest: Dict[str, str],
    session_dir: Optional[Path] = None,
    timeout: float = 1800.0,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
) -> AgentResult:
    """SINGLE one-shot executor call (tools not opened — one-shot ceiling
    is honest and visible to user). stdout saved by caller (run_task_pipeline -> work.md).
    """
    member = _coerce_member(member)
    prompt = get_work_prompt(task, round1_digest)
    return await run_member(
        member,
        prompt,
        timeout=timeout,
        session_dir=session_dir,
        on_status=on_agent_status,
    )


async def run_fix(
    member: CouncilMember | tuple[str, list[str]],
    task: str,
    previous_draft: str,
    critical_flaws: List[str],
    draft_path: Optional[Path] = None,
    session_dir: Optional[Path] = None,
    timeout: float = 1800.0,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
) -> AgentResult:
    """Fix call — executor gets draft vK + critical_flaws of current
    iteration, one full regeneration (one-shot cannot do diffs).
    """
    member = _coerce_member(member)
    prompt = get_fix_prompt(task, previous_draft, critical_flaws, draft_path=draft_path)
    return await run_member(
        member,
        prompt,
        timeout=timeout,
        session_dir=session_dir,
        on_status=on_agent_status,
    )


# --- Task mode: review — verdict mechanics --------------------
REVIEW_VERDICTS = ("APPROVED", "REQUIRED_FIXES")


def extract_review(result: AgentResult) -> Optional[dict]:
    """Strict parse of reviewer verdict. Unrecognized JSON / bad
    verdict = None (vote not counted — NOT approved by default): new
    mandatory JSON field for 6 heterogeneous CLIs = desync channel,
    best-effort + DEGRADED flag.

    verdict is compared case-insensitively (same reason as
    _extract_executor_vote — prompt schema doesn't guarantee model case),
    but returns CANONICAL value from REVIEW_VERDICTS so that
    downstream comparisons (`verdict == "APPROVED"`) keep working.
    """
    parsed = extract_json_block(result.output) if ok(result) else None
    raw_verdict = parsed.get("verdict") if isinstance(parsed, dict) else None
    verdict_by_lower = {v.lower(): v for v in REVIEW_VERDICTS}
    verdict = (
        verdict_by_lower.get(raw_verdict.lower())
        if isinstance(raw_verdict, str)
        else None
    )
    if verdict is None or not isinstance(parsed, dict):
        return None

    def as_str_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        return []

    return {
        "verdict": verdict,
        "critical_flaws": as_str_list(parsed.get("critical_flaws")),
        "suggested_edits": as_str_list(parsed.get("suggested_edits")),
    }


def count_approvals(results: Dict[str, Optional[dict]], council_size: int) -> bool:
    """Majority APPROVED: strictly more than half of reviewers
    (council_size - 1 — executor doesn't vote). None in values = vote
    not counted, included in denominator (unrecognized didn't vote for,
    but not against either).
    """
    voters = council_size - 1
    approved = sum(
        1 for r in results.values() if r is not None and r["verdict"] == "APPROVED"
    )
    return voters > 0 and approved > voters / 2


async def run_review(
    task: str,
    draft: str,
    executor_name: str,
    round1: Dict[str, AgentResult],
    round2: Dict[str, AgentResult],
    council: List[CouncilMember],
    change_log: Optional[List[str]] = None,
    draft_path: Optional[Path] = None,
    session_dir: Optional[Path] = None,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Optional[dict]]:
    """ALL council agents EXCEPT executor, in parallel (author doesn't
    review themselves — bias). Each gets: task, draft (inline; file path when
    overflowing, R3 mechanics), THEIR OWN R1/R2 (own position, not others' —
    bias), critical change log. Returns name -> parsed review; None = vote
    not counted (NEVER approved).

    on_agent_status: optional callback (agent_name, status) for live GUI status.
    """
    council = _normalize_council(council)
    prepared: List[Tuple[CouncilMember, str]] = []
    for member in council:
        name = _member_name(member)
        if name == executor_name:
            continue
        r1ok = ok(round1.get(name, AgentResult(name=name)))
        r2ok = ok(round2.get(name, AgentResult(name=name)))
        if not r1ok and not r2ok:
            continue  # failed in both R1 and R2 — not a reviewer (nothing to counter)
        your_r1 = round1[name].output if r1ok else "(no Round 1 for this agent)"
        your_r2 = round2[name].output if r2ok else "(no Round 2 for this agent)"
        prompt = get_review_prompt(
            task, draft, your_r1, your_r2, change_log=change_log, draft_path=draft_path
        )
        prepared.append((member, prompt))

    if not prepared:
        return {}

    results = await asyncio.gather(
        *(
            run_member(
                member,
                prompt,
                timeout=600.0,
                session_dir=session_dir,
                on_status=on_agent_status,
            )
            for member, prompt in prepared
        )
    )
    return {
        _member_name(member): extract_review(result)
        for (member, _), result in zip(prepared, results)
    }


# --- Task mode: loop + cap + exit state ------------------------
def _flatten_flaws(review_results: Dict[str, Optional[dict]]) -> List[str]:
    """All critical_flaws of the iteration in a flat list (for change log and fix)."""
    lines: List[str] = []
    for name in sorted(review_results):
        r = review_results[name]
        if r is not None and r["verdict"] == "REQUIRED_FIXES":
            lines.extend(f"- {name}: {flaw}" for flaw in r["critical_flaws"])
    return lines


async def run_task_pipeline(
    task: str,
    council: List[CouncilMember],
    roster: List[str],
    round1: Dict[str, AgentResult],
    round2: Dict[str, AgentResult],
    session: SessionWriter,
    max_review_iterations: int = 2,
    work_timeout: float = 1800.0,
    on_agent_status: Optional[Callable[[str, str], None]] = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> dict:
    """Task pipeline: executor selection -> one call -> review loop with
    cap -> exit state (APPROVED / PARTIAL / DEGRADED / ABORT).

    Returns dict (written to task_verdict.json): {status, executor,
    executor_reason, executor_votes, executor_missing, iterations, reviews:
    {iter: {reviews, missing}}, work_chars, intent_only, aborted_reason}.

    task-verdict.md written here as well (without meta — run metadata
    added by caller re-rendering after collecting wall-time,
    see run_council_async in orchestrator.py).

    on_agent_status: optional callback (agent_name, status) for live GUI status.
    on_stage: optional callback (stage) — phases "task" (executor work),
        "task-review-N" (review iteration N), "task-fix-N" (fix N).
    """
    council = _normalize_council(council)
    max_review_iterations = max(0, int(max_review_iterations))
    work_timeout = max(1.0, float(work_timeout))

    def _finish(
        status: str,
        executor_name: Optional[str],
        executor_info: dict,
        iterations: int,
        reviews_history: Dict[int, dict],
        work_chars: int,
        aborted_reason: Optional[str],
        intent_only: bool = False,
    ) -> dict:
        result = {
            "status": status,
            "executor": executor_name,
            "executor_reason": executor_info.get("reason"),
            "executor_votes": executor_info.get("votes") or {},
            "executor_missing": executor_info.get("missing") or [],
            "iterations": iterations,
            "reviews": reviews_history,
            "work_chars": work_chars,
            "intent_only": intent_only,
            "aborted_reason": aborted_reason,
        }
        session.write_task_verdict_json(result)
        session.write_task_verdict(render_task_verdict_markdown(task, result))
        return result

    executor = select_executor(round2, council, roster)
    session.write_executor(executor)

    if executor["winner"] is None:
        # Honest ABORT: the R1/R2 discussion IS the result; no artifact.
        return _finish(
            "ABORT",
            None,
            executor,
            0,
            {},
            0,
            "no executor chosen (all votes none/invalid) — discussion R1/R2 kept in session",
        )

    exec_name = executor["winner"]
    exec_member = next(m for m in council if _member_name(m) == exec_name)
    round1_digest = {n: r.output for n, r in round1.items() if ok(r)}
    reviews_history: Dict[int, dict] = {}
    change_log: List[str] = []
    draft = ""

    if on_stage is not None:
        on_stage("task")
    work_result = await run_work(
        exec_member,
        task,
        round1_digest,
        session_dir=session.dir,
        timeout=work_timeout,
        on_agent_status=on_agent_status,
    )
    draft = (work_result.output or "").strip()
    session.write_work(draft)
    if work_result.error and not draft:
        return _finish(
            "ABORT",
            exec_name,
            executor,
            0,
            {},
            0,
            f"executor failed before artifact: {work_result.error}",
        )
    if check_work_gate(draft) == "aborted":
        return _finish(
            "DEGRADED",
            exec_name,
            executor,
            0,
            {},
            len(draft),
            f"work output < {WORK_MIN_LENGTH} chars — artifact not published as done (no auto-retry)",
        )

    (session.dir / "work-final.md").write_text(draft, encoding="utf-8")
    status = "PARTIAL"
    aborted_reason: Optional[str] = None
    max_iters = max(1, max_review_iterations)  # 0 = one review without fix

    for i in range(1, max_iters + 1):
        if on_stage is not None:
            on_stage(f"task-review-{i}")
        review_results = await run_review(
            task,
            draft,
            exec_name,
            round1,
            round2,
            council,
            change_log=change_log or None,
            draft_path=session.dir / "work.md"
            if len(draft) > REVIEW_DRAFT_MAX_CHARS
            else None,
            session_dir=session.dir,
            on_agent_status=on_agent_status,
        )
        # who should have reviewed but didn't return a recognized verdict:
        reviewers = [
            _member_name(m)
            for m in council
            if _member_name(m) != exec_name
            and (
                ok(round1.get(_member_name(m), AgentResult(name=_member_name(m))))
                or ok(round2.get(_member_name(m), AgentResult(name=_member_name(m))))
            )
        ]
        missing = [n for n in reviewers if review_results.get(n) is None]
        session.write_task_review(i, review_results, missing)
        reviews_history[i] = {"reviews": review_results, "missing": missing}

        # Denominator = len(reviewers)+1, NOT len(council): an agent dead
        # in both R1 and R2 cannot vote — counting it makes APPROVED
        # unreachable (reproduced bug: sole live reviewer, zero flaws).
        if count_approvals(review_results, council_size=len(reviewers) + 1):
            status = "APPROVED"
            break
        if all(r is None for r in review_results.values()) and review_results:
            status = "DEGRADED"
            aborted_reason = "no parsable review verdicts (majority unparseable)"
            break
        if i == max_iters:
            status = "PARTIAL"
            break

        # loop advances ONLY on REQUIRED_FIXES with non-empty critical_flaws:
        open_flaws = _flatten_flaws(review_results)
        if not open_flaws:
            status = "PARTIAL"
            aborted_reason = (
                "REQUIRED_FIXES without critical_flaws — no mechanical basis to fix"
            )
            break

        if on_stage is not None:
            on_stage(f"task-fix-{i}")
        fix_result = await run_fix(
            exec_member,
            task,
            draft,
            open_flaws,
            draft_path=session.dir / "work.md"
            if len(draft) > REVIEW_DRAFT_MAX_CHARS
            else None,
            session_dir=session.dir,
            timeout=work_timeout,
            on_agent_status=on_agent_status,
        )
        new_draft = (fix_result.output or "").strip()
        session.write_work(new_draft)
        if fix_result.error and not new_draft:
            status = "DEGRADED"
            aborted_reason = f"fix attempt failed: {fix_result.error}"
            break
        if check_work_gate(new_draft) == "aborted":
            status = "DEGRADED"
            aborted_reason = "fix attempt produced empty/short output"
            break

        draft = new_draft
        (session.dir / "work-final.md").write_text(draft, encoding="utf-8")
        change_log = open_flaws

    return _finish(
        status,
        exec_name,
        executor,
        len(reviews_history),
        reviews_history,
        len(draft),
        aborted_reason,
        looks_like_intent_only(draft),
    )

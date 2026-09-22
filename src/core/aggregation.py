"""Round result aggregation.

Pure aggregation of already-received answers — no LLM calls: the claims
survival map, votes, and score trajectory. Plus the shared parsing utils
extract_json_block/ok, used by the rounds, verification, and the
orchestrator.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .models import AgentResult
from .verification import verify_quote_in_evidence


def extract_json_block(text: str) -> Optional[dict]:
    """Tries to pull a JSON structure out of a Round 2/3 answer.

    Agents are arbitrary CLIs and are not required to follow the format
    strictly, so the parser tries hard and does not treat failure as a
    condition for the round to have failed.

    Verbose tool-using CLIs sometimes first illustrate the format with an
    example ```json block and give the real answer as a second one — the
    prompts themselves demand "at the very end you MUST return...", so the
    last block is authoritative. We try blocks from the end; if the last
    one is truncated/invalid (e.g. cut by the CLI's length limit), we roll
    back to an earlier one instead of giving up — the old behavior (only
    the first match) lost the agent's entire vote on a single invalid
    block, even when the next block was excellent JSON."""
    for candidate in reversed(
        re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    ):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def ok(result: AgentResult) -> bool:
    """Is there enough content in the result to pass it on to the next round?"""
    return not result.error and bool(result.output)


# Status normalization from R2 (SUPPORTED/CONTRADICTED/UNRESOLVED) and R3
# (resolved/partially_resolved/unresolved/falsified) into the single CLM
# map vocabulary: RESOLVED/PARTIALLY_RESOLVED/FALSIFIED/UNRESOLVED.
_R2_STATUS_MAP = {
    "SUPPORTED": "RESOLVED",
    "CONTRADICTED": "FALSIFIED",
    "UNRESOLVED": "UNRESOLVED",
}
_R3_STATUS_MAP = {
    "RESOLVED": "RESOLVED",
    "PARTIALLY_RESOLVED": "PARTIALLY_RESOLVED",
    "UNRESOLVED": "UNRESOLVED",
    "FALSIFIED": "FALSIFIED",
}


def _normalize_status(raw: object, status_map: Dict[str, str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    return status_map.get(raw.strip().upper())


def _rollup_claim_status(entry: dict) -> str:
    """One status per map row, from the mix of R2/R3 answers on a single
    CLM-id.

    The mechanical quote check beats what the agent claimed: a false
    POSITIVE (a paraphrase accepted as a verbatim quote) is more dangerous
    than uncertainty."""
    if any(row.get("quote_verified") is False for row in entry["r2"] + entry["r3"]):
        return "POSSIBLE_HALLUCINATION"

    statuses = [row["status"] for row in entry["r3"] if row.get("status")] or [
        row["status"] for row in entry["r2"] if row.get("status")
    ]

    if not statuses:
        # Neither R2 nor R3 weighed in on this CLM — judge by R1: a claimed
        # FACT with no evidence binding is unverified (UNKNOWN), everything
        # else is simply not yet considered (UNRESOLVED).
        if any(
            v.get("status") == "FACT" and not v.get("evidence_ref")
            for v in entry["variants"]
        ):
            return "UNKNOWN"
        return "UNRESOLVED"

    if "FALSIFIED" in statuses:
        return "FALSIFIED"
    if "PARTIALLY_RESOLVED" in statuses:
        return "PARTIALLY_RESOLVED"
    if "RESOLVED" in statuses and "UNRESOLVED" in statuses:
        return "PARTIALLY_RESOLVED"
    if all(s == "RESOLVED" for s in statuses):
        return "RESOLVED"
    return "UNRESOLVED"


def aggregate_claims(
    clm_inventory: Dict[str, List[dict]],
    round2: Dict[str, AgentResult],
    round3: Dict[str, AgentResult],
    evidence_dir: Optional[Path] = None,
) -> dict:
    """The claims survival map: a CLM-id x round matrix, with no extra
    LLM calls — pure aggregation of already-parsed JSON.

    CLM-id is per-agent (agents do not see each other in Round 1):
    "CLM-1" from agent A and from agent B may refer to different claims.
    Python cannot merge them by meaning, so all R1 variants under one bare
    id are collected together as "variants" — this honestly shows the
    discrepancy instead of quietly presenting it as a single claim.
    References from R2/R3 to an id that no agent had in R1 go into
    untracked rather than being silently dropped.
    """
    claims: Dict[str, dict] = {}

    def bucket(claim_id: str) -> dict:
        return claims.setdefault(
            claim_id, {"id": claim_id, "variants": [], "r2": [], "r3": []}
        )

    for agent, agent_claims in clm_inventory.items():
        for claim in agent_claims or []:
            if not isinstance(claim, dict) or not claim.get("id"):
                continue
            bucket(str(claim["id"]))["variants"].append(
                {
                    "agent": agent,
                    "statement": claim.get("statement"),
                    "status": claim.get("status"),
                    "evidence_ref": claim.get("evidence_ref"),
                    "falsification_test": claim.get("falsification_test"),
                }
            )

    untracked_r2: List[dict] = []
    unsupported_assumptions: List[dict] = []

    for agent, result in round2.items():
        parsed = extract_json_block(result.output) if ok(result) else None
        if not parsed:
            continue

        for cs in parsed.get("claim_status") or []:
            if not isinstance(cs, dict) or not cs.get("id"):
                continue

            claim_id = str(cs["id"])
            quote, ref = cs.get("evidence_quote"), cs.get("evidence_ref")
            quote_verified = (
                verify_quote_in_evidence(evidence_dir, quote, ref)[0]
                if quote and ref
                else None
            )

            row = {
                "agent": agent,
                "status": _normalize_status(cs.get("status"), _R2_STATUS_MAP),
                "raw_status": cs.get("status"),
                "reason": cs.get("reason"),
                "evidence_quote": quote,
                "evidence_ref": ref,
                "conflict_category": cs.get("conflict_category") or None,
                "quote_verified": quote_verified,
            }

            if claim_id in claims:
                claims[claim_id]["r2"].append(row)
            else:
                untracked_r2.append({"claim_id": claim_id, **row})

        for ua in parsed.get("unsupported_assumptions") or []:
            if isinstance(ua, dict):
                unsupported_assumptions.append(
                    {
                        "agent": ua.get("agent", agent),
                        "assumption": ua.get("assumption"),
                        "reason": ua.get("reason"),
                        "claim_id": ua.get("claim_id"),
                    }
                )

    untracked_r3: List[dict] = []

    for agent, result in round3.items():
        parsed = extract_json_block(result.output) if ok(result) else None
        if not parsed:
            continue

        for resp in parsed.get("responses_to_claims") or []:
            if not isinstance(resp, dict):
                continue

            claim_id_r3 = resp.get("claim_id")
            quote, ref = resp.get("evidence_quote"), resp.get("evidence_ref")
            quote_verified = (
                verify_quote_in_evidence(evidence_dir, quote, ref)[0]
                if quote and ref
                else None
            )

            row = {
                "agent": agent,
                "claim": resp.get("claim"),
                "status": _normalize_status(resp.get("status"), _R3_STATUS_MAP),
                "raw_status": resp.get("status"),
                "evidence_quote": quote,
                "evidence_ref": ref,
                "quote_verified": quote_verified,
            }

            if claim_id_r3 and str(claim_id_r3) in claims:
                claims[str(claim_id_r3)]["r3"].append(row)
            else:
                untracked_r3.append({"claim_id": claim_id_r3, **row})

    for entry in claims.values():
        entry["statement"] = next(
            (v["statement"] for v in entry["variants"] if v.get("statement")), None
        )
        entry["conflict_category"] = next(
            (r["conflict_category"] for r in entry["r2"] if r.get("conflict_category")),
            None,
        )
        entry["final_status"] = _rollup_claim_status(entry)

    return {
        "claims": list(claims.values()),
        "untracked_r2": untracked_r2,
        "untracked_r3": untracked_r3,
        "unsupported_assumptions": unsupported_assumptions,
    }


def _extract_vote(result: AgentResult) -> Optional[dict]:
    """Extracts a valid final_vote (score 0-10 + verdict) from a single
    round's answer, if it is present there and the score is a real number
    in range."""
    parsed = extract_json_block(result.output) if ok(result) else None
    final_vote = parsed.get("final_vote") if isinstance(parsed, dict) else None
    score = final_vote.get("score") if isinstance(final_vote, dict) else None

    if (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and 0 <= score <= 10
    ):
        return {
            "score": score,
            "verdict": final_vote.get("verdict")
            if isinstance(final_vote, dict)
            else None,
        }
    return None


def aggregate_votes(round3: Dict[str, AgentResult]) -> dict:
    """Council voting: each agent puts a "final_vote" (score 0-10 +
    verdict) in their Round 3 JSON, and Python aggregates them — no new
    LLM call, just aggregation of already-received results (from the agent's
    stdout, not from files — files are what the agent READS as context, not
    what it answers to us)."""
    votes: Dict[str, dict] = {}
    missing: List[str] = []

    for name, result in round3.items():
        vote = _extract_vote(result)
        if vote is not None:
            votes[name] = vote
        else:
            missing.append(name)

    scores = [vote["score"] for vote in votes.values()]

    return {
        "votes": votes,
        "missing": missing,
        "average_score": sum(scores) / len(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
    }


def compute_vote_trajectory(
    round2: Dict[str, AgentResult], round3: Dict[str, AgentResult]
) -> dict:
    """Per-agent R2 -> R3 score trajectory — both points already live in
    the JSON (final_vote was added to R2 alongside R3), so no new LLM call
    is needed. "moved_toward_mean" is a mechanical, not a proven, signal of
    compression toward the R3 consensus: the tool does not close the R3
    consensus drift, it only shows it."""
    r2_scores = {
        name: vote["score"]
        for name, result in round2.items()
        if (vote := _extract_vote(result))
    }
    r3_scores = {
        name: vote["score"]
        for name, result in round3.items()
        if (vote := _extract_vote(result))
    }

    r3_mean = sum(r3_scores.values()) / len(r3_scores) if r3_scores else None

    trajectory = []
    for name in sorted(set(r2_scores) | set(r3_scores)):
        r2_score = r2_scores.get(name)
        r3_score = r3_scores.get(name)
        delta = (
            r3_score - r2_score
            if (r2_score is not None and r3_score is not None)
            else None
        )

        moved_toward_mean = None
        if delta is not None and r3_mean is not None and r2_score != r3_mean:
            moved_toward_mean = abs(r3_score - r3_mean) < abs(r2_score - r3_mean)

        trajectory.append(
            {
                "agent": name,
                "r2_score": r2_score,
                "r3_score": r3_score,
                "delta": delta,
                "moved_toward_mean": moved_toward_mean,
            }
        )

    return {"trajectory": trajectory, "r3_mean": r3_mean}

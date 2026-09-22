"""Quote verification in evidence.

Mechanical check: agents' quotes from Round 2 (claim_status) and Round 3
(responses_to_claims) must appear verbatim in the attached files.
Called from the orchestrator (run_council_async) and from aggregation
(aggregate_claims — quote_verified in the map rows).
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import AgentResult


def normalize_for_quote_check(text: str) -> str:
    """Normalize text for quote verification.

    Normalizations:
    - Collapse whitespace/newlines
    - Unify quote types (smart quotes -> straight quotes)
    - ё -> е
    - CASE SENSITIVE (intentional: a case change means rewriting, not verbatim)
    """
    # Collapse whitespace and newlines
    text = re.sub(r"\s+", " ", text)
    # Unify quote types
    text = (
        text.replace("«", '"')
        .replace("»", '"')
        .replace(""", '"').replace(""", '"')
        .replace("'", "'")
        .replace("'", "'")
    )
    # ё -> е
    text = text.replace("ё", "е").replace("Ё", "Е")
    return text.strip()


def verify_quote_in_evidence(
    evidence_dir: Optional[Path], quote: str, evidence_ref: str
) -> Tuple[bool, Optional[str]]:
    """Verify that a quote exists in the referenced evidence file.

    Returns (found, mismatch_log) where mismatch_log contains details if not found.
    """
    if not evidence_dir or not evidence_dir.exists():
        return False, f"evidence_dir not found: {evidence_dir}"

    evidence_file = evidence_dir / evidence_ref
    if not evidence_file.exists():
        return False, f"evidence file not found: {evidence_ref}"

    try:
        content = evidence_file.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"failed to read evidence file {evidence_ref}: {e}"

    normalized_quote = normalize_for_quote_check(quote)
    normalized_content = normalize_for_quote_check(content)

    if normalized_quote in normalized_content:
        return True, None

    # Find approximate location for logging
    preview = (
        normalized_content[:200] + "..."
        if len(normalized_content) > 200
        else normalized_content
    )
    mismatch_log = (
        f"QUOTE MISMATCH: evidence_ref={evidence_ref}, "
        f"quote='{quote[:100]}{'...' if len(quote) > 100 else ''}', "
        f"evidence_preview='{preview}'"
    )
    return False, mismatch_log


def verify_claims_quotes(
    evidence_dir: Optional[Path],
    round2: Dict[str, AgentResult],
    round3: Dict[str, AgentResult],
) -> List[str]:
    """Verify all evidence_quotes in Round 2 claim_status and Round 3 responses_to_claims.

    Returns list of mismatch log entries.
    """
    from .aggregation import extract_json_block, ok

    mismatches = []

    # Verify Round 2 claim_status quotes
    for name, result in round2.items():
        parsed = extract_json_block(result.output) if ok(result) else None
        if not parsed:
            continue

        claim_status_list = parsed.get("claim_status", [])
        if not isinstance(claim_status_list, list):
            continue

        for claim in claim_status_list:
            if not isinstance(claim, dict):
                continue
            quote = claim.get("evidence_quote")
            evidence_ref = claim.get("evidence_ref")
            claim_id = claim.get("id", "unknown")
            if quote and evidence_ref:
                found, mismatch = verify_quote_in_evidence(
                    evidence_dir, quote, evidence_ref
                )
                if not found and mismatch:
                    mismatches.append(f"[{name} R2 CLM-{claim_id}] {mismatch}")

    # Verify Round 3 responses_to_claims quotes
    for name, result in round3.items():
        parsed = extract_json_block(result.output) if ok(result) else None
        if not parsed:
            continue

        responses = parsed.get("responses_to_claims", [])
        if not isinstance(responses, list):
            continue

        for response in responses:
            if not isinstance(response, dict):
                continue
            quote = response.get("evidence_quote")
            evidence_ref = response.get("evidence_ref")
            claim_id = response.get("claim_id", "unknown")
            if quote and evidence_ref:
                found, mismatch = verify_quote_in_evidence(
                    evidence_dir, quote, evidence_ref
                )
                if not found and mismatch:
                    mismatches.append(f"[{name} R3 CLM-{claim_id}] {mismatch}")

    return mismatches

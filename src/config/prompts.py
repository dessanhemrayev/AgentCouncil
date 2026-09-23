from pathlib import Path
from typing import Dict, List, Optional


# Scenario presets — only the R2 header emphasis varies; the JSON schema
# and the FACT/ASSUMPTION discipline stay the same.
PRESET_EMPHASIS: Dict[str, str] = {
    "hypothesis": (
        "PRESET: hypothesis pre-validation. Actively look for hidden variables that could "
        "explain the claimed result besides the stated cause — that is the point of this review."
    ),
    "paper": (
        "PRESET: paper review simulation. Read this the way a reviewer reads a submission "
        "before deciding accept/reject — what would a reviewer flag?"
    ),
    "lit-review": (
        "PRESET: literature conflict resolution. For every conflict between claims, "
        "categorize its source explicitly — method, sample, or interpretation — and fill "
        '"conflict_category" accordingly.'
    ),
}


def get_preset_emphasis(preset: Optional[str]) -> str:
    return PRESET_EMPHASIS.get(preset, "") if preset else ""


SKILLS_INSTRUCTION = """

## AVAILABLE SKILLS

You have access to the following skills. Use them when appropriate for the task.

### council
You can collaborate with other agents in the council to solve complex sub-tasks.
When you need multiple perspectives on a problem, delegate to other council members.
The council infrastructure handles the coordination — just indicate what you need.

### web_search
Web search is AUTOMATICALLY performed by the council infrastructure before each round.
Search results are provided as evidence files in the session. You do NOT need to call
this skill explicitly — relevant searches are run and attached to your prompt context.
Review the "EVIDENCE FILES" section for web search results.

### docx_report
Use this skill when you need to generate a structured report in .docx format.
The skill creates a professionally formatted Word document with headings, tables,
and references. Files are saved to the session's artifacts folder.
Usage: describe the report content and structure needed.

### file_operations
You can read, write, and manage files directly in the session directory.
Downloaded files, generated reports, and other artifacts are stored in:
  session.dir/artifacts/
All files you create or download will be preserved in the session folder.
"""

# Evaluation-round skills (R1/R2/R3, fix/review) — no docx_report/
# file_operations/artifacts; those belong to task execution only.
EVAL_SKILLS_INSTRUCTION = """

## AVAILABLE SKILLS

### council
You can collaborate with other agents in the council on complex sub-questions.
When you need another perspective, say what you need — the council infrastructure
handles the coordination.

### web_search
Web search is AUTOMATICALLY performed by the council infrastructure before each round.
Search results are provided as evidence files in the session. You do NOT need to call
this skill explicitly — relevant searches are run and attached to your prompt context.
Review the "EVIDENCE FILES" section for web search results.
""".strip()


# experimental --adversarial — a per-agent R2 suffix, opt-in, not part
# of the default protocol (no fixed defender/attacker roles).
ADVERSARIAL_SUFFIX = (
    "ADVERSARIAL MODE (experimental, opt-in): in addition to the usual critique above, "
    "your task is to find at least one fatal flaw in the idea or in another agent's "
    "reasoning. This is a deliberate stress test, not this project's default role — do not "
    "treat it as license to fabricate problems that aren't there."
)


def get_adversarial_suffix(adversarial: bool) -> str:
    return ADVERSARIAL_SUFFIX if adversarial else ""


def get_evaluation_prompt(
    idea: str, evidence_dir: Optional[Path] = None, quick_mode: bool = False
) -> str:
    evidence_section = get_evidence_prompt_addition(evidence_dir)

    # Quick mode — needs_full_council MUST live in the same JSON block
    # as claims, or extract_json_block (first ```json block) will not see it.
    needs_full_council_key = ""
    needs_full_council_note = ""
    if quick_mode:
        needs_full_council_key = (
            ',\n  "needs_full_council": {"answer": "yes|no", "reason": "..."}'
        )
        needs_full_council_note = (
            "\n\nQUICK MODE: this is a fast 2-agent, Round-1-only check, not the full 3-round "
            "council. In the SAME JSON block below you MUST also answer whether this idea "
            'warrants the full council: "needs_full_council": {"answer": "yes"|"no", '
            '"reason": "..."}.'
        )

    return f"""
You are participating in an independent evaluation of an idea. User's idea: "{idea}"
{evidence_section}

Analyze:
1. What exactly is being proposed.
2. What problem it solves.
3. What is strong about the idea.
4. What may be wrong.
5. What alternatives exist.
6. What may already exist.
7. What experiments are needed to validate it.
8. An overall score from 0 to 10.

IMPORTANT:
Do not anchor on the opinions of other agents.
This is an independent evaluation.
{EVAL_SKILLS_INSTRUCTION}

CLM INVENTORY (REQUIRED):
At the very end you MUST return a structured JSON block with your Claim-Level Map inventory.
Each claim must have a unique CLM-id (CLM-1, CLM-2, ...).{needs_full_council_note}

```json
{{
  "claims": [
    {{"id": "CLM-1", "statement": "...", "status": "CLAIMED|FACT|ASSUMPTION", "evidence_ref": "evidence-filename.md|null", "falsification_test": "..."}}
  ]{needs_full_council_key}
}}
```

- statement: the exact claim text (in user's language)
- status: CLAIMED (your own claim), FACT (verifiable from evidence), ASSUMPTION (your assumption)
- evidence_ref: filename from attached evidence, or null if none
- falsification_test: what would falsify this claim (experiment, data, etc.)
""".strip()


def _format_agents_block(agents: Dict[str, str]) -> str:
    return "\n\n".join(f"### {name}\n{text}" for name, text in agents.items())


def _format_file_list(paths: Dict[str, Path]) -> str:
    return "\n".join(f"- {name}: {path}" for name, path in paths.items())


# File-based mode instruction (fallback when the command line overflows).
# Observed failure: the agent declares a plan to read files and stops
# without invoking the read tool — the wording below forbids that.
_READ_FILES_INSTRUCTION = """
Context below is given as FILE PATHS, not inline text, because it is too
large to fit inline. Before doing anything else, actually call your file
tool to read every listed file — do not just describe a plan to read them
and stop. Do not guess or hallucinate their content. Your final answer in
this same response must be the full analysis below, not just a statement
of intent to read the files.
""".strip()


def _format_evidence_files(evidence_paths: Dict[str, Path]) -> str:
    """Format evidence files for inclusion in prompts."""
    if not evidence_paths:
        return "(no evidence files attached)"
    return "\n".join(f"- {name}: {path}" for name, path in evidence_paths.items())


def get_evidence_prompt_addition(evidence_dir: Optional[Path]) -> str:
    """Generate the evidence files section for prompts."""
    if evidence_dir is None or not evidence_dir.exists():
        return ""

    evidence_files = list(evidence_dir.glob("*"))
    if not evidence_files:
        return ""

    evidence_paths = {f.name: f for f in evidence_files}
    evidence_block = _format_evidence_files(evidence_paths)

    return f"""

EVIDENCE FILES (attached to this session — use ONLY these for factual claims):
{evidence_block}

IMPORTANT: All factual claims must be grounded in the attached evidence files.
Claims from "previous work" or "known literature" that cannot be tied to an attached file
must be marked as POSSIBLE_HALLUCINATION. Claims without any evidence must be marked as UNKNOWN.
""".strip()


# Shared R2 body (inline and file-based) — the instructions cannot drift
# between modes. Not an f-string: single curly braces in the JSON schema.
_ROUND2_BODY = """
Perform the following analysis.

1. MAJOR ERRORS

Identify the most important incorrect, weak, or unsupported claims
made by other agents.

For each claim explain:
- what was claimed;
- why it may be wrong or unsupported;
- whether the problem is factual, logical, methodological,
  or caused by an incorrect interpretation of the idea.

2. UNSUPPORTED ASSUMPTIONS

Identify assumptions that agents introduced but which do not
follow from the original idea.

Explicitly distinguish:

FACT
ASSUMPTION
INTERPRETATION
UNKNOWN

3. STRONGEST ARGUMENTS

Identify the strongest arguments made by other agents.

Do not reject an argument merely because it conflicts with your
own Round 1 position.

4. DISAGREEMENTS

Identify the most important disagreements between agents.

For each disagreement explain what causes it.

Do not merely write:
"Agents have different opinions."

Explain which assumptions, interpretations, or evidence
produce the disagreement.

5. SELF-CRITIQUE

Critically examine your own Round 1.

Identify:
- assumptions you introduced;
- claims that were too confident;
- things you may have misunderstood;
- things other agents identified better than you.

6. MISSING INFORMATION

Identify the missing information that has the largest effect
on the evaluation of the idea.

7. CRITICAL CLAIMS

Identify the claims that should be examined in the next debate round.

8. PRELIMINARY REVISION

Explain how your position should change after seeing Round 1
from the other agents.

Do not produce a final verdict.

The goal is to preserve uncertainty where uncertainty remains.

IMPORTANT:

Do not agree with another agent merely because its argument sounds
convincing.

Do not defend your own Round 1 automatically.

Do not invent facts absent from the original idea.

Separate facts, assumptions, interpretations, and unknowns.

CITATION REQUIREMENTS:
- Every claim in claim_status that references evidence MUST include an exact verbatim quote from the evidence file in "evidence_quote" and the filename in "evidence_ref".
- Quotes will be mechanically verified against the attached evidence files — do not paraphrase or summarize.
- If a claim cannot be tied to attached evidence, use status "UNRESOLVED" and leave evidence_quote/evidence_ref empty.
- For unsupported_assumptions, include "claim_id" linking to the CLM-id from Round 1.

At the very end you MUST return a structured result in this exact JSON shape:

```json
{
  "major_errors": [
    {"agent": "AgentName", "claim": "...", "problem": "...", "type": "factual|logical|methodological|interpretation"}
  ],
  "unsupported_assumptions": [
    {"agent": "AgentName", "assumption": "...", "reason": "...", "claim_id": "CLM-1"}
  ],
  "strongest_arguments": [
    {"agent": "AgentName", "argument": "...", "why_strong": "..."}
  ],
  "disagreements": [
    {"topic": "...", "positions": {}, "cause": "..."}
  ],
  "self_corrections": ["..."],
  "missing_information": ["..."],
  "critical_claims": ["..."],
  "revised_position": "...",
  "executor": "agent-name" | "none",
  "claim_status": [
    {"id": "CLM-1", "status": "SUPPORTED|CONTRADICTED|UNRESOLVED", "reason": "...", "evidence_quote": "...", "evidence_ref": "evidence-filename.md", "conflict_category": "method|sample|interpretation|null"}
  ],
  "final_vote": {"score": 0, "verdict": "..."}
}
```

executor field: if the council is given a task to execute, name which agent
should do it — an agent name from the council roster, or "none" to decline
to nominate. Voting for yourself is allowed and is counted. In plain
evaluation mode (no task) this field is ignored.
""".strip()


def get_round2_prompt(
    idea: str,
    round1_all: Dict[str, str],
    your_round1: str,
    evidence_dir: Optional[Path] = None,
    preset: Optional[str] = None,
    adversarial: bool = False,
) -> str:
    """Round 2 — Cross-Critique, INLINE context (default).

    Unlike Round 1, the agent now sees ALL Round 1 results (every council
    agent, including itself), but does not see the other agents' Round 2 —
    R2 prompts are built independently and in parallel.

    This is the main mode — proven and reliable for all known CLIs.
    See get_round2_prompt_files for the fallback variant when the command
    line overflows."""
    round1_block = _format_agents_block(round1_all)
    evidence_section = get_evidence_prompt_addition(evidence_dir)
    preset_block = f"\n{get_preset_emphasis(preset)}\n" if preset else ""
    adversarial_block = (
        f"\n{get_adversarial_suffix(adversarial)}\n" if adversarial else ""
    )

    header = f"""
ROUND 2 — CROSS-CRITIQUE

You are participating in a multi-agent council.

Your task is NOT to decide which agent is the winner.

Your task is to critically examine the independent analyses from Round 1.
{preset_block}{adversarial_block}
{EVAL_SKILLS_INSTRUCTION}

ORIGINAL IDEA: «{idea}»

ROUND 1 — ALL AGENTS:
{round1_block}

YOUR ROUND 1:
{your_round1}
{evidence_section}
""".strip()

    return f"{header}\n\n{_ROUND2_BODY}"


def get_round2_prompt_files(
    idea_path: Path,
    round1_paths: Dict[str, Path],
    your_name: str,
    evidence_dir: Optional[Path] = None,
    preset: Optional[str] = None,
    adversarial: bool = False,
) -> str:
    """Round 2 — Cross-Critique, context via FILE PATHS (fallback).

    Used only when the inline version (get_round2_prompt) does not fit in
    the OS command-line limit — otherwise inline is preferred: not every
    CLI reliably completes the "read files -> analyze" chain in a single
    non-interactive call (see idea.md)."""
    round1_block = _format_file_list(round1_paths)
    your_path = round1_paths[your_name]
    evidence_section = get_evidence_prompt_addition(evidence_dir)
    preset_block = f"\n{get_preset_emphasis(preset)}\n" if preset else ""
    adversarial_block = (
        f"\n{get_adversarial_suffix(adversarial)}\n" if adversarial else ""
    )

    header = f"""
ROUND 2 — CROSS-CRITIQUE

You are participating in a multi-agent council.

Your task is NOT to decide which agent is the winner.

Your task is to critically examine the independent analyses from Round 1.
{preset_block}{adversarial_block}
{_READ_FILES_INSTRUCTION}
{EVAL_SKILLS_INSTRUCTION}

ORIGINAL IDEA: {idea_path}

ROUND 1 — ALL AGENTS (read every file):
{round1_block}

YOUR ROUND 1: {your_path}
{evidence_section}
""".strip()

    return f"{header}\n\n{_ROUND2_BODY}"


# Shared R3 body — same reason as _ROUND2_BODY above.
_ROUND3_BODY = """
Perform the following.

1. ADDRESS CLAIMS

Identify the important claims from previous debate moves
that require a response.

2. DEFEND

Defend claims from your previous position that still survive
the criticism.

3. CONCEDE

Explicitly admit when another agent has demonstrated that
one of your earlier claims was wrong or too strong.

4. CORRECT

Modify your own position where necessary.

5. COUNTER

Challenge claims from other agents that remain unsupported,
incorrect, or based on invalid assumptions.

6. RESOLVE

For each important disputed claim classify its current status:

RESOLVED
PARTIALLY_RESOLVED
UNRESOLVED

7. NEW INFORMATION

State any genuinely new insight that emerged from the debate.

8. UPDATED POSITION

Give your current position after taking the previous debate
into account.

9. FINAL VOTE

This is your independent vote, cast after the full debate.

Give a score from 0 to 10 for the ORIGINAL IDEA itself (not for who
won the debate, not for another agent's performance) and a one-sentence
verdict explaining that score.

This score may differ from your Round 1 score — it should reflect
everything you learned during Round 2 and Round 3.

IMPORTANT:

Your objective is not to win.

Your objective is to keep only claims that survive criticism.

Do not repeat arguments that have already been adequately addressed.

Do not agree merely for the sake of consensus.

Do not preserve your previous position merely because it was yours.

Explicitly acknowledge uncertainty.

Your FINAL VOTE must be your own independent judgment of the idea,
not an attempt to match or average other agents' scores.

CITATION REQUIREMENTS:
- Every response in responses_to_claims that references evidence MUST include an exact verbatim quote from the evidence file in "evidence_quote" and the filename in "evidence_ref".
- Quotes will be mechanically verified against the attached evidence files — do not paraphrase or summarize.
- Include "claim_id" linking to the CLM-id from Round 1/2.
- If a claim cannot be tied to attached evidence, use status "UNRESOLVED" or "FALSIFIED" and leave evidence_quote/evidence_ref empty.

At the very end you MUST return a structured result in this exact JSON shape:

```json
{
  "responses_to_claims": [
    {"claim_id": "CLM-1", "claim": "...", "response": "...", "status": "resolved|partially_resolved|unresolved|falsified", "evidence_quote": "...", "evidence_ref": "evidence-filename.md"}
  ],
  "defended_claims": ["..."],
  "concessions": ["..."],
  "corrections": ["..."],
  "counterarguments": ["..."],
  "new_insights": ["..."],
  "updated_position": "...",
  "final_vote": {"score": 0, "verdict": "..."}
}
```
""".strip()


def get_round3_prompt(
    idea: str,
    round1_all: Dict[str, str],
    round2_all: Dict[str, str],
    round3_history: Dict[str, str],
    your_round1: str,
    your_round2: str,
    evidence_dir: Optional[Path] = None,
) -> str:
    """Round 3 — Sequential Rebuttal, INLINE context (default).

    No more two chosen agents debating: all n agents take turns
    (A1 -> A2 -> ... -> An), and each next agent sees the previous moves in
    this same round (but not future ones). round3_history contains only the
    moves made so far, in turn order.

    The main mode is reliable, but by Round 3 the text of Round 1 + Round 2
    from all agents easily reaches tens of thousands of characters and may
    not fit in the OS command-line limit; then council.py switches to
    get_round3_prompt_files."""
    round1_block = _format_agents_block(round1_all)
    round2_block = _format_agents_block(round2_all)
    round3_block = (
        _format_agents_block(round3_history)
        if round3_history
        else "(no previous moves yet — you are the first to respond in this round)"
    )
    evidence_section = get_evidence_prompt_addition(evidence_dir)

    header = f"""
ROUND 3 — SEQUENTIAL REBUTTAL

You are one participant in a multi-agent council.

The other agents have already produced analyses and,
in this round, some agents have already made rebuttal statements.

Your task is to respond to the strongest unresolved issues
in the current public debate.
{EVAL_SKILLS_INSTRUCTION}

ORIGINAL IDEA: «{idea}»

ROUND 1 — ALL AGENTS:
{round1_block}

ROUND 2 — ALL AGENTS:
{round2_block}

PREVIOUS ROUND 3 MOVES:
{round3_block}

YOUR ROUND 1:
{your_round1}

YOUR ROUND 2:
{your_round2}
{evidence_section}
""".strip()

    return f"{header}\n\n{_ROUND3_BODY}"


def get_round3_prompt_files(
    idea_path: Path,
    round1_paths: Dict[str, Path],
    round2_paths: Dict[str, Path],
    round3_history_paths: Dict[str, Path],
    your_round1_path: Path,
    your_round2_path: Path,
    evidence_dir: Optional[Path] = None,
) -> str:
    """Round 3 — Sequential Rebuttal, context via FILE PATHS (fallback).

    Used only when the inline version (get_round3_prompt) does not fit in
    the OS command-line limit — see get_round2_prompt_files."""
    round1_block = _format_file_list(round1_paths)
    round2_block = _format_file_list(round2_paths)
    round3_block = (
        _format_file_list(round3_history_paths)
        if round3_history_paths
        else "(no previous moves yet — you are the first to respond in this round)"
    )
    evidence_section = get_evidence_prompt_addition(evidence_dir)

    header = f"""
ROUND 3 — SEQUENTIAL REBUTTAL

You are one participant in a multi-agent council.

The other agents have already produced analyses and,
in this round, some agents have already made rebuttal statements.

Your task is to respond to the strongest unresolved issues
in the current public debate.

{_READ_FILES_INSTRUCTION}
{EVAL_SKILLS_INSTRUCTION}

ORIGINAL IDEA: {idea_path}

ROUND 1 — ALL AGENTS (read every file):
{round1_block}

ROUND 2 — ALL AGENTS (read every file):
{round2_block}

PREVIOUS ROUND 3 MOVES (read every file, in order):
{round3_block}

YOUR ROUND 1: {your_round1_path}

YOUR ROUND 2: {your_round2_path}
{evidence_section}
""".strip()

    return f"{header}\n\n{_ROUND3_BODY}"


# --- Task mode: executor and reviewer prompts ----------------
# EN by convention; not f-strings where the JSON schema appears
# (single curly braces) — same pattern as _ROUND2_BODY/_ROUND3_BODY.

WORK_DISCUSSION_MAX = 1_500  # per-agent R1 summary in the executor prompt
REVIEW_DRAFT_MAX_CHARS = 24_000  # draft in the reviewer/fix prompt (inline)
REVIEW_CHANGELOG_MAX_CHARS = 6_000
REVIEW_OWN_R1_MAX = 8_000
REVIEW_OWN_R2_MAX = 6_000


def _truncate(text: str, limit: int) -> str:
    """Mechanical head truncation (not LLM summarization — rejected by memondo)."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated] "


def get_work_prompt(task: str, round1_digest: Dict[str, str]) -> str:
    """Executor prompt. Task + a compressed R1 summary + a strict
    anti-lazy requirement (a documented project failure: "announcing an
    intent and then stopping")."""
    discussion = "\n\n".join(
        f"### {name}\n{_truncate(text, WORK_DISCUSSION_MAX)}"
        for name, text in round1_digest.items()
    )
    return f"""
EXECUTE THE TASK. You are the council's chosen executor.

TASK:
{task}

COUNCIL DISCUSSION (Round 1 summaries — use as context, do not re-debate):
{discussion}

{SKILLS_INSTRUCTION}

ARTIFACTS STORAGE:
When you generate files (reports, documents, data files), save them to:
  artifacts/
All files in this directory are preserved in the session folder.
You can reference other files in the session using relative paths.

OUTPUT REQUIREMENT (hard):
Print the FULL TEXT of the finished work in your response.
NOT a plan, NOT an outline, NOT a promise to write it, NOT "here is what I will do".
Start immediately with the deliverable content. Your stdout is saved verbatim
as the artifact — nothing after the text is needed.
""".strip()


def get_fix_prompt(
    task: str,
    previous_draft: str,
    critical_flaws: List[str],
    draft_path: Optional[Path] = None,
) -> str:
    """Fix prompt. For the executor: draft vK + the list of
    critical_flaws of the current review iteration; one full regeneration
    (one-shot cannot do diffs)."""
    if draft_path is not None:
        draft_section = f"PREVIOUS DRAFT (read this file — it is the full current text):\n{draft_path.as_posix()}"
    else:
        draft_section = f"PREVIOUS DRAFT (full current text):\n{_truncate(previous_draft, REVIEW_DRAFT_MAX_CHARS)}"
    flaws = "\n".join(f"- {f}" for f in critical_flaws[:40])
    return f"""
EXECUTE THE TASK (FIX ITERATION). You are the council's executor.
Reviewers found objective failures in the previous draft.

TASK:
{task}

{draft_section}

CRITICAL FLAWS TO FIX (from the previous review iteration):
{flaws}
{EVAL_SKILLS_INSTRUCTION}

OUTPUT REQUIREMENT (hard):
Print the FULL UPDATED TEXT of the work in your response — the complete
deliverable with all listed flaws fixed. NOT a diff, NOT a plan, NOT
"I will fix it". Do not contradict fixes already made to satisfy previous
reviewers.
""".strip()


def get_review_prompt(
    task: str,
    draft: str,
    your_r1: str,
    your_r2: str,
    change_log: Optional[List[str]] = None,
    draft_path: Optional[Path] = None,
) -> str:
    """Reviewer prompt. Draft (inline or path when overflowing),
    YOUR OWN R1/R2 (your own position, not others' — bias), a change log +
    a consistency requirement (memondo: verify the requested fixes were
    actually implemented and that new requirements do not contradict the
    old ones — anti whack-a-mole)."""
    if draft_path is not None:
        draft_section = f"DRAFT (read this file FIRST — it is the full deliverable):\n{draft_path.as_posix()}"
    else:
        draft_section = (
            f"DRAFT (full text):\n{_truncate(draft, REVIEW_DRAFT_MAX_CHARS)}"
        )

    if change_log:
        log_section = (
            "CHANGES REQUESTED IN THE PREVIOUS REVIEW ITERATION:\n"
            + _truncate("\n".join(change_log[:40]), REVIEW_CHANGELOG_MAX_CHARS)
            + "\nYou MUST verify that these fixes were actually implemented in the draft,\n"
            "and that any new requirement you make does not contradict changes made to satisfy them."
        )
    else:
        log_section = ""

    return f"""
REVIEW THE COUNCIL'S DELIVERABLE.

TASK:
{task}

{draft_section}

YOUR ROUND 1 (your own position):
{_truncate(your_r1, REVIEW_OWN_R1_MAX)}

YOUR ROUND 2 (your own position):
{_truncate(your_r2, REVIEW_OWN_R2_MAX)}
{log_section}
{EVAL_SKILLS_INSTRUCTION}

Be a strict reviewer, not a polite one. "critical_flaws" is for OBJECTIVE
failures: wrong facts, missing required content, internal contradictions,
claims contradicted by the draft itself. "suggested_edits" is for
nice-to-haves. APPROVED means: no objective failures remain. Any new
requirement you raise does not contradict a change already made to satisfy
a previous reviewer (anti whack-a-mole).

At the very end you MUST return exactly this JSON block:
```json
{{
  "verdict": "APPROVED" | "REQUIRED_FIXES",
  "critical_flaws": ["objective failures, empty list if none"],
  "suggested_edits": ["nice-to-haves, empty list if none"]
}}
```
""".strip()

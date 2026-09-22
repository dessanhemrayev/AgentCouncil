# AgentCouncil

A multi-agent "council" for evaluating ideas: several autonomous CLI AI agents (Claude Code, Codex, Gemini CLI, etc.) independently analyze, challenge, and refine an idea, while an external Python orchestrator manages only the protocol and the passing of public context.

Roles are not tied to specific models — any CLI agent with a non-interactive mode can participate. Pure Python (>= 3.13), standard library only, zero runtime dependencies (the GUI adds an opt-in `customtkinter` extra — see [GUI](#gui); a browser dashboard adds an opt-in `fastapi`/`uvicorn` extra — see [Web dashboard](#web-dashboard)).

## Protocol

| Round | Format | What the agent sees |
|---|---|---|
| Round 1 — Independent Analysis | all in parallel | only the original idea |
| Round 2 — Cross-Critique | all in parallel | the idea + all Round 1 answers (but not other agents' Round 2) |
| Round 3 — Sequential Rebuttal | one by one | the idea + Round 1 + Round 2 + Round 3 moves already made |
| Council Vote | aggregation in Python | — (no new LLM call) |

- **Round 1** measures independence of judgment: no agent sees any other agent's answers.
- **Round 2** — finding errors, unsupported assumptions, and disagreements between independent positions: distinguishes FACT / ASSUMPTION / INTERPRETATION / UNKNOWN and includes self-critique.
- **Round 3** — sequential debate: each next agent sees the moves of the previous agents in the same round (but not future ones); the order of moves rotates between runs.
- **Voting**: in the Round 3 JSON response, each agent returns `final_vote` (score 0–10 + verdict); Python only extracts the ready-made numbers and computes average/min/max — no additional LLM call.

## Running

Requires Python 3.13+ and installed CLI agents (in PATH).

```sh
python main.py
```

With no arguments and a TTY, the program discovers known agents in the system, lets you add your own manually, lets you pick the council members, asks for the idea — and runs the full cycle. Progress is shown with a live spinner (`⠹ ROUND 3 — move: Claude Code (47s)`), results are printed per round, and a vote summary is shown at the end.

### Non-interactive CLI

```sh
python main.py "Your idea here"
python main.py --idea @idea.md --evidence paper1.md paper2.md
python main.py --quick --agents claude,gemini "Quick hypothesis test"
python main.py "Which study is right?" --preset lit-review --evidence a.md b.md c.md
python main.py --full --agents claude,codex,gemini "Full council evaluation"
python main.py --list-agents
```

- `--evidence FILE...` attaches evidence files to the session (copied into `sessions/run-NNN/evidence/`, any format including PDF/DOCX/DOC — copied as-is, agents read them with their own file tools); every round's prompt then requires claims to be grounded in them or marked `POSSIBLE_HALLUCINATION`/`UNKNOWN`, and every `evidence_quote` an agent cites is mechanically checked against the file text (see [Claim map](#claim-map)). The mechanical quote check reads the file as UTF-8 text, so it only verifies plain-text/Markdown evidence; a quote from a PDF/DOCX file degrades honestly to an unverified mismatch rather than crashing the run. `--evidence` also accepts URLs directly — they're fetched and saved into `evidence/` the same way (the CLI/GUI/web dashboard all share this).
- `council.json`'s `web_search` (`{"enabled": false, "max_results": 5, "timeout": 30}`) — **disabled by default**. When turned on, a DuckDuckGo search (stdlib `urllib`, HTML-scraped — no API key, but fragile to DDG markup changes) runs automatically before each round, seeded from the idea/prior-round text, and the results are saved as `evidence/web_search_results.md`. Since this sends the idea text to an external service on every round, opt in deliberately rather than flipping it on globally.
- `--quick` runs Round 1 only, capped at 2 agents, and each agent must additionally answer `needs_full_council: yes/no` — a fast signal on whether the full 3-round council is worth running.
- `--full` (the default once an idea is given) runs all 3 rounds. Note the cost: full is 3 rounds × N agents (with API members — 3 billed calls each), while `--quick` is 2 agents × 1 call; use `--quick` as the cheap recon and `--full` when the verdict is worth it.
- `--preset hypothesis|paper|lit-review` shifts Round 2's emphasis (hidden variables / pre-submission review / method-sample-interpretation conflict categorization) without changing the JSON protocol.
- `--agents claude,gemini` restricts the council to named agents; `--no-open` skips auto-opening `verdict.md`; `--output-dir DIR` overrides the session directory; `--config FILE` points at a `council.json` (default agents/mode/evidence folder, and where the Round 3 first-move rotation counter lives — see below).
- `--adversarial` is an **experimental**, opt-in flag: it adds a per-agent "find at least one fatal flaw" suffix to Round 2. It is not the project's default role (there are deliberately no fixed defender/attacker roles) — use it only for A/B comparison against a normal run on the same idea.

Between runs, Round 3's first mover rotates (persisted as `round3_rotation` in `council.json`) so the same agent doesn't always get the advantage of moving first.

### API models as council members

Council members are not tied to CLI agents: `council.json` may declare API members (`type: "openai"`) that join the same R1/R2/R3 protocol next to CLI agents (billed per call, same as CLI agents — `--quick` stays 2 agents × 1 call):

```json
{
  "members": [
    {
      "id": "gpt-5-api",
      "name": "GPT-5 API",
      "type": "openai",
      "model": "gpt-5",
      "api_key_env": "OPENAI_API_KEY",
      "default": true,
      "aliases": ["gpt5"]
    }
  ]
}
```

- `model` is required; `base_url` is optional (default `https://api.openai.com`); `api_key_env` names the environment variable holding the Bearer key (default `OPENAI_API_KEY`) — the key itself never goes into `council.json`. Delivery is stdlib-only (`urllib`, POST `{base_url}/v1/responses`, one retry on 429/5xx) — no `openai` package, so the zero-runtime-dependency invariant stays intact.
- A missing key is not a crash: the member fails with a `NO KEY` error and the round reports it as dropped, exactly like a failed CLI agent. `--list-agents` shows API members with their model and the live key status (`KEY OK` / `NO KEY`) — check it before paying for a run.
- `--agents` matches id, name, or alias (case-insensitive); without `--agents` the default council is the configured `default: true` members plus autodiscovered known CLIs (order: `default_members` when set).
- The HTTP call runs in a worker thread and emits the same `running`/`done`/`error`/`timeout` status events as CLI agents, so the CLI spinner and the GUI "Agent × Stage" matrix work unchanged.

### GUI

```sh
python main.py --gui
```

A single tkinter window (tabs: Идея / Evidence / Запуск / Результаты, plus a log pane) — the same council engine as the CLI, just another consumer of it; both produce an identical `verdict.md`. The GUI is the only part with a pip dependency: `customtkinter` is an **opt-in extra** (`uv sync --extra gui`) — the core and the CLI stay stdlib-only with zero runtime dependencies, and without the extra the CLI works and `--gui` falls back to a clean install-hint. tkinter itself is part of the standard library, but on Windows not every Python install bundles Tcl/Tk (notably some `uv`-managed interpreters) — `--gui` detects this, and if the current interpreter lacks tkinter it looks for one that has it (via the `py` launcher) and relaunches itself there automatically. If none is found, it prints install guidance and falls back to the CLI instead of crashing.

### Web dashboard

```sh
uv sync --extra web
uv run python -m src.web.main
# → http://127.0.0.1:8080
```

A browser dashboard, backed by a small FastAPI JSON API (`src/web/`) — same council engine again, a third consumer of `run_council_async`. It lets you start a run from the browser (idea, agent checkboxes, preset, task mode, evidence — either pasted URLs or real file uploads), watch it live (an "agent × stage" status grid, a progress bar, and a streaming log — all pushed over a WebSocket), and browse past sessions (verdict, per-round answers, claim map, vote) from the sidebar.

Requires the `web` extra (`fastapi`, `uvicorn[standard]`, `python-multipart`) — note the `[standard]`: plain `uvicorn` doesn't bundle a WebSocket implementation, so without it `/ws/...` 404s and the live view never updates. A few things worth knowing:

- Only one run at a time — starting a second run while one is in progress returns `409`; this keeps the live status grid from mixing updates from two runs.
- Like the CLI, `council.json`/`sessions/` resolve relative to the process's working directory — run it from the repo root, same as `python main.py`.
- This project isn't set up as an installable package (no `[build-system]` in `pyproject.toml` — deliberate, keeps it a stdlib-only, run-in-place tool per the top of this README), so there's no `agentcouncil`/`agentcouncil-web` console command — always invoke it as `python -m src.web.main` (or `python main.py [--gui]` for the other two).
- It's a local dev dashboard (binds `127.0.0.1` only) with no authentication — don't expose it beyond localhost as-is.

## Task mode

Task mode turns the council from an idea-evaluator into an executor-plus-reviewers pipeline. After Round 1 and Round 2 run exactly as above, the council **votes on who should do the work** (a new `executor` field in the Round 2 JSON — an agent name from the roster, or `"none"`) instead of proceeding to Round 3. The chosen agent gets one one-shot call to produce the full deliverable; everyone else on the council (never the author) reviews it against a strict binary schema (`APPROVED` / `REQUIRED_FIXES` + `critical_flaws`); if a majority hasn't approved and there are concrete `critical_flaws` to act on, the executor gets one full rewrite per remaining iteration, up to a configurable cap.

```sh
python main.py --task "Write a 5-point onboarding checklist for new hires"
python main.py --task @task.md --max-reviews 3 --work-timeout 900
```

- `--task TEXT|@file` switches to task mode; it is mutually exclusive with a positional idea/`--idea`, and it overrides `--quick` (task mode always runs the full R1+R2 first). Requires at least 2 agents (one executor, one reviewer).
- `--max-reviews N` (default `2`, or `council.json`'s `max_review_iterations`) — `0`/`1` means a single review pass with no rewrite; `2`+ allows `review → fix → review` cycles up to the cap.
- `--work-timeout SEC` (default `1800`, or `council.json`'s `work_timeout`) — timeout for each executor call (initial work and every fix).
- No auto-retry on a lazy or failed executor response — a short/empty answer is reported honestly (`DEGRADED`/`ABORT`) rather than silently retried, since a one-shot CLI that stalls once is likely to stall the same way again.

The GUI has the same pipeline behind a "Режим задания" checkbox on the Запуск tab (with the same two parameters) — it calls the identical `run_council_async(task_mode=True, ...)` as the CLI, so both produce the same `task_verdict.json`/`task-verdict.md`.

**Exit states** — Python only ever aggregates JSON fields, counts, and lengths; it never judges content, so an unrecognized vote is never counted as an approval by default:

| Status | Meaning |
|---|---|
| `APPROVED` | A strict majority of reviewers (excluding the executor) returned `APPROVED` within the iteration cap. |
| `PARTIAL` | The cap was reached without a majority, or reviewers asked for `REQUIRED_FIXES` with no `critical_flaws` to mechanically act on (the loop can't move on its own). |
| `DEGRADED` | The executor's output was too short (< 200 chars), a fix attempt came back empty/short, or every reviewer's JSON was unparseable. |
| `ABORT` | Every Round 2 vote was `"none"`/unrecognized (no executor chosen), or the executor call failed before producing any output. |

`PARTIAL`/`DEGRADED`/`ABORT` are always published with a `Council-Reviewed: <STATUS>` banner and the full list of unresolved `critical_flaws` in `task-verdict.md` — the tool never reports work as council-approved while open objections remain.

Task-mode sessions add these files alongside the usual `idea.md`/`round1/`/`round2/`:

```
sessions/run-NNN/
  executor.json                 # votes, winner, reason (majority/tie/all_none), missing voters
  work.md                       # latest raw executor stdout (overwritten each fix iteration)
  work-final.md                 # last draft that passed the length gate (absent if DEGRADED/ABORT)
  review-1.json, review-2.json  # per-iteration verdicts/critical_flaws/suggested_edits + unparsed voters
  task_verdict.json             # machine-readable summary (status, executor, iterations, reviews)
  task-verdict.md               # main artifact: banner, executor, review history, unresolved flaws
```

## Example

A run with three agents — Claude Code, Gemini CLI, and Codex — evaluating this idea:

> A CLI tool that runs the same prompt through several installed AI coding agents,
> makes them critique each other's answers, and aggregates their votes into a
> single score.

**Round 1 — Independent Analysis.** Each agent receives *only* the idea and answers in parallel. Claude Code scores it 8/10 ("the protocol is cheap to implement and exposes model blind spots"), Gemini scores it 6/10 ("token costs may outweigh the benefits"), and Codex times out.

**Round 2 — Cross-Critique.** Claude and Gemini now see *all* Round 1 answers (including their own). Each returns prose analysis followed by a structured JSON block — for example, Claude's (abridged):

```json
{
  "major_errors": [
    {"agent": "Gemini CLI",
     "claim": "token costs outweigh the benefits",
     "problem": "cost is asserted, never estimated",
     "type": "logical"}
  ],
  "unsupported_assumptions": [
    {"agent": "Gemini CLI",
     "assumption": "a council run costs more than one direct query",
     "reason": "no cost model was given"}
  ],
  "self_corrections": [
    "I assumed every CLI agent exposes a non-interactive flag"
  ],
  "revised_position": "The idea stands, but cost must be measured per run."
}
```

**Round 3 — Sequential Rebuttal.** One by one, each agent sees the idea, all Round 1/2 answers, and the Round 3 moves already made (the move order rotates between runs). Claude concedes nothing on protocol but accepts the cost point; Gemini defends its position with numbers and ends its JSON with an independent vote:

```json
{
  "concessions": [
    "Claude is right that parallel rounds hide blind spots a single query cannot."
  ],
  "updated_position": "Worth building as a personal decision-support tool.",
  "final_vote": {"score": 6.5, "verdict": "Useful once cost per run is measured."}
}
```

**Council Vote.** Python parses the `final_vote` fields from both JSON blocks — no extra LLM call (console strings are localized in Russian; translated and abridged here):

```text
================================================================================
FINAL — Council Vote
================================================================================
Individual votes:
  Claude Code: 7/10 — solid protocol, needs cost measurement
  Gemini CLI: 6.5/10 — useful once cost per run is measured

Could not extract a vote: Codex

Council average: 6.75/10 (min 6.5, max 7, votes: 2/3)
```

Codex's failed Round 1 did not stop the council: it was reported as dropped and simply took no further part. The full transcript lands in `sessions/run-001/`.

## Claim map

The vote score above is a secondary metric — the main verdict is a table of claims (CLM-ids) and how they survived across rounds:

```text
CLM-1 [RESOLVED]: X reduces latency by roughly half
    R2 Gemini CLI: SUPPORTED
    R3 Claude Code: resolved
CLM-2 [POSSIBLE_HALLUCINATION] [конфликт: interpretation]: prior work Y already showed this
    R2 Claude Code: CONTRADICTED ⚠ ЦИТАТА НЕ НАЙДЕНА В EVIDENCE
```

Each agent proposes its own CLM inventory in Round 1 (`claims: [{id, statement, status, evidence_ref, falsification_test}]`); Round 2/3 attach `claim_status` / `responses_to_claims` back to those ids, and Python (`aggregate_claims` in `src/core/aggregation.py`) rolls that up into one status per claim — `RESOLVED` / `PARTIALLY_RESOLVED` / `FALSIFIED` / `UNRESOLVED` / `UNKNOWN` (a claimed fact with no evidence) / `POSSIBLE_HALLUCINATION`. No LLM call is involved: it's pure aggregation, plus one mechanical check — every `evidence_quote` an agent cites is verified against the attached evidence file's actual text, and a quote that doesn't verify overrides whatever status the agent claimed, since a false "verified" citation is more dangerous than an honest "unresolved". Claims referenced by an id no one declared in Round 1 show up as `untracked` rather than being silently dropped (agents number claims independently in Round 1, before they see each other, so the same id from two agents isn't guaranteed to mean the same claim).

## Sessions

Every run is saved to disk as results arrive (`sessions/run-001`, `run-002`, ...):

```
sessions/run-001/
  idea.md
  evidence/                    # --evidence files, copied in as attached
  round1/claude-code.md
  round2/claude-code.md
  round2/claude-code.json      # structured part, if it parsed
  round3/claude-code.md
  vote.json                    # voting results (secondary metric)
  claims.json                  # CLM x round matrix (aggregate_claims)
  citation_mismatches.json     # quotes that failed mechanical verification
  meta.json                    # wall time, agent call count, participants
  verdict.md                   # main artifact: claim-survival map + vote
```

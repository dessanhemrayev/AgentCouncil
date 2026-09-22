import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from src.config import check_agent_available, discover_agents, load_config
from src.config.agents import (
    build_roster,
    member_available,
    member_from_agent,
    select_members,
)
from src.core.models import KIND_OPENAI
from src.core.orchestrator import run_council_async
from src.core.session import SessionWriter
from src.core.web_utils import fetch_url, is_url, save_fetched_evidence


Agent = Tuple[str, List[str]]


# ===== Non-interactive CLI =====


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for non-interactive mode."""
    parser = argparse.ArgumentParser(
        prog="council",
        description="AgentCouncil — multi-agent deliberation for idea evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  council "Your idea here"
  council --idea @idea.md --evidence evidence1.md evidence2.md
  council --quick --agents claude,gemini "Quick hypothesis test"
  council "Abstract of the paper" --preset paper --evidence paper.md
  council --full --agents claude,codex,gemini "Full council evaluation"
  council --gui

Note: when combining a positional idea with --evidence (which takes multiple
files), put the idea FIRST — argparse's --evidence FILE... greedily consumes
every following token, so "--evidence a.md b.md \"idea text\"" swallows the
idea into the evidence list. --idea @file.md has no such issue.
        """.strip(),
    )

    idea_group = parser.add_mutually_exclusive_group()
    idea_group.add_argument(
        "idea",
        nargs="?",
        help="The idea to evaluate (positional argument; put it BEFORE --evidence, see notes below)",
    )
    idea_group.add_argument(
        "--idea",
        dest="idea_file",
        metavar="FILE",
        help="Read idea from file (prefix with @ for file path)",
    )
    idea_group.add_argument(
        "--task",
        dest="task",
        metavar="TEXT_OR_FILE",
        help="Task mode: task text, or @file.md — after R1/R2, select an executor, run the work, and review it",
    )

    # Evidence files. nargs="+" is greedy: a positional idea placed AFTER
    # this flag gets swallowed into the evidence list (see epilog).
    parser.add_argument(
        "--evidence",
        nargs="+",
        metavar="FILE",
        help="Evidence files to attach (copied to session, any format incl. PDF/DOCX/DOC); put a positional idea BEFORE this flag, not after",
    )

    parser.add_argument(
        "--agents",
        metavar="LIST",
        help="Comma-separated list of agent names to use (e.g., claude,gemini)",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="List discovered agents and exit",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: Round 1 only, 2 agents (default: full 3 rounds)",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Full council: all 3 rounds with all available agents (default)",
    )

    parser.add_argument(
        "--preset",
        choices=["hypothesis", "paper", "lit-review"],
        help="Scenario preset: hypothesis (pre-validation), paper (review), lit-review (conflict resolution)",
    )

    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Experimental: add a per-agent 'find at least one fatal flaw' suffix to Round 2 (opt-in, not the default role symmetry)",
    )

    parser.add_argument(
        "--max-reviews",
        type=int,
        metavar="N",
        dest="max_reviews",
        help="Task mode: max review iterations (default: council.json max_review_iterations or 2; 0 = review only, no fixes)",
    )
    parser.add_argument(
        "--work-timeout",
        type=int,
        metavar="SEC",
        dest="work_timeout",
        help="Task mode: executor call timeout in seconds (default: council.json work_timeout or 1800)",
    )
    parser.add_argument(
        "--round-timeout",
        type=int,
        metavar="SEC",
        dest="round_timeout",
        help="Round 1/2/3: agent call timeout in seconds (default: council.json round_timeout or 1200). "
        "Round 2/3 carry the accumulated context of previous rounds and may need more time.",
    )

    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't auto-open verdict.md after completion",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help="Override session output directory",
    )

    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch GUI interface (tkinter)",
    )

    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to council.json config file",
    )

    # Use parse_known_args to ignore unknown args (e.g., pytest -v)
    return parser.parse_known_args()[0]


def read_idea_from_source(args: argparse.Namespace) -> str:
    """Read the idea from either positional arg, --idea flag, or stdin."""
    if args.idea:
        return args.idea
    if args.idea_file:
        path = args.idea_file
        if path.startswith("@"):
            path = path[1:]
        return Path(path).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def run_noninteractive(args: argparse.Namespace, config: dict) -> int:
    """Run the council in non-interactive mode."""
    found = discover_agents()

    if args.list_agents:
        roster = build_roster(config, found)
        if roster:
            print("Council roster (council.json members + discovered CLI agents):")
            for member in roster:
                if member.kind == KIND_OPENAI:
                    key = "KEY OK" if os.environ.get(member.api_key_env) else "NO KEY"
                    print(f"  - {member.name} [openai] model={member.model}, {key}")
                else:
                    state = "available" if member_available(member) else "not on PATH"
                    print(
                        f"  - {member.name} [cli] ({' '.join(member.command or [])})"
                        f" — {state}"
                    )
        else:
            print("No agents discovered or configured.")
        return 0

    # --task carries the task; --idea/@file is optional extra context.
    task_mode = args.task is not None
    if task_mode:
        if args.idea_file:
            idea = read_idea_from_source(args)
        else:
            raw = args.task
            if raw.startswith("@"):
                idea = Path(raw[1:]).read_text(encoding="utf-8")
            else:
                idea = raw
        if not idea:
            print("Error: empty task (--task requires text or @file).")
            return 1
    else:
        idea = read_idea_from_source(args)
        if not idea and not args.gui:
            print(
                "Error: no idea provided. Use positional argument, --idea @file, or pipe to stdin."
            )
            return 1

    roster = build_roster(config, found)
    if not roster:
        print(
            "Error: no agents discovered or configured. Make sure CLI agents"
            " (claude, codex, gemini, hermes, pi, dsh) are installed and in PATH,"
            " or add members to council.json."
        )
        return 1

    council = select_members(roster, args.agents, config)

    if not council:
        print("Error: no agents selected.")
        return 1

    if task_mode and len(council) < 2:
        print("Error: task mode requires at least 2 agents (executor + 1 reviewer).")
        return 1

    quick_mode = args.quick or config.get("mode") == "quick"
    full_mode = args.full or config.get("mode") == "full"
    if task_mode:
        quick_mode = False  # task mode = full R1+R2 run + pipeline

    if not quick_mode and not full_mode:
        # Default is FULL (README: "--full (the default once an idea is given)"):
        # a one-shot idea run is what the docs promise, not a 2-agent probe.
        # --quick (or council.json mode=quick) remains the cheap recon option.
        full_mode = True

    # Quick mode caps the council at 2 agents (9 calls -> 2) — that's the
    # whole point of "quick". --full (or council.json mode=full) overrides.
    if quick_mode and len(council) > 2:
        dropped = [member.name for member in council[2:]]
        council = council[:2]
        print(
            f"Quick mode: используются только 2 агента ({', '.join(m.name for m in council)}); пропущены: {', '.join(dropped)} (для всех агентов — --full)."
        )

    print(f"Council participants: {', '.join(m.name for m in council)}")

    output_dir = Path(args.output_dir) if args.output_dir else None
    session = SessionWriter(output_dir)
    session.write_idea(idea)
    print(f"Session saved to: {session.dir}")

    evidence_dir: Optional[Path] = None
    if args.evidence:
        evidence_dir = session.dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for ev_item in args.evidence:
            if is_url(ev_item):
                # Fetch URL and save as evidence
                print(f"  Fetching evidence from URL: {ev_item}")
                try:
                    content, content_type = fetch_url(ev_item)
                    saved_path = save_fetched_evidence(
                        evidence_dir, ev_item, content, content_type
                    )
                    print(f"  Evidence: {ev_item} -> {saved_path.name}")
                except Exception as exc:
                    print(f"  Warning: failed to fetch {ev_item}: {exc}")
            else:
                src = Path(ev_item)
                if src.exists():
                    dst = evidence_dir / src.name
                    dst.write_bytes(src.read_bytes())
                    print(f"  Evidence: {src.name} -> {dst}")
                else:
                    print(f"  Warning: evidence file not found: {ev_item}")

    max_reviews = (
        args.max_reviews
        if args.max_reviews is not None
        else config.get("max_review_iterations")
    )
    max_reviews = 2 if max_reviews is None else max(0, int(max_reviews))
    work_timeout = (
        args.work_timeout
        if args.work_timeout is not None
        else config.get("work_timeout")
    )
    work_timeout = 1800 if work_timeout is None else max(1, int(work_timeout))
    round_timeout = (
        args.round_timeout
        if args.round_timeout is not None
        else config.get("round_timeout")
    )
    round_timeout = 1200 if round_timeout is None else max(1, int(round_timeout))

    evidence_dir = (
        session.dir / "evidence" if (session.dir / "evidence").exists() else None
    )
    asyncio.run(
        run_council_async(
            idea,
            council,
            session,
            quick_mode,
            args.preset,
            args.no_open,
            evidence_dir,
            args.config,
            args.adversarial,
            task_mode=task_mode,
            max_reviews=max_reviews,
            work_timeout=work_timeout,
            round_timeout=round_timeout,
        )
    )
    return 0


def ask_agents_manually() -> List[Agent]:
    """Lets the user manually add agents the program did not discover on its own."""
    print("Add an agent manually")
    print("For each one — a name and the command used to run it (e.g.: claude).")
    print("Empty name — finish input.\n")

    agents: List[Agent] = []

    while True:
        name = input(f"Agent #{len(agents) + 1} (name, Enter — finish): ").strip()
        if not name:
            break

        command_str = input(f"  Command to run '{name}': ").strip()
        if not command_str:
            print("  No command given, agent skipped.\n")
            continue

        command = command_str.split()

        if not check_agent_available(command):
            print(f"  Warning: command '{command[0]}' was not found in PATH.")
            confirm = input("  Add the agent anyway? (y/N): ").strip().lower()
            if confirm != "y":
                print("  Agent skipped.\n")
                continue

        agents.append((name, command))
        print(f"  Agent '{name}' added.\n")

    return agents


def choose_agents(candidates: List[Agent]) -> List[Agent]:
    """Lets the user pick which of the discovered agents take part in this council."""
    if not candidates:
        return []

    print("Which agents should take part in the council?")
    for i, (name, command) in enumerate(candidates, start=1):
        print(f"  {i}. {name} ({' '.join(command)})")

    selection = input(
        "\nEnter numbers separated by commas (e.g.: 1,3) or Enter — use all: "
    ).strip()

    if not selection:
        return candidates

    chosen = []
    for part in selection.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if 0 <= idx < len(candidates):
            chosen.append(candidates[idx])

    return chosen or candidates


def explicit_non_interactive(args: argparse.Namespace) -> bool:
    """Non-interactive: explicit CLI args that indicate scripted usage."""
    return any(
        [
            args.idea is not None,
            args.idea_file is not None,
            args.task is not None,
            args.evidence is not None,
            args.agents is not None,
            args.list_agents,
            args.quick,
            args.full,
            args.preset is not None,
            args.config is not None,
            args.no_open,
            args.output_dir is not None,
            args.gui,
        ]
    )


async def main() -> None:
    """Interactive council flow (original behavior).

    Non-interactive runs never reach this coroutine: the dispatch happens
    in cli_main() below, BEFORE any event loop starts — run_noninteractive()
    calls asyncio.run() internally, and nesting that inside an already-running
    loop (this coroutine, itself started via asyncio.run(main()))
    raises "asyncio.run() cannot be called from a running event loop".

    Kept named `main` (not `cli_main`) for backward compatibility —
    tests/test_main_flow.py does `from main import main as main_coro`."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print("Scanning the system for known AI agents...")
    print("-" * 40)

    found = discover_agents()

    if found:
        print("Agents found:")
        for name, command in found:
            print(f"  - {name} ({' '.join(command)})")
    else:
        print("No agents discovered automatically.")

    print()
    add_manual = input("Add another agent manually? (y/N): ").strip().lower()
    manual = ask_agents_manually() if add_manual == "y" else []

    candidates = found + manual

    if not candidates:
        print("не найдено и не добавлено ни одного агента")
        print(
            "Make sure the agent CLI (claude, codex, gemini, ...) is installed and available in PATH."
        )
        return

    print("-" * 40)
    council = choose_agents(candidates)

    if not council:
        print("Error: no agent selected for the council.")
        return

    print("-" * 40)
    print(f"Council participants: {', '.join(name for name, _ in council)}")
    print("-" * 40)

    try:
        idea = input("Describe your idea:\n> ")
    except (EOFError, KeyboardInterrupt):
        print("\nInput aborted.")
        return

    idea = idea.strip()

    if not idea:
        print("Идея пустая. Нечего оценивать.")
        return

    session = SessionWriter()
    session.write_idea(idea)
    print(f"\nSession is being saved to: {session.dir}")

    session_path = Path(session.dir) if isinstance(session.dir, str) else session.dir
    evidence_dir = (
        session_path / "evidence" if (session_path / "evidence").exists() else None
    )

    await run_council_async(
        idea,
        [member_from_agent(a) for a in council],
        session,
        quick_mode=False,
        preset=None,
        no_open=False,
        evidence_dir=evidence_dir,
    )


def cli_main() -> None:
    """CLI entry point (console script `agentcouncil`, and `python main.py`).

    Dispatches to --gui / non-interactive / interactive BEFORE any event
    loop starts — run_noninteractive() calls asyncio.run() internally, and
    the interactive flow starts its own loop via asyncio.run(main()).
    """
    if "--gui" in sys.argv:
        # The GUI runs its own tkinter mainloop on the main thread; the
        # council loop lives in a worker thread (see src/gui/worker.py).
        # Imported HERE (not at module top): the GUI package pulls
        # customtkinter in, and a CLI-only environment must not need it.
        from src.gui.launcher import launch_gui

        launch_gui()
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        args = parse_args()
        config = load_config(args.config)
        if explicit_non_interactive(args) or not sys.stdin.isatty():
            sys.exit(run_noninteractive(args, config))
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nStopped by user.")


if __name__ == "__main__":
    cli_main()

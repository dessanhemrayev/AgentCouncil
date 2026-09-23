import json
import shutil
from typing import cast
from pathlib import Path

from src.core.models import KIND_CLI, KIND_OPENAI, CouncilMember, MemberKind
from src.core.logging_utils import get_logger

logger = get_logger("config.agents")

Agent = tuple[str, list[str]]

# Known CLI agents: (display name, launch command).
# IMPORTANT: the command must be non-interactive and contain the "{prompt}"
# placeholder — otherwise the prompt goes to stdin and a bare CLI hangs.
# @file: long prompts can be passed as @path (claude, hermes, gemini).
KNOWN_AGENTS: list[Agent] = [
    ("Claude Code", ["claude", "-p", "{prompt}"]),
    ("Codex", ["codex", "exec", "{prompt}"]),
    ("Gemini CLI", ["gemini", "-p", "{prompt}"]),
    ("Hermes", ["hermes", "-z", "{prompt}"]),
    ("Pi", ["pi", "-p", "{prompt}"]),
    ("DeepSeek Harness", ["dsh", "--profile", "headless", "{prompt}"]),
]

# Agents without @file support — stdin fallback instead.
AGENTS_WITHOUT_FILE_SUPPORT: list[str] = []


def check_agent_available(command: list[str]) -> bool:
    """Checks whether the agent command is available on the system (PATH)."""
    if not command:
        return False
    return shutil.which(command[0]) is not None


def discover_agents() -> list[Agent]:
    """Automatically finds agents from the known-CLI list on PATH."""
    return [
        (name, command)
        for name, command in KNOWN_AGENTS
        if check_agent_available(command)
    ]


# --- Roster: council members as data (T1) ---
# A member is no longer just a CLI-command tuple: CouncilMember carries the
# identity (id/name/aliases), the kind ("cli" | "openai"), and the call
# parameters. CLI/GUI still consume tuple lists — their switch is T6.


def member_from_agent(agent: Agent) -> CouncilMember:
    """CouncilMember for an autodiscovered known CLI (KNOWN_AGENTS on PATH).

    id = the executable name (command[0]): what users type, and what
    council.json uses as configured ids ("claude", "codex", ...). A
    discovered member is a default — today's no---agents run is exactly the
    discovered set.
    """
    name, command = agent
    member_id = command[0] if command else name.lower()
    return CouncilMember(
        id=member_id,
        name=name,
        kind=KIND_CLI,
        command=list(command),
        is_default=True,
    )


def member_from_config(entry: dict[str, object]) -> CouncilMember:
    """Parse one council.json members[] entry into a CouncilMember.

    Raises TypeError (a field of the wrong JSON type) or ValueError (a
    wrong value: unknown kind, missing required field) with a precise
    reason; build_roster catches both per entry (best-effort config: one
    broken entry must not take the run down, same philosophy as
    load_config). The JSON `type` -> kind mapping lives here (plan
    правка 2): council.json carries `type`, members carry `kind`.
    """
    member_id = entry.get("id")
    if not isinstance(member_id, str) or not member_id.strip():
        raise ValueError("missing or empty 'id'")
    member_id = member_id.strip()

    raw_type = entry.get("type", "cli")
    if raw_type == "cli":
        kind: MemberKind = KIND_CLI
    elif raw_type == "openai":
        kind = KIND_OPENAI
    else:
        raise ValueError(f"unknown 'type': {raw_type!r} (expected 'cli' or 'openai')")

    command = entry.get("command")
    if command is not None and (
        not isinstance(command, list) or not all(isinstance(p, str) for p in command)
    ):
        raise TypeError("'command' must be a list of strings")
    command = cast(list[str] | None, command)

    model = entry.get("model")
    if model is not None and not isinstance(model, str):
        raise TypeError("'model' must be a string")

    api_key_env = entry.get("api_key_env")
    if api_key_env is not None and not isinstance(api_key_env, str):
        raise TypeError("'api_key_env' must be a string")

    base_url = entry.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise TypeError("'base_url' must be a string")

    name = entry.get("name")
    if name is not None and not isinstance(name, str):
        raise TypeError("'name' must be a string")

    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise TypeError("'aliases' must be a list of strings")
    aliases = cast(list[str], aliases)

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TypeError("'enabled' must be a boolean")

    is_default = entry.get("default", False)
    if not isinstance(is_default, bool):
        raise TypeError("'default' must be a boolean")

    if kind == KIND_CLI:
        if not command:
            raise ValueError("cli member requires a non-empty 'command'")
        if not any("{prompt}" in part for part in command):
            raise ValueError("cli 'command' must contain the {prompt} placeholder")
    elif not model:
        raise ValueError("openai member requires a non-empty 'model'")

    return CouncilMember(
        id=member_id,
        name=name or member_id,
        kind=kind,
        command=list(command) if command else None,
        model=model,
        base_url=base_url or None,
        api_key_env=api_key_env or "OPENAI_API_KEY",
        enabled=enabled,
        is_default=is_default,
        aliases=list(aliases),
    )


def member_available(member: CouncilMember) -> bool:
    """Whether the member can be called right now.

    cli: executable present on PATH — the same check discover_agents
    applies. openai: always True at roster level; the key status (KEY OK /
    NO KEY) is a --list-agents / run-time concern (T9), not a roster filter.
    """
    if member.kind == KIND_CLI:
        return check_agent_available(member.command or [])
    return True


def build_roster(
    config: dict,
    discovered: list[Agent] | None = None,
) -> list[CouncilMember]:
    """The ordered roster: configured members first, then autodiscovered.

    - configured: council.json members[] in config order (member_from_config;
      broken entries are skipped with a warning). They stay in the roster
      even when not on PATH / NO KEY — --list-agents must be able to show
      them (T6), and an explicit request fails as a clean per-call error.
    - autodiscovered: known CLIs found on PATH (the `discovered` list —
      current PATH behavior) whose id is not already taken. Id conflicts:
      configured wins, the discovered agent is shadowed with a warning.
    Order: configured (config order), then the remaining discovered
    (discover_agents order). Selection over the roster: select_members().
    """
    roster: list[CouncilMember] = []
    taken: set[str] = set()

    members_raw = config.get("members")
    if members_raw is None:
        members_raw = []
    if not isinstance(members_raw, list):
        logger.warning("Warning: council.json 'members' must be a list; ignoring it.")
        members_raw = []

    for index, entry in enumerate(members_raw):
        try:
            member = member_from_config(entry)
        except (TypeError, ValueError) as exc:
            logger.warning("Warning: council.json members[%s] skipped: %s", index, exc)
            continue
        roster.append(member)
        taken.add(member.id.lower())

    for agent in discovered or []:
        candidate = member_from_agent(agent)
        if candidate.id.lower() in taken:
            logger.warning(
                "Warning: discovered agent %r (%s) is already in council.json members; "
                "the configured member wins.",
                candidate.name,
                candidate.id,
            )
            continue
        roster.append(candidate)
        taken.add(candidate.id.lower())

    return roster


def select_members(
    members: list[CouncilMember],
    agent_names: str | None = None,
    config: dict | None = None,
) -> list[CouncilMember]:
    """Select council members from the roster (main.py/GUI switch to it in T6).

    agent_names ("a,b,c"): match by id, name, or alias — case-insensitive
    equality, roster order preserved, duplicates collapsed. Disabled members
    are hard-off (an explicitly requested one warns and stays out). No match
    at all → warning + the full roster (the current fallback; the tuple
    version in main.py pins the same behavior until T6).

    agent_names empty: the default council — enabled members flagged
    is_default (configured "default": true plus autodiscovered knowns); cli
    members are additionally checked on PATH (parity with today's runs,
    which only ever see discovered agents); openai members are always
    candidates (NO KEY is a run-time error, T9). config["default_members"],
    when set, filters AND orders the result (that list's order wins); ids
    absent from the roster warn; an empty result is returned as-is — the
    caller reports "no agents selected".
    """
    if agent_names:
        requested = [tok.strip() for tok in agent_names.split(",") if tok.strip()]
        matched: set[str] = set()
        selected: list[CouncilMember] = []
        selected_ids: set[str] = set()
        for member in members:
            hit = [t for t in requested if member.matches(t)]
            if not hit:
                continue
            matched.update(t.lower() for t in hit)
            if not member.enabled:
                logger.warning(
                    f"Warning: agent {member.name!r} is disabled in "
                    "council.json; skipped."
                )
                continue
            if member.id.lower() not in selected_ids:
                selected.append(member)
                selected_ids.add(member.id.lower())
        unknown = [tok for tok in requested if tok.lower() not in matched]
        if unknown:
            logger.warning("Warning: unknown agents ignored: %s.", ", ".join(unknown))
        if not selected:
            logger.warning(
                f"Warning: none of the requested agents ({agent_names}) were found. Using all available."
            )
            return list(members)
        return selected

    defaults: list[CouncilMember] = []
    for member in members:
        if not member.enabled or not member.is_default:
            continue
        if not member_available(member):
            logger.warning(
                "Warning: default member %r (%s) is not available (not on PATH); skipped.",
                member.name,
                member.id,
            )
            continue
        defaults.append(member)

    default_ids = (config or {}).get("default_members")
    if not default_ids:
        return defaults
    if not isinstance(default_ids, list) or not all(
        isinstance(item, str) for item in default_ids
    ):
        logger.warning(
            "Warning: council.json 'default_members' must be a list of ids; ignored."
        )
        return defaults

    by_id = {member.id.lower(): member for member in defaults}
    ordered: list[CouncilMember] = []
    ordered_ids: set[str] = set()
    missing: list[str] = []
    for item in default_ids:
        pick = by_id.get(item.strip().lower())
        if pick is None:
            missing.append(item)
            continue
        if pick.id.lower() not in ordered_ids:
            ordered.append(pick)
            ordered_ids.add(pick.id.lower())
    if missing:
        logger.warning(
            "Warning: default_members absent from the roster: %s.", ", ".join(missing)
        )
    return ordered


# --- council.json: shared by CLI and core (orchestrator R3 rotation) ---
# Best-effort: a broken council.json must not take the run down.


def load_config(config_path: str | None = None) -> dict:
    """Load council.json config if it exists."""
    if config_path:
        path = Path(config_path)
    else:
        path = Path("council.json")

    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}  # broken config must not take the run down
    return {}


def save_config(config: dict, config_path: str | None = None) -> None:
    """Saves council.json — currently only used by the R3 first-move rotation;
    best-effort: a failed write must not take the run down; rotation is a convenience."""
    path = Path(config_path) if config_path else Path("council.json")
    try:
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        return  # best-effort: a failed write must not take the run down


def add_member_to_config(
    config: dict,
    entry: dict,
    config_path: str | None = None,
) -> CouncilMember:
    """Validate and append one council.json members[] entry, then persist it.

    Reuses member_from_config for validation — the same rules build_roster
    itself enforces on load (id required, cli command needs {prompt}, openai
    needs a model, ...) — so an agent added this way can never produce an
    entry the loader would later reject.

    Unlike save_config() (best-effort, used only for the R3 rotation
    counter), a write failure here is NOT swallowed: the caller (the GUI/web
    "add agent" form) needs to know the entry wasn't actually persisted.

    Raises TypeError/ValueError (invalid entry, from member_from_config, or
    a duplicate id against the current roster) or OSError (the write itself
    failed). Mutates and returns `config` unchanged on any of these — the
    file is only written after validation succeeds.
    """
    member = member_from_config(entry)

    existing = build_roster(config, discover_agents())
    if any(m.id.lower() == member.id.lower() for m in existing):
        raise ValueError(f"an agent with id {member.id!r} already exists")

    members_raw = config.get("members")
    members = list(members_raw) if isinstance(members_raw, list) else []
    members.append(entry)
    config["members"] = members

    path = Path(config_path) if config_path else Path("council.json")
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    return member

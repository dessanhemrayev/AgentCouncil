from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol


@dataclass
class AgentResult:
    name: str
    output: str = ""
    error: str | None = None


# Council member kinds. In council.json the field is called `type`; the
# rename to `kind` happens once at load time (build_roster), and `type` is
# what gets written back out (meta.json, T3) — one field, never both.
KIND_CLI: Final = "cli"
KIND_OPENAI: Final = "openai"
MemberKind = Literal["cli", "openai"]


@dataclass
class CouncilMember:
    """A council participant: a CLI agent or an API model.

    Data only — how the member is CALLED is decided at the edge by
    runner_for(member) (T2), so members stay serializable and testable.

    id — stable identifier used by --agents / default_members matching;
    name — display name; aliases — extra match keys for selection.
    """

    id: str
    name: str
    kind: MemberKind = KIND_CLI
    command: list[str] | None = None  # kind="cli"; must contain {prompt}
    model: str | None = None  # kind="openai": e.g. "gpt-5"
    base_url: str | None = None  # kind="openai"; None = https://api.openai.com
    api_key_env: str = "OPENAI_API_KEY"  # kind="openai"
    enabled: bool = True
    is_default: bool = False
    aliases: list[str] = field(default_factory=list)

    def matches(self, token: str) -> bool:
        """Case-insensitive equality match by id, name, or alias."""
        needle = token.strip().lower()
        if not needle:
            return False
        keys = (self.id, self.name, *self.aliases)
        return any(key.strip().lower() == needle for key in keys)


@dataclass
class RunContext:
    """Per-call runtime parameters for a Runner (T2).

    The member holds WHO is called; RunContext holds HOW this particular
    call runs: where session artifacts (prompt files, work artifacts) go,
    how long to wait, and the live-status callback ("running"/"done"/
    "error"/"timeout" — the GUI "Agent × Stage" matrix consumes it).
    """

    session_dir: Path | None = None
    timeout: float = 600.0
    on_status: Callable[[str, str], None] | None = None


class Runner(Protocol):
    """Delivery protocol: one async method, one result type (T2).

    Implementations: CliRunner (subprocess delivery), OpenAIResponsesRunner
    (HTTP, T8). Instances are built from a member by runner_for(member) —
    members carry data only, behavior lives at the edge (plan, правка 3).
    """

    async def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        """Deliver the prompt to the member; return the captured result."""
        ...

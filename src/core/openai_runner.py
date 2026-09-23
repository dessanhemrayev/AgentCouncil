"""API delivery: OpenAI Responses runner for kind="openai" members (T8).

Same Runner protocol as CliRunner: run(prompt, ctx) -> AgentResult with the
same on_status events ("running" / "done" / "error" / "timeout"), so the GUI
"Agent × Stage" matrix and the CLI spinner need no changes. No CLI mechanics
here — no @file, no argv thresholds: the prompt goes to the API as-is (the
whole value of the Runner split, [Ревью 4]).

The blocking HTTP call runs in a worker thread (asyncio.to_thread) so the
event loop of the GUI worker / web dashboard never stalls.
"""

import asyncio

from .api_client import DEFAULT_BASE_URL, ApiError, ApiTimeout, responses_post
from .models import AgentResult, CouncilMember, RunContext


class OpenAIResponsesRunner:
    """Runner for openai members: HTTP delivery via the Responses API.

    model / base_url / api_key_env are member data (council.json `type:
    "openai"` entries); prompt goes out unchanged, the assistant text comes
    back as AgentResult.output so extract_json_block keeps working unchanged.
    """

    def __init__(self, member: CouncilMember) -> None:
        self.member = member

    async def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        name = self.member.name
        on_status = ctx.on_status

        if not self.member.model:
            if on_status is not None:
                on_status(name, "error")
            return AgentResult(
                name=name, error="No model ('model') configured for the OpenAI member."
            )

        if on_status is not None:
            on_status(name, "running")

        try:
            output = await asyncio.to_thread(
                responses_post,
                base_url=self.member.base_url or DEFAULT_BASE_URL,
                model=self.member.model,
                input_text=prompt,
                api_key_env=self.member.api_key_env,
                timeout=ctx.timeout,
            )
        except ApiTimeout as exc:
            if on_status is not None:
                on_status(name, "timeout")
            return AgentResult(name=name, error=str(exc))
        except ApiError as exc:
            if on_status is not None:
                on_status(name, "error")
            return AgentResult(name=name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — any delivery failure must become an AgentResult error, not a crashed round
            if on_status is not None:
                on_status(name, "error")
            return AgentResult(name=name, error=f"{type(exc).__name__}: {exc}")

        if on_status is not None:
            on_status(name, "done")
        return AgentResult(name=name, output=output or "Empty response from agent.")

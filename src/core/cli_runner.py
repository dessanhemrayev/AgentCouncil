"""CLI delivery: subprocess runner for CouncilMember (T2).

All delivery mechanics live here (argv thresholds, @file fallback, stdin
fallback, .cmd/.bat resolution, pinned cwd); the core's single delivery
seam is council.run_member → runner_for() below. API members get
OpenAIResponsesRunner (T8) behind the same Runner protocol — with no
CLI-specific delivery (no @file, no argv thresholds — ревью 4).
"""

import asyncio
import contextlib
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from ..config.agents import AGENTS_WITHOUT_FILE_SUPPORT
from .models import (
    KIND_CLI,
    KIND_OPENAI,
    AgentResult,
    CouncilMember,
    RunContext,
    Runner,
)

# Prompt-length limits for the command line, per executable kind:
# .cmd/.bat (npm CLIs) go through cmd.exe (~8191 chars), raw .exe
# (venv console-scripts) up to ~32767. R2/R3 context can exceed both
# (WinError 206). hermes -z reads NO stdin — 7500 leaves headroom for
# cmd.exe; longer prompts fall to stdin mode, the agent drops out but
# the round continues (see main.py alive2 filtering).
MAX_ARG_PROMPT_LENGTH_CMD = 7500
MAX_ARG_PROMPT_LENGTH_EXE = 25000


def _max_arg_prompt_length(executable: str) -> int:
    if executable.lower().endswith((".cmd", ".bat")):
        return MAX_ARG_PROMPT_LENGTH_CMD
    return MAX_ARG_PROMPT_LENGTH_EXE


# Observed failure (Hermes @file, Round 2): a tool-using agent answers by
# WRITING A REPORT FILE instead of stdout, the vote is lost. The preamble
# below forbids that side effect inside the file the agent opens.
_NO_FILE_WRITE_PREAMBLE = """
DELIVERY NOTE: this file is your full task/prompt (sent as a file only
because it was too long for a command-line argument — not a request to
produce a file yourself). Do NOT create, write, or edit any file as your
answer, and do not just summarize what you would do. Print your complete
answer directly in your final response text — that stdout is what gets
captured and parsed; anything you write to disk instead is invisible to
the harness and will be treated as no answer at all.

---

""".lstrip()


class CliRunner:
    """Runner for CLI members: subprocess delivery with the fallback chain.

    Holds the member (data only — plan правка 3); built per member by
    runner_for(). run(prompt, ctx) is the single entry point.

    Prompt delivery strategy:
    1. If the prompt fits in a command-line argument — pass it as an argument.
    2. If the prompt is too long and the agent supports @file — write it to a
       temporary file and pass @path (removes the length limit).
    3. Otherwise — fall back to stdin (if the agent supports it) or error.

    Prompt files are kept for debugging/post-mortem (session/prompts/).
    """

    def __init__(self, member: CouncilMember) -> None:
        self.member = member

    async def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        name = self.member.name
        command = list(self.member.command or [])
        on_status = ctx.on_status
        session_dir = ctx.session_dir
        timeout = ctx.timeout

        if not command:
            return AgentResult(name=name, error="Не задана команда.")

        if on_status is not None:
            on_status(name, "running")

        executable = shutil.which(command[0])

        if executable is None:
            if on_status is not None:
                on_status(name, "error")
            return AgentResult(
                name=name,
                error=(
                    f"Команда не найдена: {command[0]}. "
                    "Проверьте, что агент установлен и доступен в PATH."
                ),
            )

        has_placeholder = any("{prompt}" in item for item in command)
        max_arg_prompt_length = _max_arg_prompt_length(executable)

        if len(prompt) > max_arg_prompt_length:
            print(
                f"[cli_runner] {name}: prompt length {len(prompt)} exceeds "
                f"threshold {max_arg_prompt_length} for {executable}",
                file=sys.stderr,
            )

        if has_placeholder and len(prompt) <= max_arg_prompt_length:
            cmd = [item.replace("{prompt}", prompt) for item in command]
            stdin_data = None
        elif has_placeholder and name not in AGENTS_WITHOUT_FILE_SUPPORT:
            # File fallback: @path removes the command-line length limit.
            try:
                if session_dir is not None:
                    prompts_dir = session_dir / "prompts"
                else:
                    prompts_dir = Path(tempfile.gettempdir()) / "agentcouncil_prompts"
                prompts_dir.mkdir(parents=True, exist_ok=True)
                # Must be ABSOLUTE: the subprocess runs with cwd=session_dir,
                # a relative @path would double the session path (run-007).
                prompts_dir = prompts_dir.resolve()

                prompt_file = prompts_dir / f"{name}_{uuid.uuid4().hex}.prompt.md"
                prompt_file.write_text(
                    _NO_FILE_WRITE_PREAMBLE + prompt, encoding="utf-8"
                )

                cmd = [item.replace("{prompt}", f"@{prompt_file}") for item in command]
                stdin_data = None

                print(
                    f"[cli_runner] {name}: using file fallback @{prompt_file}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001 — any fallback failure must degrade to stdin, not crash the round
                print(
                    f"[cli_runner] {name}: file fallback failed ({exc}), "
                    f"falling back to stdin",
                    file=sys.stderr,
                )
                cmd = [item for item in command if "{prompt}" not in item]
                stdin_data = prompt.encode("utf-8")
        elif has_placeholder:
            # Prompt too long for the command line — stdin fallback (documented
            # for claude/codex); for other CLIs a normal CLI error instead of a
            # guaranteed crash; the round continues without the agent.
            cmd = [item for item in command if "{prompt}" not in item]
            stdin_data = prompt.encode("utf-8")
        else:
            cmd = list(command)
            stdin_data = prompt.encode("utf-8")

        # Use the resolved .cmd/.bat wrapper path — create_subprocess_exec
        # does not resolve extensionless names the way cmd.exe does.
        cmd[0] = executable

        try:
            # Pin cwd: the work prompt tells the executor to save into
            # "artifacts/" — without this, files land wherever the harness runs.
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_dir) if session_dir is not None else None,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(stdin_data),
                    timeout=timeout,
                )
            except TimeoutError:
                # Best-effort cleanup of the killed process; its exit status
                # does not matter — the timeout result is already decided.
                with contextlib.suppress(Exception):
                    process.kill()

                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=5.0)

                if on_status is not None:
                    on_status(name, "timeout")
                return AgentResult(
                    name=name,
                    error=f"Таймаут: агент не ответил за {timeout} сек.",
                )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if process.returncode != 0:
                if on_status is not None:
                    on_status(name, "error")
                return AgentResult(
                    name=name,
                    output=stdout_text,
                    error=stderr_text or f"Код возврата: {process.returncode}.",
                )

            if not stdout_text and stderr_text:
                if on_status is not None:
                    on_status(name, "error")
                return AgentResult(
                    name=name,
                    error=stderr_text,
                )

            if on_status is not None:
                on_status(name, "done")
            return AgentResult(
                name=name,
                output=stdout_text or "Пустой ответ от агента.",
            )

        except Exception as exc:  # noqa: BLE001 — any delivery failure must become an AgentResult error, not a crashed round
            message = f"{type(exc).__name__}: {exc}"

            if (
                sys.platform == "win32"
                and isinstance(exc, OSError)
                and getattr(exc, "winerror", None) == 193
            ):
                message += (
                    "\nIf the command is a .cmd/.bat wrapper, try "
                    "['cmd', '/c', 'command-name', ...]."
                )

            if on_status is not None:
                on_status(name, "error")
            return AgentResult(name=name, error=message)


def runner_for(member: CouncilMember) -> Runner:
    """Build the runner for a member from its kind (plan, правка 3).

    The single dispatch point between member data and delivery behavior.
    The openai branch imports lazily (T8) — this module stays importable
    and cheap for pure-CLI runs.
    """
    if member.kind == KIND_CLI:
        return CliRunner(member=member)
    if member.kind == KIND_OPENAI:
        from .openai_runner import OpenAIResponsesRunner  # T8

        return OpenAIResponsesRunner(member=member)
    raise ValueError(f"unknown member kind: {member.kind!r}")

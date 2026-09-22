"""Tests for CLI delivery (cli_runner.CliRunner via council.run_member) —
launching CLI agents as subprocesses.

run_member is an async function but pytest-asyncio is not used: each test
wraps the coroutine in asyncio.run to avoid adding dependencies beyond
pytest. Tests launch real subprocesses via sys.executable, so they
don't depend on installed AI CLIs.
"""

import asyncio
import sys
from pathlib import Path

import src.core.cli_runner as cli_runner
from src.core.council import run_member
from src.core.models import KIND_CLI, CouncilMember

PYTHON = sys.executable


def run(coro):
    return asyncio.run(coro)


def run_agent(name, command, prompt, **kwargs):
    """Local shim keeping the tests' legacy (name, command) call shape."""

    async def _call():
        member = CouncilMember(id=name, name=name, kind=KIND_CLI, command=list(command))
        return await run_member(member, prompt, **kwargs)

    return _call()


class TestValidation:
    def test_empty_command_returns_error(self):
        result = run(run_agent("A", [], "prompt"))

        assert result.name == "A"
        assert result.output == ""
        assert result.error == "Не задана команда."

    def test_command_not_found(self):
        result = run(run_agent("A", ["no-such-command-xyz-123"], "prompt"))

        assert result.output == ""
        assert "Команда не найдена: no-such-command-xyz-123" in result.error
        assert "PATH" in result.error


class TestPromptDelivery:
    def test_prompt_passed_as_argument(self):
        command = [PYTHON, "-c", "import sys; print('ARG:' + sys.argv[1])", "{prompt}"]

        result = run(run_agent("A", command, "hello world"))

        assert result.error is None
        assert result.output == "ARG:hello world"

    def test_prompt_substituted_inside_argument(self):
        command = [
            PYTHON,
            "-c",
            "import sys; print('ARG:' + sys.argv[1])",
            "pre-{prompt}-post",
        ]

        result = run(run_agent("A", command, "mid"))

        assert result.error is None
        assert result.output == "ARG:pre-mid-post"

    def test_prompt_passed_via_stdin(self):
        command = [PYTHON, "-c", "import sys; print('STDIN:' + sys.stdin.read())"]

        result = run(run_agent("A", command, "hello stdin"))

        assert result.error is None
        assert result.output == "STDIN:hello stdin"

    def test_oversized_prompt_uses_file_fallback(self):
        # @file fallback: the prompt goes to a temp file, the agent gets @path.
        command = [
            PYTHON,
            "-c",
            "import sys; print('ARGC:' + str(len(sys.argv)) + ' ARG1:' + sys.argv[1])",
            "{prompt}",
        ]
        huge_prompt = "x" * (cli_runner.MAX_ARG_PROMPT_LENGTH_EXE + 1)

        result = run(run_agent("A", command, huge_prompt))

        assert result.error is None
        assert result.output.startswith("ARGC:2 ARG1:@")
        assert result.output.endswith(".prompt.md")

    def test_oversized_prompt_falls_back_to_stdin_when_file_not_supported(self):
        # For agents in AGENTS_WITHOUT_FILE_SUPPORT — fallback to stdin.
        from src.config import AGENTS_WITHOUT_FILE_SUPPORT

        AGENTS_WITHOUT_FILE_SUPPORT.append("test_agent_no_file")

        command = [
            PYTHON,
            "-c",
            "import sys; print('ARGC:' + str(len(sys.argv)) + ' STDIN:' + sys.stdin.read())",
            "{prompt}",
        ]
        huge_prompt = "x" * (cli_runner.MAX_ARG_PROMPT_LENGTH_EXE + 1)

        try:
            result = run(run_agent("test_agent_no_file", command, huge_prompt))
            assert result.error is None
            assert result.output == f"ARGC:1 STDIN:{huge_prompt}"
        finally:
            AGENTS_WITHOUT_FILE_SUPPORT.remove("test_agent_no_file")

    def test_prompt_at_threshold_still_passed_as_argument(self):
        command = [PYTHON, "-c", "import sys; print('ARG:' + sys.argv[1])", "{prompt}"]
        boundary_prompt = "x" * cli_runner.MAX_ARG_PROMPT_LENGTH_EXE

        result = run(run_agent("A", command, boundary_prompt))

        assert result.error is None
        assert result.output == f"ARG:{boundary_prompt}"


class TestSessionDirCwd:
    # Regression: without cwd=session_dir, files an executor writes to
    # "artifacts/" land in the harness's cwd and are lost to the pipeline.
    def test_agent_cwd_pinned_to_session_dir(self, tmp_path):
        command = [PYTHON, "-c", "import os; print('CWD:' + os.getcwd())", "{prompt}"]

        result = run(run_agent("A", command, "hello", session_dir=tmp_path))

        assert result.error is None
        assert result.output == f"CWD:{tmp_path}"

    def test_agent_cwd_untouched_without_session_dir(self):
        command = [PYTHON, "-c", "import os; print('CWD:' + os.getcwd())", "{prompt}"]

        result = run(run_agent("A", command, "hello"))

        assert result.error is None
        assert result.output == f"CWD:{Path.cwd()}"

    def test_file_fallback_prompt_path_survives_cwd_pin(self, tmp_path, monkeypatch):
        # Regression (run-007, Pi): after the cwd=session_dir fix, a RELATIVE
        # @path resolved against the new cwd and doubled the session path.
        # We run from a relative session_dir, as in production.
        monkeypatch.chdir(tmp_path)
        session_dir = Path("sessions") / "run-999"  # relative, as in production
        session_dir.mkdir(parents=True)
        command = [
            PYTHON,
            "-c",
            "import sys; print(open(sys.argv[1][1:]).read())",
            "{prompt}",
        ]
        huge_prompt = "x" * (cli_runner.MAX_ARG_PROMPT_LENGTH_EXE + 1)

        result = run(run_agent("A", command, huge_prompt, session_dir=session_dir))

        assert result.error is None
        assert result.output.strip().endswith(
            "x" * 50
        )  # file was actually read, not "file not found"


class TestMaxArgPromptLength:
    # .exe (venv console-scripts) vs .cmd/.bat (npm, via cmd.exe) have very
    # different command-line limits; a uniform threshold broke .exe agents
    # (hermes -z does not read stdin).
    def test_cmd_extension_uses_lower_threshold(self):
        assert (
            cli_runner._max_arg_prompt_length("C:\\npm\\dsh.cmd")
            == cli_runner.MAX_ARG_PROMPT_LENGTH_CMD
        )

    def test_bat_extension_uses_lower_threshold(self):
        assert (
            cli_runner._max_arg_prompt_length("C:\\bin\\agent.bat")
            == cli_runner.MAX_ARG_PROMPT_LENGTH_CMD
        )

    def test_cmd_extension_is_case_insensitive(self):
        assert (
            cli_runner._max_arg_prompt_length("C:\\npm\\DSH.CMD")
            == cli_runner.MAX_ARG_PROMPT_LENGTH_CMD
        )

    def test_exe_extension_uses_higher_threshold(self):
        assert (
            cli_runner._max_arg_prompt_length("C:\\Scripts\\hermes.exe")
            == cli_runner.MAX_ARG_PROMPT_LENGTH_EXE
        )

    def test_extensionless_uses_higher_threshold(self):
        assert (
            cli_runner._max_arg_prompt_length("/usr/local/bin/claude")
            == cli_runner.MAX_ARG_PROMPT_LENGTH_EXE
        )

    def test_higher_threshold_is_actually_higher(self):
        assert (
            cli_runner.MAX_ARG_PROMPT_LENGTH_EXE > cli_runner.MAX_ARG_PROMPT_LENGTH_CMD
        )


class TestExitCodes:
    def test_nonzero_exit_with_stderr(self):
        command = [PYTHON, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]

        result = run(run_agent("A", command, "p"))

        assert result.output == ""
        assert result.error == "boom"

    def test_nonzero_exit_without_stderr(self):
        command = [PYTHON, "-c", "import sys; sys.exit(3)"]

        result = run(run_agent("A", command, "p"))

        assert result.error == "Код возврата: 3."

    def test_zero_exit_with_stderr_only(self):
        command = [PYTHON, "-c", "import sys; sys.stderr.write('warning text')"]

        result = run(run_agent("A", command, "p"))

        assert result.output == ""
        assert result.error == "warning text"

    def test_zero_exit_with_both_streams_keeps_stdout(self):
        command = [
            PYTHON,
            "-c",
            "import sys; sys.stdout.write('out'); sys.stderr.write('err')",
        ]

        result = run(run_agent("A", command, "p"))

        assert result.output == "out"
        assert result.error is None

    def test_zero_exit_empty_output(self):
        command = [PYTHON, "-c", "pass"]

        result = run(run_agent("A", command, "p"))

        assert result.error is None
        assert result.output == "Пустой ответ от агента."


class TestTimeout:
    def test_timeout_returns_error(self):
        command = [PYTHON, "-c", "import time; time.sleep(5)"]

        result = run(run_agent("slow", command, "p", timeout=0.5))

        assert result.output == ""
        assert "Таймаут" in result.error
        assert "0.5" in result.error


class TestDecoding:
    def test_invalid_utf8_is_replaced(self):
        command = [PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'ok\\xffbad')"]

        result = run(run_agent("A", command, "p"))

        assert result.error is None
        assert result.output == "ok\ufffdbad"


class TestWinerrorHint:
    def test_winerror_193_adds_cmd_bat_hint(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            exc = OSError("%1 is not a valid Win32 application")
            exc.winerror = 193
            raise exc

        monkeypatch.setattr(cli_runner.sys, "platform", "win32")
        monkeypatch.setattr(cli_runner.asyncio, "create_subprocess_exec", fake_exec)

        result = run(run_agent("A", [PYTHON], "p"))

        assert result.error is not None
        assert result.error.startswith("OSError:")
        assert ".cmd/.bat" in result.error
        assert "cmd', '/c'" in result.error or 'cmd\', "/c"' in result.error

    def test_other_oserror_has_no_hint(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(cli_runner.sys, "platform", "win32")
        monkeypatch.setattr(cli_runner.asyncio, "create_subprocess_exec", fake_exec)

        result = run(run_agent("A", [PYTHON], "p"))

        assert result.error == "OSError: permission denied"

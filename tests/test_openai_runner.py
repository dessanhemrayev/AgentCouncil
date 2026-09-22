"""Tests for the API delivery chain (T7/T8): api_client + OpenAIResponsesRunner.

The Responses API is stubbed with a local http.server in a thread — no
network, no real key: the Bearer token is read from os.environ, and the
tests point api_key_env at a monkeypatched test variable. Covers the
happy path (200), 401 (no retry), 429/5xx (one retry), read timeout,
the runner_for dispatch, the runner's error mapping (NO KEY / timeout /
HTTP errors → AgentResult.error, never a crashed round), and R1 session
parity: an openai member writes the same round1/<id>.md files as a cli
member.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.core.aggregation import ok
from src.core.api_client import ApiError, ApiTimeout, responses_post
from src.core.cli_runner import runner_for
from src.core.council import run_member, run_round1
from src.core.models import (
    KIND_CLI,
    KIND_OPENAI,
    AgentResult,
    CouncilMember,
    RunContext,
)
from src.core.openai_runner import OpenAIResponsesRunner
from src.core.session import SessionWriter

KEY_ENV = "AGENTCOUNCIL_TEST_OPENAI_KEY"

R1_JSON = (
    '{"claims": [{"id": "CLM-1", "statement": "утверждение", '
    '"status": "ASSUMPTION", "evidence_ref": null, "falsification_test": "t"}]}'
)


def run(coro):
    return asyncio.run(coro)


def _response(text: str) -> str:
    """A minimal /v1/responses payload carrying one assistant message."""
    return json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }
            ]
        },
        ensure_ascii=False,
    )


class _StubAPI:
    """Minimal /v1/responses stub: records requests, replays scripted replies.

    script — list of (status, body) pairs consumed per request; an empty
    script answers 500. delay — seconds to sleep before answering (timeout
    tests). ThreadingHTTPServer: each request gets its own thread, so a
    sleeping handler does not block the next request (needed for retry).
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.auth: list[str] = []
        self.script: list[tuple[int, str]] = []
        self.delay: float = 0.0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def _make_handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    stub.requests.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    stub.requests.append({})
                stub.auth.append(self.headers.get("Authorization", ""))
                if stub.delay:
                    import time

                    time.sleep(stub.delay)
                status, body = (
                    stub.script.pop(0) if stub.script else (500, "stub exhausted")
                )
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:  # silence the test log
                pass

        return Handler

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub_api(monkeypatch):
    """Local API stub + a fake key in the env + zero retry delay."""
    api = _StubAPI()
    monkeypatch.setenv(KEY_ENV, "test-key-123")
    monkeypatch.setattr("src.core.api_client.RETRY_DELAY_SECONDS", 0.0)
    yield api
    api.stop()


def _openai_member(base_url: str, **overrides) -> CouncilMember:
    params = {
        "id": "gpt-5-api",
        "name": "GPT-5 API",
        "kind": KIND_OPENAI,
        "model": "gpt-5",
        "base_url": base_url,
        "api_key_env": KEY_ENV,
    }
    params.update(overrides)
    return CouncilMember(**params)


class TestResponsesPost:
    def test_200_returns_output_text(self, stub_api):
        stub_api.script = [(200, _response("привет, совет"))]

        out = responses_post(
            base_url=stub_api.base_url,
            model="gpt-5",
            input_text="оцени идею",
            api_key_env=KEY_ENV,
        )

        assert out == "привет, совет"
        assert stub_api.requests[0]["input"] == "оцени идею"
        assert stub_api.requests[0]["model"] == "gpt-5"
        assert stub_api.auth[0] == "Bearer test-key-123"

    def test_multiple_output_text_items_are_joined(self, stub_api):
        payload = json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "часть 1"}],
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "часть 2"}],
                    },
                    {"type": "reasoning", "summary": []},
                ]
            },
            ensure_ascii=False,
        )
        stub_api.script = [(200, payload)]

        out = responses_post(
            base_url=stub_api.base_url,
            model="gpt-5",
            input_text="p",
            api_key_env=KEY_ENV,
        )

        assert out == "часть 1\nчасть 2"

    def test_missing_key_raises_no_key_before_any_request(self, stub_api, monkeypatch):
        monkeypatch.delenv(KEY_ENV, raising=False)

        with pytest.raises(ApiError, match="NO KEY"):
            responses_post(
                base_url=stub_api.base_url,
                model="gpt-5",
                input_text="p",
                api_key_env=KEY_ENV,
            )

        assert stub_api.requests == []  # не было ни одного запроса

    def test_401_is_not_retried(self, stub_api):
        stub_api.script = [(401, json.dumps({"error": {"message": "bad key"}}))]

        with pytest.raises(ApiError, match="HTTP 401"):
            responses_post(
                base_url=stub_api.base_url,
                model="gpt-5",
                input_text="p",
                api_key_env=KEY_ENV,
            )

        assert len(stub_api.requests) == 1  # 4xx — без повторов

    def test_429_retried_once_then_success(self, stub_api):
        stub_api.script = [(429, "{}"), (200, _response("ok"))]

        out = responses_post(
            base_url=stub_api.base_url,
            model="gpt-5",
            input_text="p",
            api_key_env=KEY_ENV,
        )

        assert out == "ok"
        assert len(stub_api.requests) == 2  # один retry

    def test_500_retried_once_then_fails(self, stub_api):
        stub_api.script = [(500, "boom"), (500, "boom")]

        with pytest.raises(ApiError, match="HTTP 500"):
            responses_post(
                base_url=stub_api.base_url,
                model="gpt-5",
                input_text="p",
                api_key_env=KEY_ENV,
            )

        assert len(stub_api.requests) == 2  # один retry, затем честная ошибка

    def test_read_timeout_raises_api_timeout(self, stub_api):
        stub_api.delay = 1.5

        with pytest.raises(ApiTimeout, match="Таймаут"):
            responses_post(
                base_url=stub_api.base_url,
                model="gpt-5",
                input_text="p",
                api_key_env=KEY_ENV,
                timeout=0.3,
            )


class TestOpenAIResponsesRunner:
    def test_runner_for_dispatches_openai_to_responses_runner(self):
        member = CouncilMember(id="g", name="G", kind=KIND_OPENAI, model="gpt-5")

        assert isinstance(runner_for(member), OpenAIResponsesRunner)

    def test_missing_model_returns_error_not_crash(self):
        member = CouncilMember(id="g", name="G", kind=KIND_OPENAI)

        result = run(OpenAIResponsesRunner(member=member).run("p", RunContext()))

        assert result.name == "G"
        assert result.output == ""
        assert "Не задана модель" in result.error

    def test_no_key_becomes_agent_error(self, stub_api, monkeypatch):
        monkeypatch.delenv(KEY_ENV, raising=False)
        statuses: list[tuple[str, str]] = []
        member = _openai_member(stub_api.base_url)

        result = run(
            OpenAIResponsesRunner(member=member).run(
                "p", RunContext(on_status=lambda n, s: statuses.append((n, s)))
            )
        )

        assert result.error.startswith("NO KEY")
        assert result.output == ""
        assert statuses == [("GPT-5 API", "running"), ("GPT-5 API", "error")]

    def test_happy_path_output_and_statuses(self, stub_api):
        stub_api.script = [(200, _response("ответ"))]
        statuses: list[tuple[str, str]] = []
        member = _openai_member(stub_api.base_url)

        result = run(
            OpenAIResponsesRunner(member=member).run(
                "промпт", RunContext(on_status=lambda n, s: statuses.append((n, s)))
            )
        )

        assert result.error is None
        assert result.output == "ответ"
        assert statuses == [("GPT-5 API", "running"), ("GPT-5 API", "done")]
        assert stub_api.requests[0]["input"] == "промпт"

    def test_timeout_maps_to_timeout_status(self, stub_api):
        stub_api.delay = 1.5
        statuses: list[tuple[str, str]] = []
        member = _openai_member(stub_api.base_url)

        result = run(
            OpenAIResponsesRunner(member=member).run(
                "p",
                RunContext(timeout=0.3, on_status=lambda n, s: statuses.append((n, s))),
            )
        )

        assert "Таймаут" in result.error
        assert statuses == [("GPT-5 API", "running"), ("GPT-5 API", "timeout")]


class TestOpenAiMemberInCouncil:
    def test_openai_member_writes_same_r1_files_as_cli(
        self, stub_api, monkeypatch, tmp_path
    ):
        """An openai member runs through the SAME round machinery: the
        session gets round1/<id>.md exactly like for a cli member."""
        stub_api.script = [
            (200, _response("```json\n" + R1_JSON + "\n```")),
            (200, _response("```json\n" + R1_JSON + "\n```")),
        ]

        openai_member = _openai_member(stub_api.base_url)
        cli_member = CouncilMember(
            id="claude",
            name="Claude Code",
            kind=KIND_CLI,
            command=["claude", "-p", "{prompt}"],
        )

        real = run_member

        async def hybrid(member, prompt, **kwargs):
            # openai goes through the real runner + stub HTTP; cli is stubbed.
            if member.kind == KIND_OPENAI:
                return await real(member, prompt, **kwargs)
            return AgentResult(name=member.name, output="```json\n" + R1_JSON + "\n```")

        monkeypatch.setattr("src.core.council.run_member", hybrid)

        session = SessionWriter(tmp_path / "sessions")
        results, inventory, degradation = run(
            run_round1("идея", [cli_member, openai_member], session)
        )

        assert ok(results["GPT-5 API"])
        assert inventory["GPT-5 API"][0]["id"] == "CLM-1"
        assert degradation == "full"
        files = {p.name for p in (session.dir / "round1").iterdir()}
        assert files == {"claude-code.md", "gpt-5-api.md"}
        # The prompt reached the API unchanged (the R1 prompt embeds the idea).
        assert "идея" in stub_api.requests[0]["input"]

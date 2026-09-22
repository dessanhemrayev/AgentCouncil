"""End-to-end tests for the web dashboard API (src/web/).

External effects (spawning real CLI agents) are substituted by monkeypatching
src.core.orchestrator.run_council_async — run_council_background imports it
locally (`from ..core.orchestrator import run_council_async`) inside the
function body, so patching the module-level name is picked up on next call,
same pattern as tests/test_main_flow.py patches src.core.orchestrator.run_round1.

Every session dir this file creates lives under a per-test tmp_path (cwd is
monkeypatched there) — nothing here touches the repo's own sessions/.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import src.web.api as api_module
from src.web.main import app


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Run every test from an empty tmp dir — sessions/council.json isolated."""
    monkeypatch.chdir(tmp_path)
    api_module.ACTIVE_RUNS.clear()
    yield
    api_module.ACTIVE_RUNS.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _write_fake_session(tmp_path, name="run-001", *, with_vote=True):
    session_dir = tmp_path / "sessions" / name
    (session_dir / "round1").mkdir(parents=True)
    (session_dir / "round1" / "agent-a.md").write_text("R1 answer", encoding="utf-8")
    (session_dir / "idea.md").write_text("Test idea", encoding="utf-8")
    (session_dir / "verdict.md").write_text(
        "# Verdict\n\nLooks good.", encoding="utf-8"
    )
    (session_dir / "meta.json").write_text(
        json.dumps({"agents": [{"name": "agent-a", "type": "cli"}]}), encoding="utf-8"
    )
    if with_vote:
        (session_dir / "vote.json").write_text(
            json.dumps(
                {
                    "votes": {"agent-a": {"score": 8, "verdict": "approve"}},
                    "missing": [],
                    "average_score": 8,
                    "min_score": 8,
                    "max_score": 8,
                }
            ),
            encoding="utf-8",
        )
        (session_dir / "claims.json").write_text(
            json.dumps({"claims": [], "untracked_r2": [], "untracked_r3": []}),
            encoding="utf-8",
        )
    return session_dir


class TestSessionsEndpoints:
    def test_list_sessions_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_and_get_session_detail(self, client, tmp_path):
        _write_fake_session(tmp_path)
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        [summary] = resp.json()
        assert summary["session_id"] == "run-001"
        assert summary["status"] == "completed"

        detail = client.get("/api/sessions/run-001").json()
        assert detail["verdict_markdown"].startswith("# Verdict")
        assert detail["rounds"]["round1"]["agent-a"] == "R1 answer"
        assert detail["vote"]["average_score"] == 8
        # claims_map_text is server-rendered via cli.render.render_claims_map_lines
        # (see src/web/api.py) rather than re-derived from the nested JSON in JS.
        assert isinstance(detail["claims_map_text"], str)

    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/run-999")
        assert resp.status_code == 404

    def test_running_session_reports_running_status(self, client, tmp_path):
        session_dir = _write_fake_session(tmp_path, with_vote=False)
        (session_dir / "verdict.md").unlink()
        api_module.ACTIVE_RUNS["run-001"] = {"started_at": None, "websocket": None}
        resp = client.get("/api/sessions")
        [summary] = resp.json()
        assert summary["status"] == "running"


class TestResolveSessionDir:
    @pytest.mark.parametrize(
        "session_id",
        ["../../etc", "..%2f..%2fetc", "run-1/../../secret", "not-a-run-id", ""],
    )
    def test_rejects_non_run_ids(self, session_id):
        with pytest.raises(Exception) as exc_info:
            api_module._resolve_session_dir(session_id)
        assert getattr(exc_info.value, "status_code", None) == 404

    def test_accepts_run_id(self):
        assert api_module._resolve_session_dir("run-042").name == "run-042"

    def test_get_session_file_rejects_traversal(self, client, tmp_path):
        _write_fake_session(tmp_path)
        resp = client.get("/api/sessions/..%2f..%2fsecrets/files/x")
        assert resp.status_code == 404


class TestListAgents:
    def test_includes_configured_openai_member(self, client, tmp_path):
        (tmp_path / "council.json").write_text(
            json.dumps(
                {
                    "members": [
                        {
                            "id": "gpt-test",
                            "name": "GPT Test",
                            "type": "openai",
                            "model": "gpt-5",
                            "default": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        openai_agents = [a for a in agents if a["kind"] == "openai"]
        assert len(openai_agents) == 1
        assert openai_agents[0]["id"] == "gpt-test"
        assert openai_agents[0]["model"] == "gpt-5"
        assert openai_agents[0]["available"] is True


class TestAddAgent:
    """POST /api/agents — the web dashboard's "Add agent" form. Backed by
    add_member_to_config (src/config/agents.py), which validates via
    member_from_config — the same rules council.json's own loader enforces —
    before persisting, so a rejected entry never reaches disk."""

    def test_add_cli_agent_persists_and_shows_up_in_roster(self, client, tmp_path):
        resp = client.post(
            "/api/agents",
            data={
                "kind": "cli",
                "id": "test-custom-agent",
                "name": "Test Custom Agent",
                "command": "test-custom-agent --flag {prompt}",
                "is_default": "true",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "test-custom-agent"
        assert body["kind"] == "cli"
        assert body["command"] == ["test-custom-agent", "--flag", "{prompt}"]

        saved = json.loads((tmp_path / "council.json").read_text(encoding="utf-8"))
        assert saved["members"][0]["id"] == "test-custom-agent"

        agents = client.get("/api/agents").json()["agents"]
        assert any(a["id"] == "test-custom-agent" for a in agents)

    def test_add_openai_agent(self, client):
        resp = client.post(
            "/api/agents",
            data={
                "kind": "openai",
                "id": "test-gpt",
                "model": "gpt-5",
                "api_key_env": "MY_KEY",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "openai"
        assert body["model"] == "gpt-5"
        assert body["command"] is None

    def test_command_string_is_shlex_split(self, client):
        resp = client.post(
            "/api/agents",
            data={
                "kind": "cli",
                "id": "test-quoted",
                "command": 'test-quoted --path "C:/some path/x" {prompt}',
            },
        )
        assert resp.status_code == 200
        assert resp.json()["command"] == [
            "test-quoted",
            "--path",
            "C:/some path/x",
            "{prompt}",
        ]

    def test_rejects_missing_prompt_placeholder(self, client):
        resp = client.post(
            "/api/agents",
            data={
                "kind": "cli",
                "id": "test-bad-agent",
                "command": "test-bad-agent --no-placeholder",
            },
        )
        assert resp.status_code == 400

    def test_rejects_duplicate_id(self, client):
        first = client.post(
            "/api/agents",
            data={"kind": "cli", "id": "test-dup", "command": "test-dup {prompt}"},
        )
        assert first.status_code == 200

        second = client.post(
            "/api/agents",
            data={"kind": "cli", "id": "test-dup", "command": "other {prompt}"},
        )
        assert second.status_code == 400


class TestStartCouncil:
    def test_rejects_empty_task(self, client):
        resp = client.post("/api/council/start", data={"task": "   "})
        assert resp.status_code == 400

    def test_sequential_runs_get_distinct_session_ids(self, client, monkeypatch):
        async def fake_run(*args, **kwargs):
            return None

        monkeypatch.setattr("src.core.orchestrator.run_council_async", fake_run)

        ids = []
        for _ in range(2):
            resp = client.post("/api/council/start", data={"task": "idea"})
            assert resp.status_code == 200
            ids.append(resp.json()["session_id"])
            # Let the background task run to completion (and pop itself from
            # ACTIVE_RUNS) before starting the next one.
            for _ in range(200):
                if not api_module.ACTIVE_RUNS:
                    break
                import time

                time.sleep(0.01)

        assert ids[0] != ids[1]
        assert ids == sorted(ids)

    def test_rejects_concurrent_run_with_409(self, client, monkeypatch):
        release = asyncio.Event()

        async def fake_run(*args, **kwargs):
            await release.wait()

        monkeypatch.setattr("src.core.orchestrator.run_council_async", fake_run)

        first = client.post("/api/council/start", data={"task": "idea one"})
        assert first.status_code == 200

        second = client.post("/api/council/start", data={"task": "idea two"})
        assert second.status_code == 409

        release.set()


class TestWebSocketContract:
    def test_agent_status_and_stage_change_are_streamed(self, client, monkeypatch):
        """The core regression this guards: orchestrator calls on_agent_status/
        on_stage SYNCHRONOUSLY (see src/core/orchestrator.py _make_stage_cb/
        _emit_stage) — those callbacks must stay plain `def`s that schedule
        asyncio.create_task(...) themselves, not `async def`s (which would
        just build a coroutine that's never awaited and send nothing)."""

        async def fake_run(*args, **kwargs):
            on_agent_status = kwargs["on_agent_status"]
            on_stage = kwargs["on_stage"]
            session = args[2]
            session_id = session.dir.name
            # Deterministically wait for the test's websocket to attach
            # instead of racing a fixed sleep against it.
            for _ in range(500):
                info = api_module.ACTIVE_RUNS.get(session_id)
                if info and info.get("websocket") is not None:
                    break
                await asyncio.sleep(0.01)
            on_agent_status("agent-a", "running", "round1")
            on_agent_status("agent-a", "done", "round1")
            on_stage("round2")

        monkeypatch.setattr("src.core.orchestrator.run_council_async", fake_run)

        resp = client.post("/api/council/start", data={"task": "idea"})
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        with client.websocket_connect(f"/ws/{session_id}") as ws:
            seen_types = []
            for _ in range(5):
                msg = ws.receive_json()
                seen_types.append(msg["type"])
                if msg["type"] == "stage_change":
                    break

        assert "agent_status" in seen_types
        assert "stage_change" in seen_types

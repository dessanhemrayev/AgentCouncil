"""T1 — roster as data: CouncilMember, build_roster, select_members.

The data layer only: config parsing (JSON `type` -> kind), roster assembly
(configured members win id conflicts, autodiscovered knowns are appended),
and selection (id/name/alias matching, default_members ordering, the
current fallback). CLI/GUI still consume tuple lists — their switch is T6,
so no main.py behavior is touched here.
"""

import json

import pytest

from src.config.agents import (
    add_member_to_config,
    build_roster,
    member_available,
    member_from_agent,
    member_from_config,
    select_members,
)
from src.core.models import KIND_CLI, KIND_OPENAI, CouncilMember

CLAUDE = ("Claude Code", ["claude", "-p", "{prompt}"])
CODEX = ("Codex", ["codex", "exec", "{prompt}"])
GEMINI = ("Gemini CLI", ["gemini", "-p", "{prompt}"])


@pytest.fixture
def fake_which(monkeypatch):
    """Patch PATH checks: executables in `present` count as installed."""

    def _set(present):
        monkeypatch.setattr(
            "src.config.agents.shutil.which",
            lambda cmd: f"C:/fake/{cmd}.cmd" if cmd in present else None,
        )

    return _set


def _members():
    """Fresh autodiscovered-style roster (claude, codex, gemini)."""
    return [member_from_agent(a) for a in (CLAUDE, CODEX, GEMINI)]


class TestCouncilMember:
    def test_defaults(self):
        member = CouncilMember(id="claude", name="Claude Code")
        assert member.kind == KIND_CLI
        assert member.enabled is True
        assert member.is_default is False
        assert member.aliases == []
        assert member.command is None
        assert member.model is None
        assert member.api_key_env == "OPENAI_API_KEY"

    def test_matches_id_name_and_alias_case_insensitively(self):
        member = CouncilMember(id="claude", name="Claude Code", aliases=["anthropic"])
        assert member.matches("claude")
        assert member.matches("CLAUDE CODE")
        assert member.matches("Anthropic")

    def test_match_is_equality_not_substring(self):
        member = CouncilMember(id="claude", name="Claude Code")
        assert not member.matches("clau")
        assert not member.matches("")


class TestMemberFromAgent:
    def test_id_is_the_executable_name(self):
        member = member_from_agent(CLAUDE)
        assert member.id == "claude"
        assert member.name == "Claude Code"
        assert member.kind == KIND_CLI
        assert member.command == ["claude", "-p", "{prompt}"]
        assert member.is_default is True

    def test_empty_command_falls_back_to_lowercased_name(self):
        member = member_from_agent(("Odd Agent", []))
        assert member.id == "odd agent"


class TestMemberFromConfig:
    def test_full_cli_entry(self):
        member = member_from_config(
            {
                "id": "claude",
                "name": "Claude Code",
                "type": "cli",
                "command": ["claude", "-p", "{prompt}"],
                "default": True,
                "aliases": ["claude-code"],
            }
        )
        assert member.kind == KIND_CLI
        assert member.is_default is True
        assert member.aliases == ["claude-code"]
        assert member.enabled is True

    def test_missing_type_defaults_to_cli_and_name_to_id(self):
        member = member_from_config(
            {"id": "codex", "command": ["codex", "exec", "{prompt}"]}
        )
        assert member.kind == KIND_CLI
        assert member.name == "codex"

    def test_openai_entry(self):
        member = member_from_config(
            {"id": "gpt-5-api", "type": "openai", "model": "gpt-5"}
        )
        assert member.kind == KIND_OPENAI
        assert member.model == "gpt-5"
        assert member.api_key_env == "OPENAI_API_KEY"

    def test_openai_custom_api_key_env(self):
        member = member_from_config(
            {
                "id": "gpt-5-api",
                "type": "openai",
                "model": "gpt-5",
                "api_key_env": "MY_KEY",
            }
        )
        assert member.api_key_env == "MY_KEY"

    def test_disabled_member_parses(self):
        member = member_from_config(
            {"id": "pi", "command": ["pi", "-p", "{prompt}"], "enabled": False}
        )
        assert member.enabled is False

    @pytest.mark.parametrize(
        "entry, exc, reason",
        [
            (["not", "a", "dict"], TypeError, "object"),
            ({"name": "no id"}, ValueError, "id"),
            ({"id": "x", "type": "opnai"}, ValueError, r"unknown 'type'"),
            ({"id": "x"}, ValueError, "command"),  # cli without any command
            ({"id": "x", "command": ["claude", "-p"]}, ValueError, "placeholder"),
            ({"id": "x", "type": "openai"}, ValueError, "model"),
            (
                {"id": "x", "command": "claude -p {prompt}"},
                TypeError,
                "list of strings",
            ),
            (
                {"id": "x", "type": "openai", "model": "gpt-5", "enabled": "yes"},
                TypeError,
                "boolean",
            ),
            (
                {"id": "x", "type": "openai", "model": "gpt-5", "default": 1},
                TypeError,
                "boolean",
            ),
            (
                {"id": "x", "command": ["c", "{prompt}"], "aliases": "claude"},
                TypeError,
                "aliases",
            ),
            ({"id": "x", "type": "openai", "model": 5}, TypeError, "string"),
        ],
    )
    def test_invalid_entries_raise(self, entry, exc, reason):
        with pytest.raises(exc, match=reason):
            member_from_config(entry)


class TestMemberAvailable:
    def test_cli_on_path(self, fake_which):
        fake_which({"claude"})
        assert member_available(member_from_agent(CLAUDE)) is True

    def test_cli_not_on_path(self, fake_which):
        fake_which(set())
        assert member_available(member_from_agent(CLAUDE)) is False

    def test_openai_needs_no_path(self):
        member = member_from_config({"id": "g", "type": "openai", "model": "gpt-5"})
        assert member_available(member) is True


class TestBuildRoster:
    def test_configured_members_only(self):
        config = {
            "members": [{"id": "claude", "command": ["claude", "-p", "{prompt}"]}]
        }
        roster = build_roster(config, discovered=[])
        assert [m.id for m in roster] == ["claude"]
        assert roster[0].is_default is False

    def test_autodiscovered_appended_as_defaults(self):
        roster = build_roster({}, discovered=[CLAUDE, CODEX])
        assert [m.id for m in roster] == ["claude", "codex"]
        assert all(m.is_default for m in roster)

    def test_configured_wins_id_conflict_with_warning(self, capsys):
        config = {
            "members": [
                {
                    "id": "claude",
                    "name": "My Claude",
                    "command": ["my-claude", "{prompt}"],
                }
            ]
        }
        roster = build_roster(config, discovered=[CLAUDE, CODEX])
        assert [m.id for m in roster] == ["claude", "codex"]
        assert roster[0].name == "My Claude"
        assert roster[0].command == ["my-claude", "{prompt}"]
        assert "already in council.json" in capsys.readouterr().out

    def test_conflict_match_is_case_insensitive(self):
        config = {"members": [{"id": "Claude", "command": ["my-claude", "{prompt}"]}]}
        roster = build_roster(config, discovered=[CLAUDE])
        assert len(roster) == 1
        assert roster[0].name == "Claude"  # the configured member survived

    def test_order_configured_first_then_discovered(self):
        config = {
            "members": [
                {"id": "zeta", "command": ["zeta", "{prompt}"]},
                {"id": "alpha", "command": ["alpha", "{prompt}"]},
            ]
        }
        roster = build_roster(config, discovered=[CODEX, CLAUDE])
        assert [m.id for m in roster] == ["zeta", "alpha", "codex", "claude"]

    def test_broken_entry_skipped_with_warning(self, capsys):
        config = {
            "members": [
                {"id": "broken", "type": "opnai"},
                {"id": "ok", "command": ["ok", "{prompt}"]},
            ]
        }
        roster = build_roster(config, discovered=[])
        assert [m.id for m in roster] == ["ok"]
        assert "members[0] skipped" in capsys.readouterr().out

    def test_members_not_a_list_warns_and_uses_discovered(self, capsys):
        roster = build_roster({"members": "claude"}, discovered=[CLAUDE])
        assert [m.id for m in roster] == ["claude"]
        assert "'members' must be a list" in capsys.readouterr().out

    def test_unavailable_configured_member_stays_in_roster(self, fake_which):
        fake_which(set())
        config = {
            "members": [
                {
                    "id": "claude",
                    "command": ["claude", "-p", "{prompt}"],
                    "default": True,
                }
            ]
        }
        roster = build_roster(config, discovered=[])
        assert len(roster) == 1  # roster is data; availability is selection's concern

    def test_disabled_member_stays_in_roster(self):
        config = {
            "members": [
                {"id": "pi", "command": ["pi", "-p", "{prompt}"], "enabled": False}
            ]
        }
        roster = build_roster(config, discovered=[])
        assert roster[0].enabled is False


class TestAddMemberToConfig:
    """add_member_to_config: the "Add agent" form's backend (web dashboard,
    GUI later). Validates via member_from_config — the same rules the config
    loader itself enforces — then persists to disk. discover_agents() is
    patched throughout so the duplicate-id check doesn't depend on what's
    actually installed on the machine running the tests."""

    def test_appends_and_persists_cli_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [])
        config_path = tmp_path / "council.json"
        config = {}
        entry = {
            "id": "mycli",
            "name": "My CLI",
            "type": "cli",
            "command": ["mycli", "{prompt}"],
            "default": True,
        }

        member = add_member_to_config(config, entry, config_path=str(config_path))

        assert member.id == "mycli"
        assert member.kind == KIND_CLI
        assert member.command == ["mycli", "{prompt}"]
        assert member.is_default is True
        assert config["members"] == [entry]

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["members"] == [entry]

    def test_appends_openai_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [])
        config_path = tmp_path / "council.json"
        entry = {"id": "gpt", "type": "openai", "model": "gpt-5"}

        member = add_member_to_config({}, entry, config_path=str(config_path))

        assert member.kind == KIND_OPENAI
        assert member.model == "gpt-5"

    def test_rejects_duplicate_id_against_configured_member(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [])
        config_path = tmp_path / "council.json"
        config = {
            "members": [{"id": "claude", "command": ["claude", "-p", "{prompt}"]}]
        }
        entry = {"id": "Claude", "type": "cli", "command": ["other", "{prompt}"]}

        with pytest.raises(ValueError, match="already exists"):
            add_member_to_config(config, entry, config_path=str(config_path))
        assert not config_path.exists()  # rejected before any write

    def test_rejects_duplicate_id_against_autodiscovered_agent(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [CLAUDE])
        config_path = tmp_path / "council.json"
        entry = {"id": "claude", "type": "cli", "command": ["other", "{prompt}"]}

        with pytest.raises(ValueError, match="already exists"):
            add_member_to_config({}, entry, config_path=str(config_path))

    def test_invalid_entry_raises_and_does_not_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [])
        config_path = tmp_path / "council.json"
        # Missing the {prompt} placeholder — member_from_config rejects it.
        entry = {"id": "bad", "type": "cli", "command": ["bad", "no placeholder"]}

        with pytest.raises(ValueError):
            add_member_to_config({}, entry, config_path=str(config_path))
        assert not config_path.exists()

    def test_preserves_existing_members_and_other_config_keys(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("src.config.agents.discover_agents", lambda: [])
        config_path = tmp_path / "council.json"
        config = {
            "round3_rotation": 3,
            "members": [{"id": "codex", "command": ["codex", "exec", "{prompt}"]}],
        }
        entry = {"id": "mycli", "type": "cli", "command": ["mycli", "{prompt}"]}

        add_member_to_config(config, entry, config_path=str(config_path))

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["round3_rotation"] == 3
        assert [m["id"] for m in saved["members"]] == ["codex", "mycli"]


class TestSelectMembersBySpec:
    def test_by_id_name_and_alias_case_insensitive(self):
        roster = [
            CouncilMember(id="claude", name="Claude Code", aliases=["anthropic"]),
            member_from_agent(CODEX),
            member_from_agent(GEMINI),
        ]
        result = select_members(roster, "CLAUDE CODE,codex,anthropic")
        assert [m.id for m in result] == ["claude", "codex"]

    def test_roster_order_preserved_not_request_order(self):
        result = select_members(_members(), "codex,claude")
        assert [m.id for m in result] == ["claude", "codex"]

    def test_duplicates_collapse_without_unknown_warning(self, capsys):
        result = select_members(_members(), "claude,Claude Code")
        assert [m.id for m in result] == ["claude"]
        assert "unknown agents" not in capsys.readouterr().out

    def test_unknown_tokens_warn_but_partial_match_kept(self, capsys):
        result = select_members(_members(), "codex,no-such")
        assert [m.id for m in result] == ["codex"]
        assert "unknown agents ignored" in capsys.readouterr().out

    def test_no_match_falls_back_to_all_with_warning(self, capsys):
        roster = _members()
        result = select_members(roster, "no-such-agent")
        assert result == roster
        assert "none of the requested agents" in capsys.readouterr().out

    def test_disabled_member_excluded_with_warning(self, capsys):
        roster = [
            CouncilMember(
                id="pi", name="Pi", command=["pi", "-p", "{prompt}"], enabled=False
            ),
            member_from_agent(CLAUDE),
        ]
        result = select_members(roster, "pi,claude")
        assert [m.id for m in result] == ["claude"]
        assert "disabled" in capsys.readouterr().out

    def test_spec_overrides_default_members(self):
        config = {"default_members": ["codex"]}
        result = select_members(_members(), "claude", config=config)
        assert [m.id for m in result] == ["claude"]


class TestSelectMembersDefault:
    def test_defaults_are_is_default_members(self):
        roster = _members() + [
            CouncilMember(id="private", name="Private", command=["private", "{prompt}"])
        ]
        assert [m.id for m in select_members(roster)] == ["claude", "codex", "gemini"]

    def test_unavailable_cli_default_excluded_with_warning(self, fake_which, capsys):
        fake_which({"codex"})
        roster = [member_from_agent(CLAUDE), member_from_agent(CODEX)]
        assert [m.id for m in select_members(roster)] == ["codex"]
        assert "not available" in capsys.readouterr().out

    def test_disabled_default_excluded(self):
        roster = [
            CouncilMember(
                id="pi",
                name="Pi",
                command=["pi", "-p", "{prompt}"],
                enabled=False,
                is_default=True,
            ),
            member_from_agent(CLAUDE),
        ]
        assert [m.id for m in select_members(roster)] == ["claude"]

    def test_openai_default_needs_no_path(self, fake_which):
        fake_which(set())  # nothing on PATH — the API member still runs
        roster = [
            member_from_config(
                {
                    "id": "gpt-5-api",
                    "type": "openai",
                    "model": "gpt-5",
                    "default": True,
                }
            )
        ]
        assert [m.id for m in select_members(roster)] == ["gpt-5-api"]

    def test_default_members_filters_and_orders(self):
        config = {"default_members": ["gemini", "claude"]}  # reverse roster order
        result = select_members(_members(), config=config)
        assert [m.id for m in result] == ["gemini", "claude"]

    def test_default_members_case_insensitive_and_deduped(self):
        config = {"default_members": ["Codex", "codex"]}
        result = select_members(_members(), config=config)
        assert [m.id for m in result] == ["codex"]

    def test_default_members_missing_ids_warn(self, capsys):
        config = {"default_members": ["claude", "ghost"]}
        result = select_members(_members(), config=config)
        assert [m.id for m in result] == ["claude"]
        assert "ghost" in capsys.readouterr().out

    def test_default_members_all_missing_returns_empty(self, capsys):
        config = {"default_members": ["ghost"]}
        assert select_members(_members(), config=config) == []
        assert "absent from the roster" in capsys.readouterr().out

    def test_default_members_not_a_list_ignored(self, capsys):
        result = select_members(_members(), config={"default_members": "claude"})
        assert [m.id for m in result] == ["claude", "codex", "gemini"]
        assert "must be a list of ids" in capsys.readouterr().out

    def test_nondefault_configured_member_not_in_defaults_even_if_listed(self):
        roster = _members() + [
            CouncilMember(id="private", name="Private", command=["private", "{prompt}"])
        ]
        config = {"default_members": ["private"]}
        assert select_members(roster, config=config) == []


def test_public_reexports():
    """src.config re-exports the roster API (backward-compat surface)."""
    import src.config as config_pkg

    assert config_pkg.build_roster is build_roster
    assert config_pkg.select_members is select_members
    assert config_pkg.member_from_config is member_from_config

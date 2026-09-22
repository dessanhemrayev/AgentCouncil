"""Tests for src/models.py — dataclass AgentResult."""

from src.core.models import AgentResult


class TestAgentResult:
    def test_defaults(self):
        result = AgentResult(name="A")

        assert result.name == "A"
        assert result.output == ""
        assert result.error is None

    def test_full_construction(self):
        result = AgentResult(name="A", output="ответ", error="ошибка")

        assert result.name == "A"
        assert result.output == "ответ"
        assert result.error == "ошибка"

    def test_error_without_output(self):
        result = AgentResult(name="A", error="упс")

        assert result.output == ""
        assert result.error == "упс"

    def test_equality(self):
        assert AgentResult(name="A", output="x") == AgentResult(name="A", output="x")
        assert AgentResult(name="A") != AgentResult(name="B")

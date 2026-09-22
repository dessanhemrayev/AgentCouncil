"""Tests for Markdown verdict rendering (src/gui/mdview.py): tk.Text tags.

The verdict must be displayed with typography, not raw md: markup markers
(**, ```, |---|) do not appear in text but remain as heading/bold/code/
quote/table/link tags.
"""

import pytest

ctk = pytest.importorskip("customtkinter", exc_type=ImportError)

from src.gui import mdview  # noqa: E402


@pytest.fixture(scope="module")
def ctk_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


def _render(root, md: str):
    box = ctk.CTkTextbox(root, wrap="word")
    mdview.render(box, md)
    return box._textbox


class TestMarkdownRender:
    def test_inline_and_headings(self, ctk_root):
        inner = _render(
            ctk_root,
            "# Вердикт\n\nИтог: **APPROVED** и `код`, просто *акцент*.\n",
        )

        content = inner.get("1.0", "end")
        assert "**" not in content and "Вердикт" in content
        assert inner.tag_ranges("h1") and inner.tag_ranges("bold")
        assert inner.tag_ranges("code") and inner.tag_ranges("italic")

    def test_lists_quotes_codeblocks_hr(self, ctk_root):
        inner = _render(
            ctk_root,
            "- пункт один\n- пункт два\n\n> цитата\n\n"
            "```python\nprint('hi')\n```\n\n---\n1. первый\n",
        )

        content = inner.get("1.0", "end")
        assert "• пункт один" in content
        assert "```" not in content and "print('hi')" in content
        assert inner.tag_ranges("quote") and inner.tag_ranges("codeblock")
        assert inner.tag_ranges("hr")
        assert "1. первый" in content

    def test_table_renders_without_pipes(self, ctk_root):
        inner = _render(
            ctk_root,
            "| Агент | Голос |\n|---|---|\n| A | да |\n| B | нет |\n",
        )

        content = inner.get("1.0", "end")
        assert "|" not in content  # сырой синтаксис таблиц не виден
        assert "Агент" in content and "Голос" in content
        assert inner.tag_ranges("tablehead") and inner.tag_ranges("table")

    def test_links_clickable_tag_registered(self, ctk_root):
        inner = _render(ctk_root, "См. [документацию](https://example.com/doc) ниже.\n")

        content = inner.get("1.0", "end")
        assert "](https://example.com/doc)" not in content
        assert "документацию" in content
        assert inner.tag_ranges("link:https://example.com/doc")

    def test_snake_case_not_eaten_by_italic(self, ctk_root):
        inner = _render(ctk_root, "Файл my_config_file.yaml изменён.\n")

        assert "my_config_file.yaml" in inner.get("1.0", "end")
        assert not inner.tag_ranges("italic")

    def test_checkbox_glyphs(self, ctk_root):
        inner = _render(ctk_root, "- [x] сделано\n- [ ] не сделано\n")

        content = inner.get("1.0", "end")
        assert "☑ сделано" in content and "☐ не сделано" in content

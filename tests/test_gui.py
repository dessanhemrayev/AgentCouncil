"""Tests for src/gui.py — only what doesn't require a real council
run or a visible window: widget construction, QueueWriter, formatting.
"""

import queue

import pytest

ctk = pytest.importorskip("customtkinter", exc_type=ImportError)

from src.gui import CouncilGUI, QueueWriter  # noqa: E402


@pytest.fixture(scope="module")
def ctk_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def hidden_root(ctk_root):
    top = ctk.CTkToplevel(ctk_root)
    top.withdraw()
    yield top
    top.destroy()


class TestQueueWriter:
    def test_splits_on_newlines(self):
        q: "queue.Queue[str]" = queue.Queue()
        writer = QueueWriter(q)

        writer.write("line1\nline2\n")

        assert q.get_nowait() == "line1"
        assert q.get_nowait() == "line2"
        assert q.empty()

    def test_holds_partial_line_until_newline(self):
        q: "queue.Queue[str]" = queue.Queue()
        writer = QueueWriter(q)

        writer.write("partial")
        assert q.empty()

        writer.write(" rest\n")
        assert q.get_nowait() == "partial rest"

    def test_flush_is_noop(self):
        writer = QueueWriter(queue.Queue())
        writer.flush()


class TestCouncilGUIBuild:
    def test_builds_without_error(self, hidden_root):
        app = CouncilGUI(hidden_root)

        assert app.task_text is not None
        assert app.log_text is not None
        assert app.run_button is not None

    def test_agents_checkboxes_match_discovered(self, hidden_root):
        app = CouncilGUI(hidden_root)

        assert set(app.agent_vars) == {member.name for member in app.discovered_agents}

    def test_all_agents_selected_by_default(self, hidden_root):
        app = CouncilGUI(hidden_root)

        # Pre-checked = is_default (true for every autodiscovered PATH agent,
        # see member_from_agent) — not a blanket True: council.json can also
        # add explicitly non-default members (src/gui/sidebar.py).
        selected = [name for name, var in app.agent_vars.items() if var.get()]
        assert set(selected) == {member.name for member in app.discovered_agents}

    def test_preset_default_is_none(self, hidden_root):
        app = CouncilGUI(hidden_root)

        assert app.preset_var.get() == "none"

    def test_task_mode_default_false(self, hidden_root):
        app = CouncilGUI(hidden_root)

        assert app.task_mode_var.get() is False

    def test_evidence_label_default(self, hidden_root):
        app = CouncilGUI(hidden_root)

        assert "не прикреплены" in app.evidence_label.cget("text")

    def test_switch_to_english_updates_interface(self, hidden_root):
        app = CouncilGUI(hidden_root)

        app.set_language("en")

        assert app.language == "en"
        assert app.task_header_label.cget("text") == "Task"
        assert app.run_button.cget("text") == "Run council"
        assert app.evidence_label.cget("text") == "No files attached"
        assert list(app.language_menu.cget("values")) == ["Русский", "English"]

    def test_context_menu_targets_inner_textbox(self, hidden_root):
        """Menu items must work with the inner tk.Text CTkTextbox.

        Regression: event_generate("<<Paste>>") on the CTk frame silently did
        nothing — virtual events handle class bindings on the inner
        tk.Text, not on the frame.
        """
        app = CouncilGUI(hidden_root)

        import tkinter as tk

        assert isinstance(app._task_inner_text, tk.Text)

        # Insert via the inner widget actually puts text from the clipboard.
        # The window must be visible (mapped): withdrawn Tk windows don't
        # deliver virtual events or focus.
        hidden_root.deiconify()
        hidden_root.update()
        hidden_root.clipboard_clear()
        hidden_root.clipboard_append("CTX_PASTE_PROBE")
        app.task_text.delete("1.0", "end")
        app._task_inner_text.focus_set()
        app._task_inner_text.mark_set("insert", "1.0")
        app._task_inner_text.event_generate("<<Paste>>")
        hidden_root.update()
        hidden_root.withdraw()
        assert "CTX_PASTE_PROBE" in app.task_text.get("1.0", "end")

    @staticmethod
    def _all_texts(widget, out):
        try:
            out.append(widget.cget("text"))
        except Exception:
            pass
        for child in widget.winfo_children():
            TestCouncilGUIBuild._all_texts(child, out)

    def test_task_buttons_exist(self, hidden_root):
        """The "From file"/"Clear" buttons exist in input_frame.

        Regression: without grid_rowconfigure(0, weight=1) the textbox row's
        buttons were pushed out of the frame and cut off when vertical
        space ran out.
        """
        app = CouncilGUI(hidden_root)

        texts = []
        self._all_texts(app.task_text.master, texts)
        assert "Из файла" in texts
        assert "Очистить" in texts

    def test_evidence_add_dedup_and_clear(self, hidden_root, tmp_path, monkeypatch):
        """Evidence: dedup by name, show names, clear."""
        app = CouncilGUI(hidden_root)

        f1 = tmp_path / "a.md"
        f2 = tmp_path / "b.txt"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("y", encoding="utf-8")
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilenames",
            lambda **kwargs: (str(f1), str(f2)),
        )

        app._add_evidence_files()

        assert [p.name for p in app.evidence_files] == ["a.md", "b.txt"]
        assert "a.md" in app.evidence_label.cget("text")
        assert "Прикреплено файлов: 2" in app.evidence_label.cget("text")

        # Duplicate by name (from another folder) silently doesn't overwrite —
        # skipped with an informational dialog (we patch it so it doesn't interfere).
        f1_dup = tmp_path / "sub"
        f1_dup.mkdir()
        (f1_dup / "a.md").write_text("z", encoding="utf-8")
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilenames",
            lambda **kwargs: (str(f1_dup / "a.md"),),
        )
        monkeypatch.setattr("tkinter.messagebox.showinfo", lambda *a, **kw: None)

        app._add_evidence_files()

        assert [p.name for p in app.evidence_files] == ["a.md", "b.txt"]

        app._clear_evidence_files()

        assert app.evidence_files == []
        assert "не прикреплены" in app.evidence_label.cget("text")

    def test_change_theme_maps_russian_labels(self, hidden_root):
        """Russian theme labels map to customtkinter modes."""
        app = CouncilGUI(hidden_root)

        app._change_theme("Тёмная")
        assert str(ctk.get_appearance_mode()).lower() == "dark"

        app._change_theme("Светлая")
        assert str(ctk.get_appearance_mode()).lower() == "light"

        app._change_theme("system")
        assert str(ctk.get_appearance_mode()).lower() in ("system", "light", "dark")

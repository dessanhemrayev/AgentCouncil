"""GUI AgentCouncil — CustomTkinter, modern design.

Entry point (`python -m src.gui.app`) and the thin shell of CouncilGUI:
state, builder wiring (sidebar/main_panel), log queue, worker start.
Layout is outsourced to builder functions (sidebar.py / main_panel.py), worker is in
worker.py (CouncilWorker), verdict window is in verdict_window.py, bootstrapper is
in launcher.py. CouncilGUI remains a single Tkinter widget with shared
state (self.task_text, self.agent_vars, ...) — it should not be split into separate
classes: builders take gui and build inside its frames.

Terminology: "Task" instead of "Idea" — universal across all modes.
"""

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import customtkinter as ctk

from ..config.agents import build_roster, discover_agents, load_config
from ..core.models import CouncilMember
from . import pipeline
from .i18n import LANGUAGE_LABELS, SUPPORTED_LANGUAGES, translate
from .main_panel import build_main, load_task_from_file
from .palette import APP_BG, PANEL_ALT_BG, SIDEBAR_BG, STATUS_BADGE
from .sidebar import build_sidebar, theme_mode_from_label
from .verdict_window import show_verdict
from .worker import CouncilWorker


class CouncilGUI:
    STATUS_COLORS = {
        "idle": STATUS_BADGE["idle"][1],
        "running": STATUS_BADGE["running"][1],
        "done": STATUS_BADGE["done"][1],
        "error": STATUS_BADGE["error"][1],
        "timeout": STATUS_BADGE["timeout"][1],
    }

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.language = "ru"
        self.language_var: ctk.StringVar
        self.theme_var: ctk.StringVar
        self.sidebar_header_subtitle: ctk.CTkLabel
        self.agents_label: ctk.CTkLabel
        self.no_agents_label: Optional[ctk.CTkLabel]
        self.preset_label: ctk.CTkLabel
        self.preset_buttons: Dict[str, ctk.CTkRadioButton]
        self.mode_label: ctk.CTkLabel
        self.task_mode_checkbox: ctk.CTkCheckBox
        self.task_mode_help_label: ctk.CTkLabel
        self.timeout_label: ctk.CTkLabel
        self.timeout_help_label: ctk.CTkLabel
        self.evidence_label_title: ctk.CTkLabel
        self.add_evidence_button: ctk.CTkButton
        self.add_url_button: ctk.CTkButton
        self.clear_evidence_button: ctk.CTkButton
        self.theme_label: ctk.CTkLabel
        self.theme_menu: ctk.CTkOptionMenu
        self.language_label: ctk.CTkLabel
        self.language_menu: ctk.CTkOptionMenu
        self.task_header_label: ctk.CTkLabel
        self.task_help_label: ctk.CTkLabel
        self.load_task_button: ctk.CTkButton
        self.clear_task_button: ctk.CTkButton
        self.log_label: ctk.CTkLabel
        self.pipeline_header_labels: Dict[str, ctk.CTkLabel]
        self._verdict_window_updates: List = []
        self.root.title("AgentCouncil")
        self.root.geometry("1280x860")
        self.root.minsize(980, 680)
        self.root.configure(fg_color=APP_BG)

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.evidence_files: List[Union[Path, str]] = []
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.session_dir: Optional[Path] = None
        # build_roster(): council.json members[] (including "openai" API
        # members) + autodiscovered known CLIs on PATH — same roster the web
        # dashboard uses, so an agent added to council.json shows up here too.
        self.discovered_agents: List[CouncilMember] = build_roster(
            load_config(), discover_agents()
        )
        self.agent_vars: Dict[str, ctk.BooleanVar] = {}
        self.agent_status: Dict[str, str] = {}
        self.agent_status_labels: Dict[str, ctk.CTkLabel] = {}

        # Pipeline state (pipeline.py): "Agent x Stage" matrix.
        self.current_stage: Optional[str] = None
        self.call_done: int = 0
        self.call_total: int = 0
        self.cell_started: Dict[Tuple[str, str], float] = {}
        self.cell_status: Dict[Tuple[str, str], str] = {}
        self.run_started_at: Optional[float] = None

        # Widget attributes below are assigned by the builders
        # (sidebar.py / main_panel.py); declared for mypy.
        self.preset_var: ctk.StringVar
        self.task_mode_var: ctk.BooleanVar
        self.round_timeout_var: ctk.StringVar
        self.evidence_label: ctk.CTkLabel
        self.task_text: ctk.CTkTextbox
        self._task_context_menu: tk.Menu
        self._task_inner_text: tk.Text
        self.run_button: ctk.CTkButton
        self.pipeline_frame: ctk.CTkFrame
        self.stage_chip_labels: Dict[str, ctk.CTkLabel]
        self.chip_labels: Dict[str, str]  # {stage: label} — depend on the mode
        self.matrix_frame: ctk.CTkFrame
        self.agent_cells: Dict[Tuple[str, str], ctk.CTkLabel]
        self.pipeline_columns: List[str]
        self.progress: ctk.CTkProgressBar
        self.progress_label: ctk.CTkLabel
        self.log_text: ctk.CTkTextbox

        self._build_widgets()
        self._poll_log_queue()

    def tr(self, key: str, **values: object) -> str:
        """Translate an interface string into the current language."""
        return translate(self.language, key, **values)

    def set_language(self, language: str) -> None:
        """Switch the interface language and refresh all existing widgets."""
        if language not in SUPPORTED_LANGUAGES:
            language = next(
                (code for code, label in LANGUAGE_LABELS.items() if label == language),
                "ru",
            )
        self.language = language
        if hasattr(self, "language_var"):
            self.language_var.set(LANGUAGE_LABELS[language])
        self._refresh_texts()

    def _refresh_texts(self) -> None:
        """Refresh localized text on widgets that were already created."""
        if not hasattr(self, "sidebar_header_subtitle"):
            return

        self.sidebar_header_subtitle.configure(text=self.tr("app_subtitle"))
        self.agents_label.configure(text=self.tr("agents"))
        if self.no_agents_label is not None:
            self.no_agents_label.configure(text=self.tr("no_agents"))
        self.preset_label.configure(text=self.tr("preset"))
        for value, button in self.preset_buttons.items():
            button.configure(
                text=self.tr(
                    {
                        "none": "preset_none",
                        "hypothesis": "preset_hypothesis",
                        "paper": "preset_paper",
                        "lit-review": "preset_lit_review",
                    }[value]
                )
            )
        self.mode_label.configure(text=self.tr("mode"))
        self.task_mode_checkbox.configure(text=self.tr("task_mode"))
        self.task_mode_help_label.configure(text=self.tr("task_mode_help"))
        self.timeout_label.configure(text=self.tr("agent_timeout"))
        self.timeout_help_label.configure(text=self.tr("timeout_help"))
        self.evidence_label_title.configure(text=self.tr("evidence"))
        self.add_evidence_button.configure(text=self.tr("add_files"))
        self.add_url_button.configure(text=self.tr("add_url"))
        self.clear_evidence_button.configure(text=self.tr("clear_files"))
        self.theme_label.configure(text=self.tr("theme"))
        theme_mode = theme_mode_from_label(self, self.theme_var.get())
        self.theme_menu.configure(
            values=[self.tr(f"theme_{mode}") for mode in ("system", "light", "dark")]
        )
        self.theme_var.set(self.tr(f"theme_{theme_mode}"))
        self.language_label.configure(text=self.tr("language"))
        self.language_menu.configure(
            values=[self.tr("language_ru"), self.tr("language_en")]
        )

        self.task_header_label.configure(text=self.tr("task"))
        self.task_help_label.configure(text=self.tr("task_help"))
        self.load_task_button.configure(text=self.tr("load_file"))
        self.clear_task_button.configure(text=self.tr("clear"))
        self.log_label.configure(text=self.tr("execution_log"))
        for index, key in enumerate(("cut", "copy", "paste")):
            self._task_context_menu.entryconfigure(index, label=self.tr(key))
        self._task_context_menu.entryconfigure(4, label=self.tr("select_all"))
        self._task_context_menu.entryconfigure(5, label=self.tr("clear"))

        pipeline.refresh_language(self)
        self._update_evidence_label()
        self._update_run_button()
        for update in list(self._verdict_window_updates):
            update()

    def _update_run_button(self) -> None:
        """Update the run button label for the current language and state."""
        if hasattr(self, "run_button"):
            text = (
                self.tr("running")
                if self.worker is not None and self.worker.is_alive()
                else self.tr("run_council")
            )
            self.run_button.configure(text=text)

    def _build_widgets(self) -> None:
        self.root.grid_columnconfigure(0, weight=0, minsize=308)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkScrollableFrame(
            self.root, width=308, corner_radius=0, fg_color=SIDEBAR_BG
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        build_sidebar(self)

        self.main_frame = ctk.CTkFrame(
            self.root, corner_radius=0, fg_color=PANEL_ALT_BG
        )
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=0)
        self.main_frame.grid_rowconfigure(1, weight=3)
        self.main_frame.grid_rowconfigure(2, weight=0)
        self.main_frame.grid_rowconfigure(3, weight=0)  # pipeline
        self.main_frame.grid_rowconfigure(4, weight=2)  # log
        # Call build_main once — a second call would recreate widgets
        # and hide the first layer (bug from the original flat gui.py).
        build_main(self)

    def _change_theme(self, label: str) -> None:
        """Map a localized theme label to a customtkinter mode."""
        ctk.set_appearance_mode(theme_mode_from_label(self, label))

    def _update_evidence_label(self) -> None:
        """Redraw the Evidence block label (count and file names)."""
        if not self.evidence_files:
            self.evidence_label.configure(text=self.tr("no_evidence"))
            return
        names = "\n".join(
            p.name if isinstance(p, Path) else p for p in self.evidence_files
        )
        self.evidence_label.configure(
            text=self.tr("attached_files", count=len(self.evidence_files), names=names)
        )

    def _add_evidence_files(self) -> None:
        from tkinter import filedialog, messagebox

        paths = filedialog.askopenfilenames(
            title=self.tr("select_files"),
            filetypes=[
                ("Text/Markdown", "*.md *.txt"),
                ("Documents", "*.pdf *.docx *.doc"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        existing = {p.name for p in self.evidence_files if isinstance(p, Path)}
        duplicates = []
        for raw in paths:
            path = Path(raw)
            # Same-named files from different folders would overwrite each other.
            if path.name in existing:
                duplicates.append(path.name)
                continue
            existing.add(path.name)
            self.evidence_files.append(path)
        self._update_evidence_label()
        if duplicates:
            messagebox.showinfo(
                self.tr("duplicate_title"),
                self.tr("duplicate_message", files="\n".join(duplicates)),
                parent=self.root,
            )

    def _add_evidence_url(self) -> None:
        from tkinter import simpledialog, messagebox

        url = simpledialog.askstring(
            self.tr("add_url"),
            self.tr("enter_url"),
            parent=self.root,
        )
        if not url:
            return
        url = url.strip()
        if not url:
            return
        # Basic URL validation
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror(
                self.tr("error"),
                self.tr("invalid_url"),
                parent=self.root,
            )
            return
        if url in self.evidence_files:
            messagebox.showinfo(
                self.tr("duplicate_title"),
                self.tr("duplicate_message", files=url),
                parent=self.root,
            )
            return
        self.evidence_files.append(url)
        self._update_evidence_label()

    def _clear_evidence_files(self) -> None:
        self.evidence_files.clear()
        self._update_evidence_label()

    def _load_task_from_file(self) -> None:
        load_task_from_file(self)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _update_agent_status(
        self, agent_name: str, status: str, stage: Optional[str] = None
    ) -> None:
        """Update agent status (thread-safe via root.after).

        stage — the stage on which the call fired (round1/round2/round3/task*).
        Updates the sidebar dot, the "Agent x Stage" matrix cell, and the
        completed call counter for deterministic progress.
        """

        def _do_update():
            self.agent_status[agent_name] = status
            if agent_name in self.agent_status_labels:
                color = self.STATUS_COLORS.get(status, "gray")
                self.agent_status_labels[agent_name].configure(text_color=color)

            if stage is None:
                return
            key = (agent_name, stage)
            self.cell_status[key] = status
            if status == "running":
                if stage.startswith("task"):
                    # Task-mode work/review is dynamic: each new call grows
                    # the progress denominator.
                    self.call_total += 1
                self.cell_started[key] = time.monotonic()
                pipeline.update_agent_cell(self, agent_name, stage, "running")
            elif status in ("done", "error", "timeout"):
                self.call_done += 1
                detail = ""
                if key in self.cell_started:
                    detail = self.tr(
                        "seconds",
                        seconds=int(time.monotonic() - self.cell_started[key]),
                    )
                    del self.cell_started[key]
                pipeline.update_agent_cell(self, agent_name, stage, status, detail)
                self._refresh_progress()

        self.root.after(0, _do_update)  # callback may come from the worker thread

    def _on_stage_change(self, stage: str) -> None:
        """Transition orchestration to a new stage (thread-safe via root.after)."""

        def _do_stage():
            self.current_stage = stage
            pipeline.set_stage(self, stage)

        self.root.after(0, _do_stage)

    def _refresh_progress(self) -> None:
        """Recalculates the deterministic progress bar."""
        elapsed_text = ""
        if self.run_started_at is not None:
            total_sec = int(time.monotonic() - self.run_started_at)
            minutes, sec = divmod(total_sec, 60)
            elapsed_text = (
                self.tr("minutes_seconds", minutes=minutes, seconds=sec)
                if minutes
                else self.tr("seconds", seconds=sec)
            )
        pipeline.set_progress(self, self.call_done, self.call_total, elapsed_text)

    def _tick_progress(self) -> None:
        """Ticker (500ms): live cell "running" timers and overall run timer.

        After the worker thread dies, one final tick runs
        (to render the last cell states), and only then does the chain
        stop — otherwise the "running" timers would freeze mid-render.
        """
        now = time.monotonic()
        for key, started in list(self.cell_started.items()):
            if self.cell_status.get(key) == "running":
                agent_name, stage = key
                pipeline.update_agent_cell(
                    self,
                    agent_name,
                    stage,
                    "running",
                    self.tr("seconds", seconds=int(now - started)),
                )
        self._refresh_progress()
        if self.worker is not None and self.worker.is_alive():
            self.root.after(500, self._tick_progress)

    def _reset_agent_status(self) -> None:
        """Reset all agent status indicators to idle."""

        def _do_reset():
            for name in self.agent_status:
                self.agent_status[name] = "idle"
                if name in self.agent_status_labels:
                    self.agent_status_labels[name].configure(
                        text_color=self.STATUS_COLORS["idle"]
                    )

        self.root.after(0, _do_reset)

    def _start_council(self) -> None:
        from tkinter import messagebox

        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo(
                self.tr("already_running_title"),
                self.tr("already_running_message"),
                parent=self.root,
            )
            return

        task = self.task_text.get("1.0", "end").strip()
        if not task:
            messagebox.showwarning(
                self.tr("empty_task_title"),
                self.tr("empty_task_message"),
                parent=self.root,
            )
            return

        # Explicit check: ctk.BooleanVar() here would create an orphaned
        # tk variable for each missing agent.
        selected = [
            member
            for member in self.discovered_agents
            if member.name in self.agent_vars and self.agent_vars[member.name].get()
        ]
        if len(selected) < 1:
            messagebox.showwarning(
                self.tr("no_agents_title"),
                self.tr("no_agents_message"),
                parent=self.root,
            )
            return

        task_mode = self.task_mode_var.get()

        pipeline.set_pipeline_agents(
            self, [member.name for member in selected], task_mode=task_mode
        )
        self.call_done = 0
        if task_mode:
            # Minimum denominator: R1(N) + R2(N) + executor (1) + work (1)
            # + 1 review iteration (N-1) = 3N+1; review cycles may add more.
            self.call_total = 3 * len(selected) + 1
        else:
            self.call_total = 3 * len(selected)
        self.cell_status = {}
        self.cell_started = {}
        self.current_stage = None
        self.run_started_at = time.monotonic()
        self.progress.set(0)
        self.progress_label.configure(text="")

        self.run_button.configure(state="disabled", text=self.tr("running"))
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._reset_agent_status()
        self._refresh_progress()

        self.worker = threading.Thread(
            target=CouncilWorker(
                self.log_queue,
                self._update_agent_status,
                self._on_council_finished,
                self._on_stage_change,
            ).run,
            args=(self, task, selected),
            daemon=True,
        )
        self.worker.start()
        self._tick_progress()

    def _on_council_finished(self, session_dir: Optional[Path]) -> None:
        from tkinter import messagebox

        self.run_button.configure(state="normal", text=self.tr("run_council"))

        if session_dir is None:
            # Failure: chips orange, progress not 100% — an error must not
            # look like a success.
            pipeline.mark_all_done(self, ok=False)
            self._refresh_progress()
            messagebox.showerror(
                self.tr("error"),
                self.tr("council_failed"),
                parent=self.root,
            )
            return

        pipeline.mark_all_done(self)
        self.call_done = self.call_total
        self._refresh_progress()

        # Single verdict window — external auto-open was removed (it duplicated
        # the display: window + system editor at once).
        task_verdict = session_dir / "task-verdict.md"
        verdict = session_dir / "verdict.md"
        if task_verdict.exists():
            show_verdict(self, task_verdict)
        elif verdict.exists():
            show_verdict(self, verdict)


def main() -> None:
    root = ctk.CTk()
    CouncilGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

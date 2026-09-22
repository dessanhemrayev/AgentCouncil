"""Building the GUI main panel (task field, launch buttons, log panel)."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from .palette import ACCENT, ACCENT_HOVER, FIELD_BG, MUTED, PANEL_BG, TEXT
from .pipeline import build_pipeline

if TYPE_CHECKING:
    from .app import CouncilGUI


def build_main(gui: "CouncilGUI") -> None:
    header = ctk.CTkFrame(gui.main_frame, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 8))
    gui.task_header_label = ctk.CTkLabel(
        header,
        text=gui.tr("task"),
        text_color=TEXT,
        font=ctk.CTkFont(size=22, weight="bold"),
    )
    gui.task_header_label.pack(anchor="w")
    gui.task_help_label = ctk.CTkLabel(
        header,
        text=gui.tr("task_help"),
        text_color=MUTED,
        font=ctk.CTkFont(size=13),
    )
    gui.task_help_label.pack(anchor="w")

    input_frame = ctk.CTkFrame(gui.main_frame, corner_radius=8, fg_color=PANEL_BG)
    input_frame.grid(row=1, column=0, sticky="nsew", padx=28, pady=(8, 12))
    input_frame.grid_columnconfigure(0, weight=1)
    input_frame.grid_rowconfigure(0, weight=1)

    gui.task_text = ctk.CTkTextbox(
        input_frame,
        height=250,
        corner_radius=6,
        fg_color=FIELD_BG,
        text_color=TEXT,
        border_width=1,
        font=ctk.CTkFont(size=14),
        wrap="word",
    )
    gui.task_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))

    gui._task_inner_text = gui.task_text._textbox
    gui._task_context_menu = tk.Menu(gui.task_text, tearoff=0)
    gui._task_context_menu.add_command(
        label=gui.tr("cut"),
        command=lambda: gui._task_inner_text.event_generate("<<Cut>>"),
    )
    gui._task_context_menu.add_command(
        label=gui.tr("copy"),
        command=lambda: gui._task_inner_text.event_generate("<<Copy>>"),
    )
    gui._task_context_menu.add_command(
        label=gui.tr("paste"),
        command=lambda: gui._task_inner_text.event_generate("<<Paste>>"),
    )
    gui._task_context_menu.add_separator()
    gui._task_context_menu.add_command(
        label=gui.tr("select_all"),
        command=lambda: gui._task_inner_text.tag_add("sel", "1.0", "end"),
    )
    gui._task_context_menu.add_command(
        label=gui.tr("clear"), command=lambda: gui.task_text.delete("1.0", "end")
    )

    def _show_context_menu(event):
        try:
            gui._task_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            gui._task_context_menu.grab_release()

    gui.task_text.bind("<Button-3>", _show_context_menu)
    gui.task_text.bind("<Button-2>", _show_context_menu)

    btn_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
    btn_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 12))
    gui.load_task_button = ctk.CTkButton(
        btn_frame,
        text=gui.tr("load_file"),
        command=gui._load_task_from_file,
        width=104,
        height=32,
        corner_radius=6,
        font=ctk.CTkFont(size=11),
    )
    gui.load_task_button.pack(side="left")
    gui.clear_task_button = ctk.CTkButton(
        btn_frame,
        text=gui.tr("clear"),
        command=lambda: gui.task_text.delete("1.0", "end"),
        width=104,
        height=32,
        corner_radius=6,
        fg_color="transparent",
        border_width=1,
        font=ctk.CTkFont(size=11),
    )
    gui.clear_task_button.pack(side="left", padx=(10, 0))

    gui.run_button = ctk.CTkButton(
        gui.main_frame,
        text=gui.tr("run_council"),
        command=gui._start_council,
        height=46,
        corner_radius=8,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.run_button.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 12))

    gui.pipeline_frame = ctk.CTkFrame(
        gui.main_frame, corner_radius=8, fg_color=PANEL_BG
    )
    gui.pipeline_frame.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 12))
    build_pipeline(gui)

    log_frame = ctk.CTkFrame(gui.main_frame, corner_radius=8, fg_color=PANEL_BG)
    log_frame.grid(row=4, column=0, sticky="nsew", padx=28, pady=(0, 28))
    log_frame.grid_columnconfigure(0, weight=1)
    log_frame.grid_rowconfigure(1, weight=1)

    gui.log_label = ctk.CTkLabel(
        log_frame,
        text=gui.tr("execution_log"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.log_label.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))
    gui.log_text = ctk.CTkTextbox(
        log_frame,
        height=132,
        corner_radius=6,
        fg_color=FIELD_BG,
        text_color=TEXT,
        font=ctk.CTkFont(size=12),
        wrap="word",
        state="disabled",
    )
    gui.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))


def load_task_from_file(gui: "CouncilGUI") -> None:
    path = filedialog.askopenfilename(title=gui.tr("select_file"))
    if not path:
        return
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        messagebox.showerror(gui.tr("file_read_error"), str(exc), parent=gui.root)
        return
    gui.task_text.delete("1.0", "end")
    gui.task_text.insert("1.0", text)

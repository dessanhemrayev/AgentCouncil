"""GUI verdict window: in-app markdown viewer for the result file."""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from . import mdview
from .palette import ACCENT, ACCENT_HOVER, FIELD_BG, MUTED, PANEL_ALT_BG, TEXT

if TYPE_CHECKING:
    from .app import CouncilGUI


def _open_in_shell(path: Path) -> bool:
    """Open a file or folder with the system handler (Windows os.startfile)."""
    if sys.platform != "win32" or not hasattr(os, "startfile"):
        return False
    try:
        os.startfile(path)
    except Exception:
        return False
    return True


def show_verdict(gui: "CouncilGUI", path: Path) -> None:
    from tkinter import messagebox

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        messagebox.showerror(
            gui.tr("verdict_unavailable"),
            gui.tr("verdict_read_error", filename=path.name, error=exc),
            parent=gui.root,
        )
        return

    window = ctk.CTkToplevel(gui.root)
    window.title(gui.tr("verdict"))
    window.geometry("860x680")
    window.configure(fg_color=PANEL_ALT_BG)
    window.transient(gui.root)
    window.after(80, window.lift)

    body = ctk.CTkFrame(window, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=10, pady=(10, 0))
    body.grid_rowconfigure(0, weight=1)
    body.grid_columnconfigure(0, weight=1)

    btn_src: ctk.CTkButton
    textbox = ctk.CTkTextbox(
        body,
        wrap="word",
        fg_color=FIELD_BG,
        text_color=TEXT,
        font=ctk.CTkFont(size=13),
        corner_radius=6,
    )
    textbox.grid(row=0, column=0, sticky="nsew")
    mdview.render(textbox, text)
    textbox.configure(state="disabled")

    btn_frame = ctk.CTkFrame(window, fg_color="transparent")
    btn_frame.pack(fill="x", padx=10, pady=(6, 10))
    source_mode = False

    def _toggle_source() -> None:
        """Toggle between rendered markdown and raw source."""
        nonlocal source_mode
        inner = textbox._textbox
        inner.delete("1.0", "end")
        if source_mode:
            inner.insert("1.0", text)
            btn_src.configure(text=gui.tr("render"))
        else:
            mdview.render(textbox, text)
            btn_src.configure(text=gui.tr("source"))
        source_mode = not source_mode
        textbox.configure(state="disabled")

    btn_src = ctk.CTkButton(
        btn_frame,
        text=gui.tr("source"),
        command=_toggle_source,
        width=110,
        height=28,
        corner_radius=6,
        fg_color="transparent",
        border_width=1,
        font=ctk.CTkFont(size=11),
    )
    btn_src.pack(side="left")

    btn_copy = ctk.CTkButton(
        btn_frame,
        text=gui.tr("copy"),
        command=lambda: (
            window.clipboard_clear(),
            window.clipboard_append(text),
        ),
        width=110,
        height=28,
        corner_radius=6,
        fg_color="transparent",
        border_width=1,
        font=ctk.CTkFont(size=11),
    )
    btn_copy.pack(side="left", padx=(8, 0))

    def _open(target: Path, what_key: str) -> None:
        if not _open_in_shell(target):
            messagebox.showerror(
                gui.tr("open_failed"),
                gui.tr(
                    "open_failed_message",
                    what=gui.tr(what_key),
                    path=target,
                ),
                parent=window,
            )

    btn_editor = ctk.CTkButton(
        btn_frame,
        text=gui.tr("open_editor"),
        command=lambda: _open(path, "file"),
        width=150,
        corner_radius=6,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
    )
    btn_editor.pack(side="right")
    btn_folder = ctk.CTkButton(
        btn_frame,
        text=gui.tr("open_session_folder"),
        command=lambda: _open(path.parent, "folder"),
        width=150,
        corner_radius=6,
        fg_color="transparent",
        border_width=1,
        text_color=MUTED,
    )
    btn_folder.pack(side="right", padx=(0, 8))

    def _update_texts() -> None:
        """Refresh this window when the main interface language changes."""
        try:
            if not window.winfo_exists():
                return
        except Exception:
            return
        window.title(gui.tr("verdict"))
        btn_src.configure(text=gui.tr("source" if source_mode else "render"))
        btn_copy.configure(text=gui.tr("copy"))
        btn_editor.configure(text=gui.tr("open_editor"))
        btn_folder.configure(text=gui.tr("open_session_folder"))

    def _unregister(_event=None) -> None:
        if _update_texts in gui._verdict_window_updates:
            gui._verdict_window_updates.remove(_update_texts)

    gui._verdict_window_updates.append(_update_texts)
    window.bind("<Destroy>", _unregister)

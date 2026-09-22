"""Building the GUI sidebar (agents/presets/mode/timeout/theme/language)."""

from typing import TYPE_CHECKING

import customtkinter as ctk

from ..config.agents import member_available
from ..core.models import KIND_OPENAI
from .palette import ACCENT, ACCENT_HOVER, MUTED, STATUS_BADGE, TEXT

if TYPE_CHECKING:
    from .app import CouncilGUI

PRESET_VALUES = ("none", "hypothesis", "paper", "lit-review")
PRESET_LABEL_KEYS = {
    "none": "preset_none",
    "hypothesis": "preset_hypothesis",
    "paper": "preset_paper",
    "lit-review": "preset_lit_review",
}

THEME_MODES = ("system", "light", "dark")
THEME_LABEL_KEYS = {
    "system": "theme_system",
    "light": "theme_light",
    "dark": "theme_dark",
}
THEME_LABELS_TO_MODE = {
    "Системная": "system",
    "Светлая": "light",
    "Тёмная": "dark",
    "System": "system",
    "Light": "light",
    "Dark": "dark",
}


def theme_mode_from_label(gui: "CouncilGUI", label: str) -> str:
    """Map a localized theme label to a customtkinter appearance mode."""
    for mode, key in THEME_LABEL_KEYS.items():
        if label == gui.tr(key):
            return mode
    return THEME_LABELS_TO_MODE.get(label, label)


def build_sidebar(gui: "CouncilGUI") -> None:
    header = ctk.CTkFrame(gui.sidebar, fg_color="transparent")
    header.pack(fill="x", padx=20, pady=(26, 14))
    ctk.CTkFrame(header, width=38, height=4, corner_radius=2, fg_color=ACCENT).pack(
        anchor="w", pady=(0, 10)
    )
    ctk.CTkLabel(
        header,
        text="AgentCouncil",
        text_color=TEXT,
        font=ctk.CTkFont(size=24, weight="bold"),
    ).pack(anchor="w")
    gui.sidebar_header_subtitle = ctk.CTkLabel(
        header,
        text=gui.tr("app_subtitle"),
        font=ctk.CTkFont(size=13),
        text_color=MUTED,
    )
    gui.sidebar_header_subtitle.pack(anchor="w")
    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=(0, 14)
    )

    gui.agents_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("agents"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.agents_label.pack(anchor="w", padx=20, pady=(0, 7))
    if not gui.discovered_agents:
        gui.no_agents_label = ctk.CTkLabel(
            gui.sidebar,
            text=gui.tr("no_agents"),
            text_color="orange",
            font=ctk.CTkFont(size=11),
        )
        gui.no_agents_label.pack(anchor="w", padx=20, pady=5)
    else:
        gui.no_agents_label = None
        for member in gui.discovered_agents:
            # Disabled members (council.json "enabled": false) don't get a
            # row at all — select_members() hard-skips them the same way.
            if not member.enabled:
                continue
            name = member.name
            available = member_available(member)
            # Pre-check matches "default": true (or an autodiscovered PATH
            # agent, which member_from_agent always marks as-default) — not
            # a blanket True, since the roster can now also hold explicitly
            # non-default council.json members.
            var = ctk.BooleanVar(value=member.is_default and available)
            gui.agent_vars[name] = var
            gui.agent_status[name] = "idle"
            agent_frame = ctk.CTkFrame(gui.sidebar, fg_color="transparent")
            agent_frame.pack(fill="x", padx=20, pady=4)
            label = name
            if member.kind == KIND_OPENAI:
                label = f"{name} ({member.model})"
            elif not available:
                label = f"{name} (NO PATH)"
            ctk.CTkCheckBox(
                agent_frame,
                text=label,
                variable=var,
                text_color=TEXT,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                font=ctk.CTkFont(size=13),
                state="normal" if available else "disabled",
            ).pack(side="left")
            status_label = ctk.CTkLabel(
                agent_frame,
                text="●",
                text_color=STATUS_BADGE["idle"][1],
                font=ctk.CTkFont(size=12),
            )
            status_label.pack(side="right", padx=(0, 8))
            gui.agent_status_labels[name] = status_label

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.preset_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("preset"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.preset_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.preset_var = ctk.StringVar(value="none")
    gui.preset_buttons = {}
    for value in PRESET_VALUES:
        button = ctk.CTkRadioButton(
            gui.sidebar,
            text=gui.tr(PRESET_LABEL_KEYS[value]),
            variable=gui.preset_var,
            value=value,
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=13),
        )
        button.pack(anchor="w", padx=20, pady=4)
        gui.preset_buttons[value] = button

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.mode_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("mode"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.mode_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.task_mode_var = ctk.BooleanVar(value=False)
    gui.task_mode_checkbox = ctk.CTkCheckBox(
        gui.sidebar,
        text=gui.tr("task_mode"),
        variable=gui.task_mode_var,
        text_color=TEXT,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
    )
    gui.task_mode_checkbox.pack(anchor="w", padx=20, pady=4)
    gui.task_mode_help_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("task_mode_help"),
        text_color=MUTED,
        font=ctk.CTkFont(size=10),
    )
    gui.task_mode_help_label.pack(anchor="w", padx=36, pady=(0, 6))

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.timeout_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("agent_timeout"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.timeout_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.round_timeout_var = ctk.StringVar(value="1200")
    ctk.CTkEntry(
        gui.sidebar,
        textvariable=gui.round_timeout_var,
        height=32,
        width=96,
        corner_radius=6,
        fg_color=("#F7FAFA", "#182022"),
        text_color=TEXT,
        font=ctk.CTkFont(size=13),
    ).pack(anchor="w", padx=20, pady=4)
    gui.timeout_help_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("timeout_help"),
        text_color=MUTED,
        font=ctk.CTkFont(size=10),
        justify="left",
    )
    gui.timeout_help_label.pack(anchor="w", padx=20, pady=(0, 6))

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.evidence_label_title = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("evidence"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.evidence_label_title.pack(anchor="w", padx=20, pady=(0, 7))
    gui.evidence_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("no_evidence"),
        text_color=MUTED,
        font=ctk.CTkFont(size=11),
        justify="left",
    )
    gui.evidence_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.add_evidence_button = ctk.CTkButton(
        gui.sidebar,
        text=gui.tr("add_files"),
        command=gui._add_evidence_files,
        height=32,
        corner_radius=6,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        font=ctk.CTkFont(size=12),
    )
    gui.add_evidence_button.pack(fill="x", padx=20, pady=4)
    gui.add_url_button = ctk.CTkButton(
        gui.sidebar,
        text=gui.tr("add_url"),
        command=gui._add_evidence_url,
        height=32,
        corner_radius=6,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        font=ctk.CTkFont(size=12),
    )
    gui.add_url_button.pack(fill="x", padx=20, pady=4)
    gui.clear_evidence_button = ctk.CTkButton(
        gui.sidebar,
        text=gui.tr("clear_files"),
        command=gui._clear_evidence_files,
        height=32,
        corner_radius=6,
        fg_color="transparent",
        border_width=1,
        text_color=MUTED,
        font=ctk.CTkFont(size=12),
    )
    gui.clear_evidence_button.pack(fill="x", padx=20, pady=(0, 6))

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.theme_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("theme"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.theme_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.theme_var = ctk.StringVar(value=gui.tr("theme_system"))
    gui.theme_menu = ctk.CTkOptionMenu(
        gui.sidebar,
        values=[gui.tr(THEME_LABEL_KEYS[mode]) for mode in THEME_MODES],
        variable=gui.theme_var,
        command=gui._change_theme,
        height=32,
        corner_radius=6,
        fg_color=ACCENT,
        button_color=ACCENT_HOVER,
        button_hover_color=ACCENT,
        font=ctk.CTkFont(size=12),
    )
    gui.theme_menu.pack(fill="x", padx=20, pady=4)

    ctk.CTkFrame(gui.sidebar, height=1, fg_color=("#C8D3D5", "#344044")).pack(
        fill="x", padx=20, pady=16
    )

    gui.language_label = ctk.CTkLabel(
        gui.sidebar,
        text=gui.tr("language"),
        text_color=TEXT,
        font=ctk.CTkFont(size=14, weight="bold"),
    )
    gui.language_label.pack(anchor="w", padx=20, pady=(0, 7))
    gui.language_var = ctk.StringVar(value=gui.tr("language_ru"))
    gui.language_menu = ctk.CTkOptionMenu(
        gui.sidebar,
        values=[gui.tr("language_ru"), gui.tr("language_en")],
        variable=gui.language_var,
        command=gui.set_language,
        height=32,
        corner_radius=6,
        fg_color=ACCENT,
        button_color=ACCENT_HOVER,
        button_hover_color=ACCENT,
        font=ctk.CTkFont(size=12),
    )
    gui.language_menu.pack(fill="x", padx=20, pady=(4, 20))

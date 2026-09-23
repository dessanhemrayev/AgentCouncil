"""GUI pipeline panel: stages, "Agent x Stage" matrix, progress."""

from typing import TYPE_CHECKING

import customtkinter as ctk

from .i18n import translate
from .palette import ACCENT, MUTED, STATUS_BADGE, TEXT

if TYPE_CHECKING:
    from .app import CouncilGUI

PIPELINE_STAGES = [
    ("round1", "R1 Analysis"),
    ("round2", "R2 Critique"),
    ("round3", "R3 Round"),
    ("vote", "Voting"),
]
PIPELINE_STAGE_KEYS = [key for key, _ in PIPELINE_STAGES]
STAGE_LABEL_KEYS = {
    "round1": "stage_r1_analysis",
    "round2": "stage_r2_critique",
    "round3": "stage_r3_round",
    "vote": "stage_vote",
}
TASK_STAGE_LABELS = {
    "task": "Work + Review",
}
TASK_STAGE_LABEL_KEYS = {
    "task": "stage_work_review",
}

STAGE_COLORS = {
    "pending": STATUS_BADGE["idle"],
    "active": STATUS_BADGE["running"],
    "done": STATUS_BADGE["done"],
    "error": STATUS_BADGE["error"],
}

CELL_COLORS = {
    "running": STATUS_BADGE["running"][1],
    "done": STATUS_BADGE["done"][1],
    "error": STATUS_BADGE["error"][1],
    "timeout": STATUS_BADGE["timeout"][1],
    "na": MUTED,
}

CELL_GLYPHS = {
    "running": "●",
    "done": "✓",
    "error": "✗",
    "timeout": "⏱",
    "na": "—",
}


def stage_display_name(stage: str, language: str = "en") -> str:
    """Return a human-readable stage name, including dynamic task stages."""
    if stage in TASK_STAGE_LABEL_KEYS:
        return translate(language, TASK_STAGE_LABEL_KEYS[stage])
    if stage.startswith("task-review-"):
        return translate(
            language,
            "stage_review",
            number=stage.rsplit("-", 1)[-1],
        )
    if stage.startswith("task-fix-"):
        return translate(
            language,
            "stage_fix",
            number=stage.rsplit("-", 1)[-1],
        )
    return translate(language, STAGE_LABEL_KEYS.get(stage, stage))


def build_pipeline(gui: "CouncilGUI") -> None:
    """Build the pipeline panel in gui.pipeline_frame."""
    frame = gui.pipeline_frame

    gui.stage_chip_labels = {}
    gui.chip_labels = {}
    chips = ctk.CTkFrame(frame, fg_color="transparent")
    chips.pack(fill="x", padx=14, pady=(14, 6))
    for key, _ in PIPELINE_STAGES:
        fg, text = STAGE_COLORS["pending"]
        chip = ctk.CTkLabel(
            chips,
            text=f"○ {gui.tr(STAGE_LABEL_KEYS[key])}",
            corner_radius=12,
            fg_color=fg,
            text_color=text,
            font=ctk.CTkFont(size=12, weight="bold"),
            padx=12,
            pady=4,
        )
        chip.pack(side="left", padx=(0, 8))
        gui.stage_chip_labels[key] = chip

    gui.matrix_frame = ctk.CTkFrame(frame, fg_color="transparent")
    gui.matrix_frame.pack(fill="x", padx=14, pady=(4, 6))

    progress_row = ctk.CTkFrame(frame, fg_color="transparent")
    progress_row.pack(fill="x", padx=14, pady=(4, 14))
    gui.progress = ctk.CTkProgressBar(
        progress_row,
        mode="determinate",
        progress_color=ACCENT,
        height=10,
        corner_radius=5,
    )
    gui.progress.set(0)
    gui.progress.pack(side="left", fill="x", expand=True)
    gui.progress_label = ctk.CTkLabel(
        progress_row,
        text="",
        font=ctk.CTkFont(size=12),
        text_color=MUTED,
        width=240,
    )
    gui.progress_label.pack(side="left", padx=(10, 0))


def _stage_labels(gui: "CouncilGUI") -> dict[str, str]:
    """Return current stage labels, including the build-time Russian fallback."""
    labels = getattr(gui, "chip_labels", None)
    if labels:
        return labels
    return {
        key: stage_display_name(key, getattr(gui, "language", "en"))
        for key in PIPELINE_STAGE_KEYS
    }


def set_chip_labels(gui: "CouncilGUI", task_mode: bool) -> None:
    """Recalculate stage chip labels for the selected mode."""
    labels = {key: gui.tr(label_key) for key, label_key in STAGE_LABEL_KEYS.items()}
    if task_mode:
        labels["round3"] = gui.tr("stage_work_review")
        labels["vote"] = gui.tr("stage_verdict")
    gui.chip_labels = labels
    for key, chip in getattr(gui, "stage_chip_labels", {}).items():
        chip.configure(text=f"○ {labels[key]}")


def set_pipeline_agents(gui: "CouncilGUI", agents, task_mode: bool = False) -> None:
    """Recreate the matrix for the selected agents and mode."""
    set_chip_labels(gui, task_mode)

    for child in gui.matrix_frame.winfo_children():
        child.destroy()

    gui.agent_cells = {}
    gui.pipeline_header_labels = {}
    if task_mode:
        gui.pipeline_columns = ["round1", "round2", "task"]
    else:
        gui.pipeline_columns = ["round1", "round2", "round3"]

    grid = ctk.CTkFrame(gui.matrix_frame, fg_color="transparent")
    grid.pack(fill="x")

    ctk.CTkLabel(grid, text="", width=140).grid(row=0, column=0, sticky="w")
    for col, stage in enumerate(gui.pipeline_columns, start=1):
        header = ctk.CTkLabel(
            grid,
            text=stage_display_name(stage, gui.language),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
        )
        header.grid(row=0, column=col, padx=8, sticky="w")
        gui.pipeline_header_labels[stage] = header

    for row, name in enumerate(agents, start=1):
        ctk.CTkLabel(
            grid,
            text=name,
            text_color=TEXT,
            font=ctk.CTkFont(size=12),
            anchor="w",
            width=120,
        ).grid(row=row, column=0, sticky="w", pady=1)
        for col, stage in enumerate(gui.pipeline_columns, start=1):
            cell = ctk.CTkLabel(
                grid,
                text="—",
                text_color=MUTED,
                font=ctk.CTkFont(size=11),
                width=95,
                anchor="w",
            )
            cell.grid(row=row, column=col, padx=6, sticky="w", pady=1)
            gui.agent_cells[(name, stage)] = cell


def set_stage(gui: "CouncilGUI", stage: str) -> None:
    """Highlight the active pipeline stage, including dynamic task stages."""
    gui.current_stage = stage
    labels = _stage_labels(gui)
    chip_stage = "round3" if stage.startswith("task") else stage
    for key, chip in getattr(gui, "stage_chip_labels", {}).items():
        label = labels.get(key, stage_display_name(key, gui.language))
        fg, text = (
            STAGE_COLORS["active"] if key == chip_stage else STAGE_COLORS["pending"]
        )
        glyph = "●" if key == chip_stage else "○"
        chip.configure(fg_color=fg, text_color=text, text=f"{glyph} {label}")
    mark_stage_done(gui, chip_stage, skip_self=True)


def mark_stage_done(gui: "CouncilGUI", stage: str, skip_self: bool = False) -> None:
    """Mark a stage and all preceding stages as complete."""
    chip_stage = "round3" if stage.startswith("task") else stage
    labels = _stage_labels(gui)
    keys = list(labels)
    try:
        idx = keys.index(chip_stage)
    except ValueError:
        return
    for key in keys[: idx + (0 if skip_self else 1)]:
        chip = getattr(gui, "stage_chip_labels", {}).get(key)
        if chip is not None:
            fg, text = STAGE_COLORS["done"]
            chip.configure(
                fg_color=fg,
                text_color=text,
                text=f"✓ {labels.get(key, stage_display_name(key, gui.language))}",
            )


def mark_all_done(gui: "CouncilGUI", ok: bool = True) -> None:
    """Mark every stage complete, or failed when ok is false."""
    labels = getattr(gui, "chip_labels", {})
    glyph = "✓" if ok else "✗"
    fg, text = STAGE_COLORS["done"] if ok else STAGE_COLORS["error"]
    for key, chip in getattr(gui, "stage_chip_labels", {}).items():
        chip.configure(
            fg_color=fg,
            text_color=text,
            text=f"{glyph} {labels.get(key, stage_display_name(key, gui.language))}",
        )


def update_agent_cell(
    gui: "CouncilGUI",
    agent_name: str,
    stage: str,
    status: str,
    detail: str = "",
) -> None:
    """Update one agent/stage matrix cell."""
    if stage == "vote":
        return
    column = stage if stage in gui.pipeline_columns else "task"
    cell = gui.agent_cells.get((agent_name, column))
    if cell is None:
        return
    glyph = CELL_GLYPHS.get(status, "—")
    color = CELL_COLORS.get(status, MUTED)
    text = f"{glyph} {detail}".strip() if status != "na" else "—"
    cell.configure(text=text, text_color=color)


def set_progress(
    gui: "CouncilGUI", done: int, total: int, elapsed_text: str = ""
) -> None:
    """Update deterministic progress based on completed agent calls."""
    if total <= 0:
        gui.progress.set(0)
        gui.progress_label.configure(text=elapsed_text or "")
        return
    gui.progress.set(min(1.0, done / total))
    percent = int(done / total * 100)
    parts = [gui.tr("progress_calls", done=done, total=total, percent=percent)]
    if elapsed_text:
        parts.append(elapsed_text)
    gui.progress_label.configure(text="  ·  ".join(parts))


def refresh_language(gui: "CouncilGUI") -> None:
    """Refresh pipeline labels without recreating the matrix."""
    set_chip_labels(
        gui, getattr(gui, "task_mode_var", None) is not None and gui.task_mode_var.get()
    )
    current_stage = getattr(gui, "current_stage", None)
    if current_stage is None:
        for key, chip in gui.stage_chip_labels.items():
            chip.configure(text=f"○ {gui.chip_labels[key]}")
    else:
        set_stage(gui, current_stage)
    for stage, header in getattr(gui, "pipeline_header_labels", {}).items():
        header.configure(text=stage_display_name(stage, gui.language))

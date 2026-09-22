"""Renders a markdown verdict into tk.Text — no external dependencies.

Agent verdicts are raw markdown: headings, **bold**/`code`/links,
lists, quotes, ```-blocks, tables, hr. In the verdict window it is drawn
with tk.Text tags (fonts, colors, indents), links are clickable.
Colors follow the current theme (light/dark) at render time: tk tags don't
understand ctk tuples (light, dark), so the mode is resolved manually.
"""

import re
import tkinter.font as tkfont
import webbrowser

import customtkinter as ctk

COLORS = {
    "light": {
        "ink": "#24292F",
        "muted": "#6B7280",
        "heading": "#1E3A8A",
        "accent": "#3B5BDB",
        "link": "#2563EB",
        "code_bg": "#F0F2F7",
        "code_fg": "#B13B62",
        "table_bg": "#F6F8FC",
        "table_head_bg": "#E9EDF6",
        "hr": "#C9CFDA",
    },
    "dark": {
        "ink": "#E6E9F0",
        "muted": "#98A2B3",
        "heading": "#A5B4FC",
        "accent": "#8FA6FF",
        "link": "#7CA5FF",
        "code_bg": "#262B36",
        "code_fg": "#FF9DB5",
        "table_bg": "#22262F",
        "table_head_bg": "#2A2F3B",
        "hr": "#3A4150",
    },
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^\[( |x|X)\]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
# Order of alternatives matters: *** before **, ` and links are separate branches.
# _italic_ — with word boundaries so it doesn't swallow snake_case names.
_INLINE_RE = re.compile(
    r"`[^`\n]+`"
    r"|\*\*\*[^*\n]+\*\*\*"
    r"|\*\*[^*\n]+\*\*"
    r"|__[^_\n]+__"
    r"|\*[^*\n]+\*"
    r"|(?<![\w])_[^_\n]+_(?!\w)"
    r"|\[[^\]\n]+\]\([^)\n]+\)"
)
_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _appearance_mode() -> str:
    mode = str(ctk.get_appearance_mode()).lower()
    return "dark" if mode == "dark" else "light"


def _fonts(inner):
    """Tag fonts derived from the textbox's own font."""
    spec = inner.cget("font")
    base = tkfont.Font(font=spec, root=inner)
    size0 = abs(int(base.cget("size"))) or 13
    fixed = tkfont.nametofont("TkFixedFont").actual("family")

    def derive(**kw):
        f = tkfont.Font(font=spec, root=inner)
        f.configure(**kw)
        return f

    return {
        "bold": derive(weight="bold"),
        "italic": derive(slant="italic"),
        "bolditalic": derive(weight="bold", slant="italic"),
        "h1": derive(size=size0 + 6, weight="bold"),
        "h2": derive(size=size0 + 4, weight="bold"),
        "h3": derive(size=size0 + 2, weight="bold"),
        "h4": derive(size=size0 + 1, weight="bold"),
        "mono": derive(family=fixed),
        "monob": derive(family=fixed, weight="bold"),
    }


def _define_tags(inner, fonts: dict, p: dict) -> None:
    inner.tag_configure(
        "h1", font=fonts["h1"], foreground=p["heading"], spacing1=8, spacing3=4
    )
    inner.tag_configure(
        "h2", font=fonts["h2"], foreground=p["heading"], spacing1=7, spacing3=3
    )
    inner.tag_configure(
        "h3", font=fonts["h3"], foreground=p["ink"], spacing1=6, spacing3=2
    )
    inner.tag_configure(
        "h4", font=fonts["h4"], foreground=p["ink"], spacing1=5, spacing3=2
    )
    inner.tag_configure(
        "h5", font=fonts["bold"], foreground=p["muted"], spacing1=4, spacing3=2
    )
    inner.tag_configure(
        "h6", font=fonts["bold"], foreground=p["muted"], spacing1=4, spacing3=2
    )
    inner.tag_configure("bold", font=fonts["bold"])
    inner.tag_configure("italic", font=fonts["italic"])
    inner.tag_configure("bolditalic", font=fonts["bolditalic"])
    inner.tag_configure(
        "code", font=fonts["mono"], background=p["code_bg"], foreground=p["code_fg"]
    )
    inner.tag_configure(
        "codeblock",
        font=fonts["mono"],
        background=p["code_bg"],
        foreground=p["ink"],
        lmargin1=12,
        lmargin2=12,
        spacing1=1,
        spacing3=1,
    )
    inner.tag_configure(
        "quote",
        font=fonts["italic"],
        foreground=p["muted"],
        lmargin1=16,
        lmargin2=16,
        spacing1=1,
        spacing3=1,
    )
    inner.tag_configure("hr", foreground=p["hr"], justify="center")
    inner.tag_configure("bullet", foreground=p["accent"])
    inner.tag_configure("muted", foreground=p["muted"])
    inner.tag_configure("table", font=fonts["mono"], background=p["table_bg"])
    inner.tag_configure(
        "tablehead",
        font=fonts["monob"],
        background=p["table_head_bg"],
        foreground=p["heading"],
    )
    inner.tag_configure("tablerule", font=fonts["mono"], foreground=p["hr"])


def _bind_link(inner, url: str, p: dict) -> str:
    """Clickable link: a tag on the URL with color, underline, and cursor."""
    tag = f"link:{url}"
    if tag not in inner.tag_names():  # bindings are applied once per URL
        inner.tag_configure(tag, foreground=p["link"], underline=True)
        inner.tag_bind(tag, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
        inner.tag_bind(tag, "<Enter>", lambda _e: inner.configure(cursor="hand2"))
        inner.tag_bind(tag, "<Leave>", lambda _e: inner.configure(cursor=""))
    return tag


def _inline(inner, text: str, base: tuple[str, ...] = (), plain: bool = False) -> None:
    """Inline markup: `code`, **bold**, *italic*, [text](url).

    plain=True — for headings: accents inside them don't lower the font
    (the bold tag would overlap the heading font).
    """
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            inner.insert("end", text[pos : m.start()], base)
        token = m.group(0)
        if token.startswith("`"):
            inner.insert("end", token[1:-1], base + ("code",))
        elif token.startswith("***"):
            tags = ("bolditalic",) if plain else ("bold", "italic")
            inner.insert("end", token[3:-3], base + tags)
        elif token.startswith(("**", "__")):
            inner.insert("end", token[2:-2], base + (() if plain else ("bold",)))
        elif token.startswith(("*", "_")):
            inner.insert("end", token[1:-1], base + (() if plain else ("italic",)))
        else:  # link [text](url)
            link = _LINK_RE.match(token)
            if link is None:  # can't happen: token from _INLINE_RE — but for mypy
                inner.insert("end", token, base)
            else:
                tag = _bind_link(inner, link.group(2), COLORS[_appearance_mode()])
                inner.insert("end", link.group(1), base + (tag,))
        pos = m.end()
    if pos < len(text):
        inner.insert("end", text[pos:], base)


def _table(inner, lines: list[str], start: int) -> int:
    """Table |a|b|: aligned monospaced columns, header with background."""
    rows: list[tuple[list[str], bool]] = []
    i = start
    n = len(lines)
    while i < n and lines[i].strip().startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        is_sep = bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)
        rows.append((cells, is_sep))
        i += 1

    # Header — first row, only if immediately followed by the separator |---|---|.
    header: list[str] = []
    body_rows: list[list[str]] = []
    if len(rows) >= 2 and rows[1][1]:
        header = rows[0][0]
        body_rows = [r[0] for r in rows[2:]]
    else:
        body_rows = [r[0] for r in rows if not r[1]]

    all_rows = ([header] if header else []) + body_rows
    ncols = max((len(r) for r in all_rows), default=1)
    widths = [1] * ncols
    for r in all_rows:
        for c, cell in enumerate(r[:ncols]):
            widths[c] = max(widths[c], min(len(cell), 42))

    def render_row(cells: list[str], tag: str) -> None:
        padded = [
            (cells[c] if c < len(cells) else "")[:42].ljust(widths[c])
            for c in range(ncols)
        ]
        inner.insert("end", "  " + " │ ".join(padded) + "\n", (tag,))

    if header:
        render_row(header, "tablehead")
        rule = "  " + "─┼─".join("─" * w for w in widths) + "\n"
        inner.insert("end", rule, ("tablerule",))
    for r in body_rows:
        render_row(r, "table")
    inner.insert("end", "\n")
    return i


def _body(inner, text: str) -> None:
    lines: list[str] = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()

        if not s:
            inner.insert("end", "\n")
            i += 1
            continue

        m = _HEADING_RE.match(s)
        if m:
            level = min(len(m.group(1)), 6)
            _inline(inner, m.group(2).strip(), (f"h{level}",), plain=True)
            inner.insert("end", "\n")
            i += 1
            continue

        if _HR_RE.match(s):
            inner.insert("end", "─" * 68 + "\n", ("hr",))
            i += 1
            continue

        if s.startswith("```"):
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                inner.insert("end", lines[i].rstrip() + "\n", ("codeblock",))
                i += 1
            i += 1  # closing fence (or EOF — unclosed block is OK too)
            inner.insert("end", "\n")
            continue

        if s.startswith(">"):
            while i < n and lines[i].strip().startswith(">"):
                _inline(inner, lines[i].strip().lstrip(">").strip(), ("quote",))
                inner.insert("end", "\n")
                i += 1
            continue

        if s.startswith("|"):
            i = _table(inner, lines, i)
            continue

        m = _LIST_RE.match(lines[i])
        if m:
            indent, marker, content = m.group(1), m.group(2), m.group(3)
            pad = "    " * (len(indent.expandtabs(4)) // 2)
            check = _CHECKBOX_RE.match(content)
            inner.insert("end", pad)
            if check:
                box = "☑ " if check.group(1).lower() == "x" else "☐ "
                inner.insert("end", box, ("bullet",))
                _inline(inner, check.group(2))
            elif marker in "-*+":
                inner.insert("end", "• ", ("bullet",))
                _inline(inner, content)
            else:  # numbered list — keep the number as is
                inner.insert("end", marker + " ", ("bullet",))
                _inline(inner, content)
            inner.insert("end", "\n")
            i += 1
            continue

        _inline(inner, s)
        inner.insert("end", "\n")
        i += 1


def render(textbox, text: str) -> None:
    """Renders markdown into a CTkTextbox (via tags on the inner tk.Text)."""
    inner = textbox._textbox
    _define_tags(inner, _fonts(inner), COLORS[_appearance_mode()])
    _body(inner, text)

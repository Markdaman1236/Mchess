"""The game log drawer from Board.nib.

The "Log Container" is 230 points wide beside the 741 point board. From
the top: a three-line "White / — / Black" name field (displayPattern
"%{value1}@\\n—\\n%{value2}@", null placeholder "TBD"), the "Edit Game
Info…" button, and an MBCMoveTableView with no header, 14 point rows,
3x2 intercell spacing, alternating row colours, horizontal and vertical
grid lines in (0.539, 0.624, 0.712), and seven columns:

    Move 25 (right, "1."), White.origin 30 (right), White.operation 9
    (centre, MBCStretchyTextCell), White.destination 45 (left), and the
    same three for Black.

-[MBCBoardWin toggleLogView:] slides the container in from the right
edge by animating logViewRightEdgeConstraint, so the board narrows while
the drawer moves; the default NSAnimationContext duration is 0.25 s.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PANEL_W = 230
COLUMNS = (  # width, anchor
    (25, "e"),
    (30, "e"),
    (9, "center"),
    (45, "w"),
    (30, "e"),
    (9, "center"),
    (45, "w"),
)
SPACING_X = 3
SPACING_Y = 2
ROW_H = 14
GRID = "#899fb5"
ROW_BG = ("#ffffff", "#f4f5f5")
PANEL_BG = "#ececec"
NAMES_BG = "#dcdcdc"
SLIDE_SECONDS = 0.25


class MoveLog(tk.Frame):
    """The log container: a clip frame whose width animates, with the
    drawer contents placed at full width inside it."""

    def __init__(self, master, scale: float, on_edit_info) -> None:
        self.scale = scale
        self.full = int(round(PANEL_W * scale))
        super().__init__(master, width=0, bg=PANEL_BG, highlightthickness=0)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.inner = tk.Frame(self, bg=PANEL_BG, width=self.full)
        self.inner.place(x=0, y=0, width=self.full, relheight=1.0)
        pad = int(round(7 * scale))
        self.names = tk.Label(
            self.inner, text="TBD\n—\nTBD", justify="center", bg=NAMES_BG,
            font=tkfont.Font(family="Segoe UI", size=-int(round(13 * scale))),
        )
        self.names.pack(side="top", fill="x", padx=(pad, int(round(8 * scale))), pady=(int(round(9 * scale)), 0),
                        ipady=int(round(4 * scale)))
        ttk.Button(self.inner, text="Edit Game Info…", command=on_edit_info).pack(
            side="top", pady=(int(round(6 * scale)), int(round(8 * scale))))
        table = tk.Frame(self.inner, bg=GRID, highlightthickness=0)
        table.pack(side="top", fill="both", expand=True, padx=(pad, int(round(8 * scale))), pady=(0, int(round(8 * scale))))
        self.canvas = tk.Canvas(table, bg=ROW_BG[0], highlightthickness=1, highlightbackground="#b5b5b5", bd=0)
        self.bar = ttk.Scrollbar(table, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.font = tkfont.Font(family="Segoe UI", size=-int(round(11 * scale)))
        self.rows: list[tuple] = []
        self._shown_rows = 0
        self._anim = None
        self.visible = False
        self.canvas.bind("<Configure>", lambda _e: (self._draw(), self._scroll_to_end()))
        self.canvas.bind("<MouseWheel>", self._wheel)

    # ----- contents -------------------------------------------------------

    def set_names(self, white: str, black: str) -> None:
        self.names.configure(text=f"{white or 'TBD'}\n—\n{black or 'TBD'}")

    def set_moves(self, parts: list[tuple[str, str, str]]) -> None:
        """parts holds (origin, operation, destination) for each ply."""
        rows = []
        for index in range(0, len(parts), 2):
            white = parts[index]
            black = parts[index + 1] if index + 1 < len(parts) else ("", "", "")
            rows.append((f"{index // 2 + 1}.",) + tuple(white) + tuple(black))
        if rows == self.rows:
            return
        self.rows = rows
        self._draw()
        if len(rows) != self._shown_rows:
            # -[MBCGameInfo updateMoves:] scrolls the new row into view.
            self._shown_rows = len(rows)
            self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        row_h = int(round((ROW_H + SPACING_Y) * self.scale))
        if len(self.rows) * row_h > self.canvas.winfo_height() > 1:
            self.canvas.yview_moveto(1.0)
        else:
            self.canvas.yview_moveto(0.0)

    def _wheel(self, event) -> str:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        s = self.scale
        row_h = int(round((ROW_H + SPACING_Y) * s))
        width = max(canvas.winfo_width(), 1)
        visible_h = max(canvas.winfo_height(), 1)
        total_h = max(len(self.rows) * row_h, visible_h)
        canvas.configure(scrollregion=(0, 0, width, total_h), yscrollincrement=row_h)
        # alternatingRowBackgroundColors fills the empty area too.
        for row in range(total_h // row_h + 1):
            y = row * row_h
            canvas.create_rectangle(0, y, width, y + row_h, fill=ROW_BG[row & 1], width=0)
        edges = []
        x = 0.0
        for col_w, _anchor in COLUMNS:
            cell = (col_w + SPACING_X) * s
            edges.append((x, x + cell))
            x += cell
        for _left, right in edges:
            canvas.create_line(int(right), 0, int(right), total_h, fill=GRID)
        for row in range(total_h // row_h + 1):
            y = (row + 1) * row_h - 1
            canvas.create_line(0, y, width, y, fill=GRID)
        pad = SPACING_X * s / 2.0 + 1
        for row, values in enumerate(self.rows):
            mid = row * row_h + row_h / 2.0
            for col, text in enumerate(values):
                if not text:
                    continue
                left, right = edges[col]
                anchor = COLUMNS[col][1]
                if col in (2, 5) and len(text) > 1:
                    # MBCStretchyTextCell: castling spills over the empty
                    # origin and destination cells, centred on the operation.
                    canvas.create_text((left + right) / 2.0, mid, text=text, anchor="center", font=self.font)
                elif anchor == "e":
                    canvas.create_text(right - pad, mid, text=text, anchor="e", font=self.font)
                elif anchor == "w":
                    canvas.create_text(left + pad, mid, text=text, anchor="w", font=self.font)
                else:
                    canvas.create_text((left + right) / 2.0, mid, text=text, anchor="center", font=self.font)

    # ----- showing and hiding -----------------------------------------------

    def show(self, visible: bool, animate: bool = True) -> None:
        if visible == self.visible and self._anim is None:
            return
        self.visible = visible
        target = self.full if visible else 0
        if not animate:
            self._anim = None
            self.configure(width=target)
            return
        start = self.winfo_width() if self.winfo_ismapped() else (0 if visible else self.full)
        self._anim = {"from": start, "to": target, "began": time.perf_counter()}
        self._step(self._anim)

    def _step(self, anim: dict) -> None:
        if self._anim is not anim:
            return
        t = min(1.0, (time.perf_counter() - anim["began"]) / SLIDE_SECONDS)
        eased = t * t * (3.0 - 2.0 * t)  # ease in, ease out
        width = int(round(anim["from"] + (anim["to"] - anim["from"]) * eased))
        self.configure(width=width)
        if t >= 1.0:
            self._anim = None
            return
        self.after(15, lambda: self._step(anim))

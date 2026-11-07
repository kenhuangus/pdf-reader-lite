"""Hand-drawn signature pad."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk


@dataclass
class SignatureResult:
    strokes: list[list[tuple[float, float]]]
    ratio: float
    width_pt: float


SIZES = [
    ("Small — 120 pt", 120.0),
    ("Medium — 180 pt", 180.0),
    ("Large — 240 pt", 240.0),
]


class SignatureDialog(tk.Toplevel):
    def __init__(self, parent, palette: dict) -> None:
        super().__init__(parent)
        self.palette = palette
        self.result: SignatureResult | None = None
        self.strokes: list[list[tuple[float, float]]] = []
        self._current: list[tuple[float, float]] = []
        self._drawing = False

        self.title("Hand-drawn signature")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(background=palette["bg"])
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Draw your signature", style="Brand.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(frame, text="This places a hand-drawn ink annotation on the PDF. "
                             "It is not a cryptographic digital signature.",
                  foreground=palette["muted"], wraplength=610).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.canvas = tk.Canvas(frame, width=600, height=240, bg="#ffffff",
                                bd=1, highlightthickness=0,
                                highlightbackground=palette["page_border"])
        self.canvas.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.canvas.bind("<Button-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end)

        self.canvas.create_line(20, 205, 580, 205, fill="#c9ccd2", dash=(4, 4))
        self.canvas.create_text(580, 205, text="sign above this line", anchor="se",
                                fill="#aab0b8", font=("Segoe UI", 8))
        self.canvas.create_text(14, 200, text="", tags="hint")

        controls = ttk.Frame(frame)
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(controls, text="Placed size:").pack(side="left", padx=(0, 6))
        self.size_var = tk.StringVar(value=SIZES[1][0])
        self.size_box = ttk.Combobox(controls, textvariable=self.size_var,
                                     values=[label for label, _ in SIZES],
                                     state="readonly", width=18)
        self.size_box.pack(side="left")
        ttk.Button(controls, text="Undo", style="TButton",
                   command=self._undo).pack(side="left", padx=(14, 0))
        ttk.Button(controls, text="Clear", style="TButton",
                   command=self._clear).pack(side="left", padx=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Place Signature", style="Accent.TButton",
                   command=self._accept).pack(side="left")

        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Control-z>", lambda _e: self._undo())
        self.canvas.focus_set()
        self.grab_set()

    def _start(self, event) -> None:
        self._drawing = True
        point = self._clamp(event.x, event.y)
        self._current = [point]
        self.canvas.create_line(*point, *point, width=3, fill="#17253b",
                                capstyle="round", smooth=True, tags="sign")
        self.canvas.delete("hint")
        self.canvas.create_text(14, 200, text="", tags="hint")

    def _drag(self, event) -> None:
        if not self._drawing:
            return
        point = self._clamp(event.x, event.y)
        if self._current and abs(point[0] - self._current[-1][0]) + \
                abs(point[1] - self._current[-1][1]) < 0.5:
            return
        self._current.append(point)
        self.canvas.delete("live")
        self.canvas.create_line(*[c for pt in self._current for c in pt],
                                width=3, fill="#17253b", capstyle="round",
                                smooth=True, tags=("sign", "live"))

    def _end(self, _event) -> None:
        if not self._drawing:
            return
        self._drawing = False
        self.canvas.delete("live")
        if len(self._current) >= 2:
            self.strokes.append(self._current)
        self._current = []
        self._redraw()

    @staticmethod
    def _clamp(x: float, y: float) -> tuple[float, float]:
        return max(2.0, min(598.0, x)), max(2.0, min(238.0, y))

    def _undo(self) -> None:
        if self._drawing and self._current:
            self._current = []
            self._drawing = False
        elif self.strokes:
            self.strokes.pop()
        self._redraw()

    def _clear(self) -> None:
        self.strokes = []
        self._current = []
        self._drawing = False
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("sign")
        self.canvas.delete("live")
        for stroke in self.strokes:
            if len(stroke) >= 2:
                self.canvas.create_line(*[c for pt in stroke for c in pt],
                                        width=3, fill="#17253b",
                                        capstyle="round", smooth=True, tags="sign")

    def _accept(self) -> None:
        if self._drawing:
            self._end(None)
        if not self.strokes:
            messagebox.showinfo("Signature", "Draw a signature first.", parent=self)
            return
        min_x, min_y, max_x, max_y = self._bounds()
        width = max(1.0, max_x - min_x)
        height = max(1.0, max_y - min_y)
        margin = 6.0
        min_x, min_y = min_x - margin, min_y - margin
        width += 2 * margin
        height += 2 * margin
        normalized: list[list[tuple[float, float]]] = []
        for stroke in self.strokes:
            normalized.append([((x - min_x) / width, (y - min_y) / height)
                               for x, y in stroke])
        label_width = dict(SIZES)[self.size_var.get()]
        self.result = SignatureResult(normalized, height / width, label_width)
        self.grab_release()
        self.destroy()

    def _bounds(self) -> tuple[float, float, float, float]:
        xs = [x for stroke in self.strokes for x, _ in stroke]
        ys = [y for stroke in self.strokes for _, y in stroke]
        return min(xs), min(ys), max(xs), max(ys)

    def cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def ask_signature(parent, palette: dict) -> SignatureResult | None:
    dialog = SignatureDialog(parent, palette)
    parent.wait_window(dialog)
    return dialog.result

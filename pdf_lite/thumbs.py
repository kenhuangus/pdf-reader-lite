"""Lazy thumbnail strip used by the Pages sidebar."""

from __future__ import annotations

import tkinter as tk
from collections import OrderedDict
from tkinter import ttk

from PIL import ImageTk

from .core import PdfDocument


THUMB_WIDTH = 132
THUMB_GAP = 12
THUMB_LABEL_H = 24
THUMB_CACHE_LIMIT = 64


class ThumbnailPanel(tk.Frame):
    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        self.doc: PdfDocument | None = None
        self._items: list[dict] = []
        self._cache: OrderedDict[int, object] = OrderedDict()
        self._render_after: str | None = None
        self._selected = -1
        self._palette_ready = False
        self._thumb_w = THUMB_WIDTH
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0,
                                background=app.palette["sidebar"])
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", self._on_configure)

    def set_palette(self, palette: dict) -> None:
        self.canvas.configure(background=palette["sidebar"])
        if self.doc is not None:
            self._draw_all()
            self._schedule_render()

    def set_document(self, doc: PdfDocument | None) -> None:
        self.doc = doc
        self._cache.clear()
        self._selected = -1
        self._items = []
        self.canvas.delete("all")
        self.canvas.yview_moveto(0)
        if doc is None:
            return
        self._build_items()
        self._draw_all()
        self._schedule_render()

    def invalidate_page(self, number: int) -> None:
        """Invalidate and re-render a page thumbnail after an edit."""
        self._cache.pop(number, None)
        self.canvas.delete(f"timg{number}")
        self._schedule_render()

    def _on_configure(self, _event=None) -> None:
        width = self.canvas.winfo_width()
        wanted = max(110, min(THUMB_WIDTH, width - 22))
        if wanted != self._thumb_w and self.doc is not None:
            self._thumb_w = wanted
            self._build_items()
            self._draw_all()
            self._schedule_render()

    def _build_items(self) -> None:
        self._items = []
        if self.doc is None:
            return
        y = 10
        for number in range(self.doc.page_count):
            width_pt, height_pt = self.doc.page_size(number, 0)
            ratio = self._thumb_w / max(1.0, width_pt)
            height = max(12, int(round(height_pt * ratio)))
            self._items.append({
                "n": number,
                "x": 10,
                "y": y,
                "w": self._thumb_w,
                "h": height,
                "ratio": ratio,
                "slot": height + THUMB_LABEL_H + THUMB_GAP,
            })
            y += height + THUMB_LABEL_H + THUMB_GAP
        region_h = max(self.canvas.winfo_height(), y + 8)
        self.canvas.configure(scrollregion=(0, 0, 220, region_h))

    def _draw_all(self) -> None:
        palette = self.app.palette
        self.canvas.delete("all")
        for item in self._items:
            number = item["n"]
            x, y, width, height = item["x"], item["y"], item["w"], item["h"]
            self.canvas.create_rectangle(x + 2, y + 2, x + width + 2, y + height + 2,
                                         fill=palette["shadow"], outline=palette["shadow"],
                                         tags=("thumb", f"t{number}"))
            self.canvas.create_rectangle(x, y, x + width, y + height,
                                         fill="#ffffff", outline=palette["page_border"],
                                         tags=("thumb", f"tb{number}"))
            self.canvas.create_text(x + width / 2, y + height / 2,
                                    text=str(number + 1), fill="#b7bcc5",
                                    font=("Segoe UI", 9), tags=("thumb", f"tp{number}"))
            self.canvas.create_text(x + width / 2, y + height + 12,
                                    text=str(number + 1), fill=palette["muted"],
                                    font=("Segoe UI", 8), tags=("thumb", f"tl{number}"))
            if self._selected == number:
                self._draw_selection(number)
        self._ensure_visible(self._selected)

    def _draw_selection(self, number: int) -> None:
        if not (0 <= number < len(self._items)):
            return
        item = self._items[number]
        self.canvas.delete("thumb-sel")
        self.canvas.create_rectangle(item["x"] - 3, item["y"] - 3,
                                     item["x"] + item["w"] + 3, item["y"] + item["h"] + 3,
                                     outline=self.app.palette["accent"], width=3,
                                     tags=("thumb", "thumb-sel", f"t{number}"))

    def select_page(self, number: int) -> None:
        if self._selected == number:
            self._ensure_visible(number)
            return
        self._selected = number
        self.canvas.delete("thumb-sel")
        if 0 <= number < len(self._items):
            self._draw_selection(number)
            self._ensure_visible(number)

    def _ensure_visible(self, number: int) -> None:
        if not (0 <= number < len(self._items)):
            return
        item = self._items[number]
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        if item["y"] < top or item["y"] + item["h"] > top + height - 12:
            region = self._scroll_height()
            self.canvas.yview_moveto(
                max(0.0, min(1.0, (item["y"] - 8) / max(1, region))))

    def _scroll_height(self) -> int:
        try:
            return max(self.canvas.winfo_height(),
                       int(float(str(self.canvas.cget("scrollregion")).split()[3])))
        except Exception:
            return max(1, self.canvas.winfo_height())

    def _on_wheel(self, event) -> str:
        if event.delta == 0:
            return "break"
        steps = 2 if abs(event.delta) < 120 else max(1, abs(event.delta) // 40)
        self.canvas.yview_scroll((-1 if event.delta > 0 else 1) * steps, "units")
        self._schedule_render()
        return "break"

    def _on_click(self, event) -> None:
        y = self.canvas.canvasy(event.y)
        for item in self._items:
            if item["y"] <= y <= item["y"] + item["h"]:
                self.app.go_to_page(item["n"] + 1, remember=True)
                break

    def _schedule_render(self) -> None:
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._render_after = self.after(80, self._render_visible)

    def _visible_items(self) -> list[int]:
        if not self._items:
            return []
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height() + 60
        return [item["n"] for item in self._items
                if item["y"] + item["h"] >= top - 20 and item["y"] <= bottom]

    def _render_visible(self) -> None:
        self._render_after = None
        if self.doc is None:
            return
        pending: list[int] = []
        for number in self._visible_items():
            photo = self._cache.get(number)
            if photo is not None:
                self._cache.move_to_end(number)
                self._place(number, photo)
            elif not self.canvas.find_withtag(f"ti{number}"):
                pending.append(number)
        for number in pending[:2]:
            self._render_number(number)
        if len(pending) > 2:
            self._render_after = self.after(90, self._render_visible)

    def _render_number(self, number: int) -> None:
        if self.doc is None or not (0 <= number < len(self._items)):
            return
        item = self._items[number]
        try:
            image = self.doc.render_page(number, item["ratio"])
            if image.size != (item["w"], item["h"]):
                image = image.resize((item["w"], item["h"]),
                                     Image.Resampling.BILINEAR)
            photo = ImageTk.PhotoImage(image=image)
            self._cache[number] = photo
            self._cache.move_to_end(number)
            while len(self._cache) > THUMB_CACHE_LIMIT:
                self._cache.popitem(last=False)
            self._place(number, photo)
        except Exception:
            # The main view will report document errors; thumbnails simply stay
            # as placeholders if a damaged page cannot render.
            return

    def _place(self, number: int, photo) -> None:
        if not (0 <= number < len(self._items)) or self.canvas.find_withtag(f"ti{number}"):
            return
        item = self._items[number]
        self.canvas.delete(f"tp{number}")
        self.canvas.create_image(item["x"], item["y"], image=photo, anchor="nw",
                                 tags=("thumb", f"ti{number}", f"t{number}"))

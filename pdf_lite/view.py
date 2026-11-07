"""Continuous page canvas, selection, links and lazy rendering."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from collections import OrderedDict
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

import fitz
from PIL import Image, ImageTk

from .core import BASE_SCALE, PdfDocument
from .editor import get_element_at_point


class PageRender(tk.Frame):
    pass


MODE_READ = "read"
MODE_SELECT = "select"
MODE_EDIT = "edit"

MIN_ZOOM = 0.10
MAX_ZOOM = 5.00
ZOOM_STEPS = [0.10, 0.15, 0.20, 0.25, 0.33, 0.50, 0.67, 0.75, 0.90, 1.00,
              1.10, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00, 4.00, 5.00]
PAGE_MARGIN_X = 28
PAGE_MARGIN_TOP = 24
PAGE_GAP = 30
PAGE_MARGIN_BOTTOM = 36
CACHE_LIMIT = 320 * 1024 * 1024  # bytes, approximate


class ContinuousView(tk.Frame):
    """Acrobat-style continuous scrolling page view."""

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        self.doc: PdfDocument | None = None
        self.zoom = 1.0
        self.fit_mode: str | None = "width"
        self.view_rotation = 0
        self.layout: list[dict] = []
        self.content_width = 0
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._cache_bytes = 0
        self._render_after: str | None = None
        self._scroll_after: str | None = None
        self._notify_after: str | None = None
        self._resize_after: str | None = None
        self._last_canvas_size = (0, 0)
        self._last_page = -1
        self._last_zoom = -1
        self.search_hits: dict[int, list[tuple[float, float, float, float]]] = {}
        self.current_hit: tuple[int, tuple[float, float, float, float]] | None = None
        self.selection_page: int | None = None
        self.selection_rects: list[tuple[float, float, float, float]] = []
        self.selection_page_rects: list[tuple[float, float, float, float]] = []
        self.selection_text = ""
        self._sel_page: int | None = None
        self._sel_origin: tuple[float, float] | None = None
        self._sel_dragged = False
        self._press_point: tuple[float, float] | None = None
        self._context_menu: tk.Menu | None = None
        self._placing_signature = False
        self._signature_strokes: list[list[tuple[float, float]]] = []
        self._signature_ratio = 1.0
        self._signature_width = 180.0
        self.mode = MODE_READ
        self.edit_mode = False
        self._pan_origin: tuple[int, int] | None = None

        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, background="#e9eaed")
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hscroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self._update_vscroll,
                              xscrollcommand=self.hscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.hscroll.grid(row=1, column=0, sticky="ew")
        self.hscroll.grid_remove()
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._on_linux_wheel(e, -1))
        self.canvas.bind("<Button-5>", lambda e: self._on_linux_wheel(e, 1))
        self.canvas.bind("<Button-1>", self._on_button_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_button_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_context_menu)
        self.canvas.bind("<Motion>", self._on_motion_hover)
        self.canvas.bind("<FocusIn>", lambda _e: None)
        self._selection_active = False

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------
    def set_document(self, doc: PdfDocument, initial_fit: str | None = "width") -> None:
        self.doc = doc
        self.fit_mode = initial_fit
        self.zoom = 1.0
        self.view_rotation = 0
        self._cache.clear()
        self._cache_bytes = 0
        self.layout = []
        self.search_hits = {}
        self.current_hit = None
        self._last_page = -1
        self.cancel_signature_placement(silent=True)
        self.clear_selection()
        self._last_canvas_size = (0, 0)
        self.update_idletasks()
        self.canvas.delete("all")
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
        self._relayout(preserve=False)
        self.canvas.focus_set()
        self._notify(force=True)

    def clear_document(self) -> None:
        self.doc = None
        self.layout = []
        self._cache.clear()
        self._cache_bytes = 0
        self.search_hits = {}
        self.current_hit = None
        self._last_page = -1
        self.cancel_signature_placement(silent=True)
        self.clear_selection()
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))
        self._draw_empty_state()

    def _draw_empty_state(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 400)
        height = max(self.canvas.winfo_height(), 300)
        bg = self.app.palette["workspace"]
        fg = self.app.palette["muted"]
        accent = self.app.palette["accent"]
        self.canvas.configure(background=bg)
        self.canvas.create_text(width / 2, height / 2 - 46, text="PDF Lite",
                                font=("Segoe UI", 25, "bold"), fill=accent,
                                tags="empty")
        self.canvas.create_text(width / 2, height / 2,
                                text="Open a PDF to start reading",
                                font=("Segoe UI", 13), fill=fg, tags="empty")
        self.canvas.create_text(width / 2, height / 2 + 32,
                                text="Ctrl+O  •  drag to select  •  Ctrl+F to find  •  F11 for full screen",
                                font=("Segoe UI", 9), fill=fg, tags="empty")

    def set_palette(self, palette: dict) -> None:
        self.canvas.configure(background=palette["workspace"])
        if self.doc is None:
            self._draw_empty_state()
        else:
            self._relayout(preserve=True)

    # ------------------------------------------------------------------
    # Layout and zoom
    # ------------------------------------------------------------------
    def _canvas_size(self) -> tuple[int, int]:
        return max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)

    def _reference_page(self) -> int:
        if not self.layout:
            return 0
        return min(self.current_page(), max(0, self.doc.page_count - 1))

    def _compute_fit_zoom(self, ref_page: int) -> float:
        if self.doc is None:
            return 1.0
        width, height = self.doc.page_size(ref_page, self.view_rotation)
        canvas_w, canvas_h = self._canvas_size()
        avail_w = max(80, canvas_w - 2 * PAGE_MARGIN_X)
        avail_h = max(80, canvas_h - PAGE_MARGIN_TOP - PAGE_MARGIN_BOTTOM)
        width_px = max(1.0, width * BASE_SCALE)
        height_px = max(1.0, height * BASE_SCALE)
        if self.fit_mode == "width":
            return avail_w / width_px
        return min(avail_w / width_px, avail_h / height_px)

    @staticmethod
    def _clamp_zoom(value: float) -> float:
        return max(MIN_ZOOM, min(MAX_ZOOM, float(value)))

    def _capture_anchor(self) -> tuple[int, float] | None:
        if not self.layout:
            return None
        top = self.canvas.canvasy(0)
        for item in self.layout:
            if item["y"] + item["h"] >= top:
                frac = (top - item["y"]) / max(1.0, item["h"])
                return item["n"], max(0.0, min(1.0, frac))
        return self.layout[-1]["n"], 1.0

    def _relayout(self, preserve: bool = True) -> None:
        if self.doc is None or self.doc.page_count == 0:
            return
        canvas_w, canvas_h = self._canvas_size()
        if canvas_w < 80 or canvas_h < 80:
            return
        anchor = self._capture_anchor() if preserve else None
        ref_page = self._reference_page()
        if self.fit_mode == "width":
            self.zoom = self._compute_fit_zoom(ref_page)
        elif self.fit_mode == "page":
            self.zoom = self._compute_fit_zoom(ref_page)
        else:
            self.zoom = self._clamp_zoom(self.zoom)
        scale = BASE_SCALE * self.zoom

        entries: list[dict] = []
        max_page_w = 0
        y = PAGE_MARGIN_TOP
        for number in range(self.doc.page_count):
            base_w_pt, base_h_pt = self.doc.page_size(number, 0)
            base_w = max(1, int(round(base_w_pt * scale)))
            base_h = max(1, int(round(base_h_pt * scale)))
            if self.view_rotation in (90, 270):
                width, height = base_h, base_w
            else:
                width, height = base_w, base_h
            max_page_w = max(max_page_w, width)
            entry = {
                "n": number,
                "x": 0,
                "y": y,
                "w": width,
                "h": height,
                "base_w": base_w,
                "base_h": base_h,
                "page_w": base_w_pt,
                "page_h": base_h_pt,
                "sx": base_w / base_w_pt,
                "sy": base_h / base_h_pt,
                "scale": scale,
            }
            entries.append(entry)
            y += height + PAGE_GAP
        total_h = y - PAGE_GAP + PAGE_MARGIN_BOTTOM
        self.content_width = max(canvas_w, max_page_w + 2 * PAGE_MARGIN_X)
        for item in entries:
            item["x"] = (self.content_width - item["w"]) / 2.0
        self.layout = entries

        region_h = max(canvas_h, total_h)
        self.canvas.configure(scrollregion=(0, 0, self.content_width, region_h))
        self.canvas.delete("all")
        self._draw_pages()

        # Horizontal scrollbar only when the page is wider than the window.
        if self.content_width > canvas_w + 1:
            self.hscroll.grid()
        else:
            self.hscroll.grid_remove()
            self.canvas.xview_moveto(0.0)

        if anchor is not None:
            number, frac = anchor
            if 0 <= number < len(entries):
                target = entries[number]["y"] + frac * entries[number]["h"]
                self.canvas.yview_moveto(max(0.0, min(1.0, target / region_h)))
        elif not preserve:
            self.canvas.yview_moveto(0.0)

        self.after_idle(self._center_horizontally)
        self._schedule_render()
        self._schedule_notify()

    def _center_horizontally(self) -> None:
        canvas_w, _ = self._canvas_size()
        if self.content_width > canvas_w + 1:
            fraction = ((self.content_width - canvas_w) / 2.0) / self.content_width
            self.canvas.xview_moveto(max(0.0, min(1.0, fraction)))
        else:
            self.canvas.xview_moveto(0.0)

    def fit_width(self) -> None:
        self.fit_mode = "width"
        self._relayout(preserve=True)

    def fit_page(self) -> None:
        self.fit_mode = "page"
        self._relayout(preserve=True)

    def actual_size(self) -> None:
        self.fit_mode = None
        self.zoom = 1.0
        self._relayout(preserve=True)

    def set_zoom(self, zoom: float) -> None:
        self.fit_mode = None
        self.zoom = self._clamp_zoom(zoom)
        self._relayout(preserve=True)

    def zoom_in(self) -> None:
        current = self.zoom
        target = next((z for z in ZOOM_STEPS if z > current + 1e-6), MAX_ZOOM)
        self.set_zoom(target)

    def zoom_out(self) -> None:
        current = self.zoom
        target = next((z for z in reversed(ZOOM_STEPS) if z < current - 1e-6), MIN_ZOOM)
        self.set_zoom(target)

    def rotate_cw(self) -> None:
        self.view_rotation = (self.view_rotation + 90) % 360
        self.clear_selection()
        self._relayout(preserve=True)

    def rotate_ccw(self) -> None:
        self.view_rotation = (self.view_rotation - 90) % 360
        self.clear_selection()
        self._relayout(preserve=True)

    def reset_rotation(self) -> None:
        self.view_rotation = 0
        self.clear_selection()
        self._relayout(preserve=True)

    # ------------------------------------------------------------------
    # Page navigation
    # ------------------------------------------------------------------
    def current_page(self) -> int:
        if self.doc is None or not self.layout:
            return 0
        top = self.canvas.canvasy(0)
        for item in self.layout:
            if item["y"] + item["h"] > top + 1:
                return item["n"]
        return self.doc.page_count - 1

    def _page_at_canvas_y(self, widget_y: float) -> int | None:
        if not self.layout:
            return None
        absolute = self.canvas.canvasy(widget_y)
        for item in self.layout:
            if item["y"] <= absolute <= item["y"] + item["h"]:
                return item["n"]
        # In a gap, pick the closer page.
        for item in self.layout:
            if item["y"] > absolute:
                return max(0, item["n"] - 1)
        return self.doc.page_count - 1 if self.doc else None

    def scroll_to_page(self, number: int, top: bool = True) -> None:
        if self.doc is None or not self.layout:
            return
        number = max(0, min(int(number), self.doc.page_count - 1))
        item = self.layout[number]
        region_h = max(self.canvas.winfo_height(), int(self.canvas.cget("scrollregion").split()[3]))
        target = item["y"] - (12 if top else 0)
        self.canvas.yview_moveto(max(0.0, min(1.0, target / max(1, region_h))))
        self._schedule_notify()
        self._schedule_render()

    def next_page(self) -> None:
        self.scroll_to_page(min(self.current_page() + 1, self.doc.page_count - 1))

    def previous_page(self) -> None:
        self.scroll_to_page(max(0, self.current_page() - 1))

    def first_page(self) -> None:
        self.scroll_to_page(0)

    def last_page(self) -> None:
        self.scroll_to_page(self.doc.page_count - 1)

    def _notify(self, force: bool = False) -> None:
        if self.doc is None:
            return
        page = self.current_page()
        zoom_pct = round(self.zoom * 100, 1)
        if force or page != self._last_page or zoom_pct != self._last_zoom:
            self._last_page = page
            self._last_zoom = zoom_pct
            self.app.on_page_changed(page, zoom_pct)

    def _schedule_notify(self) -> None:
        if self._notify_after is not None:
            self.after_cancel(self._notify_after)
        self._notify_after = self.after(80, self._run_notify)

    def _run_notify(self) -> None:
        self._notify_after = None
        self._notify()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _draw_pages(self) -> None:
        palette = self.app.palette
        self.canvas.delete("all")
        for item in self.layout:
            number = item["n"]
            x, y, width, height = item["x"], item["y"], item["w"], item["h"]
            shadow = palette["shadow"]
            self.canvas.create_rectangle(x + 3, y + 4, x + width + 3, y + height + 4,
                                         fill=shadow, outline=shadow, tags=("page", f"p{number}"))
            self.canvas.create_rectangle(x, y, x + width, y + height,
                                         fill="#ffffff", outline=palette["page_border"],
                                         width=1, tags=("page", f"pagebg{number}", f"p{number}"))
            self.canvas.create_text(x, y - 11, text=str(number + 1),
                                    anchor="w", font=("Segoe UI", 8),
                                    fill=palette["muted"], tags=("page", f"plabel{number}", f"p{number}"))
            self.canvas.create_text(x + width / 2, y + height / 2,
                                    text=f"Page {number + 1}\nrendering…",
                                    font=("Segoe UI", 9), fill="#9ba0a8",
                                    tags=("page", f"ph{number}", f"p{number}"))
        self._schedule_render()

    def _visible_pages(self, preload: bool = True) -> list[int]:
        if not self.layout:
            return []
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        lo = top - (220 if preload else 0)
        hi = top + height + (420 if preload else 0)
        return [item["n"] for item in self.layout
                if item["y"] + item["h"] >= lo and item["y"] <= hi]

    def _cache_key(self, number: int) -> tuple:
        item = self.layout[number]
        return number, round(item["scale"], 4), self.view_rotation

    def _schedule_render(self) -> None:
        if self._render_after is not None:
            self.after_cancel(self._render_after)
            self._render_after = None
        if self.doc is not None:
            self._render_after = self.after(55, self._render_visible)

    def _render_visible(self) -> None:
        self._render_after = None
        if self.doc is None or not self.layout:
            return
        pending: list[int] = []
        for number in self._visible_pages():
            if number >= len(self.layout):
                continue
            key = self._cache_key(number)
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                self._place_image(number, entry)
            elif not self.canvas.find_withtag(f"img{number}"):
                pending.append(number)
        if pending:
            # Render at most two pages per idle slice. Large zoomed pages take
            # longer; one page per slice keeps the window responsive.
            limit = 2 if self.zoom <= 2.0 else 1
            for number in pending[:limit]:
                self._render_page(number)
            if len(pending) > limit:
                self._render_after = self.after(70, self._render_visible)
        # Search highlights are drawn when a page image appears.

    def _render_page(self, number: int) -> None:
        if self.doc is None:
            return
        item = self.layout[number]
        try:
            image = self.doc.render_page(number, item["scale"], self.view_rotation)
            if image.size != (item["w"], item["h"]):
                # Rounding differences of one pixel are invisible; larger
                # differences can happen when a crop box is unusual.
                if abs(image.width - item["w"]) > 2 or abs(image.height - item["h"]) > 2:
                    image = image.resize((item["w"], item["h"]), Image.Resampling.BILINEAR)
            photo = ImageTk.PhotoImage(image=image)
            nbytes = image.width * image.height * 4 * 2
            entry = {"photo": photo, "image": image, "bytes": nbytes}
            key = self._cache_key(number)
            self._cache[key] = entry
            self._cache.move_to_end(key)
            self._cache_bytes += nbytes
            while self._cache_bytes > CACHE_LIMIT and len(self._cache) > 1:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= old["bytes"]
            self._place_image(number, entry)
        except Exception as exc:
            self.app.set_status(f"Could not render page {number + 1}: {exc}")
            self.after(1500, lambda n=number: self._retry_page(n))

    def _retry_page(self, number: int) -> None:
        if self.doc is not None and self.canvas.find_withtag(f"ph{number}"):
            self._schedule_render()

    def _place_image(self, number: int, entry: dict) -> None:
        if number >= len(self.layout):
            return
        item = self.layout[number]
        if self.canvas.find_withtag(f"img{number}"):
            return
        self.canvas.delete(f"ph{number}")
        self.canvas.create_image(item["x"], item["y"], anchor="nw",
                                 image=entry["photo"],
                                 tags=("image", f"img{number}", f"p{number}"))
        self._draw_overlays(number)

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------
    def _map_rect(self, number: int, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """Map a PDF page rectangle to absolute canvas coordinates."""
        if number >= len(self.layout) or self.doc is None:
            return (0.0, 0.0, 0.0, 0.0)
        item = self.layout[number]
        page_rect = self.doc.page(number).rect
        sx, sy = item["sx"], item["sy"]
        x0 = (rect[0] - page_rect.x0) * sx
        y0 = (rect[1] - page_rect.y0) * sy
        x1 = (rect[2] - page_rect.x0) * sx
        y1 = (rect[3] - page_rect.y0) * sy
        points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        base_w, base_h = item["base_w"], item["base_h"]
        rotation = self.view_rotation % 360
        mapped = []
        for px, py in points:
            if rotation == 90:
                nx, ny = base_h - py, px
            elif rotation == 180:
                nx, ny = base_w - px, base_h - py
            elif rotation == 270:
                nx, ny = py, base_w - px
            else:
                nx, ny = px, py
            mapped.append((item["x"] + nx, item["y"] + ny))
        xs = [p[0] for p in mapped]
        ys = [p[1] for p in mapped]
        return min(xs), min(ys), max(xs), max(ys)

    def _screen_to_page(self, number: int, x: float, y: float) -> tuple[float, float] | None:
        """Convert absolute canvas coordinates to PDF page coordinates."""
        if self.doc is None or number >= len(self.layout):
            return None
        item = self.layout[number]
        nx = x - item["x"]
        ny = y - item["y"]
        base_w, base_h = item["base_w"], item["base_h"]
        rotation = self.view_rotation % 360
        if rotation == 90:
            bx, by = ny, base_h - nx
        elif rotation == 180:
            bx, by = base_w - nx, base_h - ny
        elif rotation == 270:
            bx, by = base_w - ny, nx
        else:
            bx, by = nx, ny
        page_rect = self.doc.page(number).rect
        return page_rect.x0 + bx / item["sx"], page_rect.y0 + by / item["sy"]

    @staticmethod
    def _intersects(a: tuple, b: tuple) -> bool:
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    # ------------------------------------------------------------------
    # Overlays: search and selection
    # ------------------------------------------------------------------
    def set_search_hits(self, hits: dict[int, list[tuple]]) -> None:
        self.search_hits = hits
        self.current_hit = None
        for number in self._visible_pages():
            self._draw_overlays(number)

    def set_current_hit(self, page: int, rect: tuple) -> None:
        old = self.current_hit[0] if self.current_hit else None
        self.current_hit = (page, rect)
        if old is not None and old != page:
            self._draw_overlays(old)
        self._draw_overlays(page)

    def jump_to_hit(self, page: int, rect: tuple) -> None:
        if page >= len(self.layout):
            return
        self.set_current_hit(page, rect)
        screen = self._map_rect(page, rect)
        region_h = max(1, self.canvas.winfo_height())
        try:
            region_h = max(region_h, int(float(self.canvas.cget("scrollregion").split()[3])))
        except Exception:
            pass
        target = max(0.0, screen[1] - 90.0)
        self.canvas.yview_moveto(max(0.0, min(1.0, target / region_h)))
        self._schedule_notify()
        self._schedule_render()
        self.after(120, lambda: self._draw_overlays(page))

    def _draw_overlays(self, number: int) -> None:
        self.canvas.delete(f"ov{number}")
        self.canvas.delete(f"sel{number}")
        # Search rectangles.
        for rect in self.search_hits.get(number, []):
            mapped = self._map_rect(number, rect)
            if mapped[2] <= mapped[0] or mapped[3] <= mapped[1]:
                continue
            if self.current_hit and self.current_hit[0] == number and self.current_hit[1] == rect:
                self.canvas.create_rectangle(*mapped, fill="#ffb8a8", outline="#c62828",
                                             width=2, tags=("overlay", f"ov{number}"))
            else:
                self.canvas.create_rectangle(*mapped, fill="#ffe27a", outline="",
                                             stipple="gray12", tags=("overlay", f"ov{number}"))
        # Selection rectangles.
        if self.selection_page == number:
            for rect in self.selection_rects:
                self.canvas.create_rectangle(*rect, fill="#91b8e6", outline="",
                                             stipple="gray25",
                                             tags=("overlay", "selpage", f"sel{number}"))

    # ------------------------------------------------------------------
    # Mouse selection
    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_READ, MODE_SELECT, MODE_EDIT):
            raise ValueError(f"Unknown mode: {mode}")
        self.mode = mode
        self.edit_mode = (mode == MODE_EDIT)
        if mode != MODE_SELECT:
            self.clear_selection()
        self._update_cursor()

    def set_edit_mode(self, enabled: bool) -> None:
        self.set_mode(MODE_EDIT if enabled else MODE_READ)

    def _update_cursor(self) -> None:
        if self.doc is None:
            self.canvas.configure(cursor="")
            return
        if self._placing_signature:
            self.canvas.configure(cursor="crosshair")
        elif self.mode == MODE_EDIT:
            self.canvas.configure(cursor="pencil")
        elif self.mode == MODE_SELECT:
            self.canvas.configure(cursor="xterm")
        else:  # MODE_READ
            self.canvas.configure(cursor="hand2")

    def _on_button_press(self, event) -> None:
        if self.doc is None or not self.layout:
            return
        if self._placing_signature:
            page = self._page_at_canvas_y(event.y)
            if page is None:
                self.app.set_status("Click inside a page to place the signature")
                return
            x = self.canvas.canvasx(event.x)
            y = self.canvas.canvasy(event.y)
            self._place_signature_at(page, x, y)
            return

        # Direct Click-to-Edit Mode or Alt+Click:
        # User clicks anywhere in the PDF document to edit text, table, image, or insert content
        if self.mode == MODE_EDIT or (event.state & 0x0008):
            self.clear_selection()
            self.canvas.focus_set()
            page = self._page_at_canvas_y(event.y)
            if page is None:
                return
            ax = self.canvas.canvasx(event.x)
            ay = self.canvas.canvasy(event.y)
            point = self._screen_to_page(page, ax, ay)
            if point is not None:
                self.app.handle_click_to_edit(page, point)
                return

        # Read Mode: touch screen finger browsing or mouse drag pan
        if self.mode == MODE_READ:
            self._pan_origin = (event.x, event.y)
            self._sel_dragged = False
            self.canvas.scan_mark(event.x, event.y)
            self.canvas.focus_set()
            return

        # Select Mode: mouse / stylus drag text selection
        page = self._page_at_canvas_y(event.y)
        if page is None:
            return
        self.clear_selection()
        self.canvas.focus_set()
        ax = self.canvas.canvasx(event.x)
        ay = self.canvas.canvasy(event.y)
        self._press_point = (ax, ay)
        self._sel_page = page
        self._sel_origin = (ax, ay)
        self._sel_dragged = False
        self._selection_active = True
        self.canvas.configure(cursor="xterm")

    def _on_drag(self, event) -> None:
        if self.mode == MODE_READ:
            if self._pan_origin is not None:
                dx = abs(event.x - self._pan_origin[0])
                dy = abs(event.y - self._pan_origin[1])
                if dx > 3 or dy > 3:
                    self._sel_dragged = True
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            self._after_scroll()
            return

        if self.mode != MODE_SELECT:
            return
        if getattr(self, "_selection_active", False) is False:
            return
        if self._sel_page is None or self._sel_origin is None:
            return
        if self.doc is None:
            return
        ax = self.canvas.canvasx(event.x)
        ay = self.canvas.canvasy(event.y)
        ox, oy = self._sel_origin
        if abs(ax - ox) + abs(ay - oy) > 3:
            self._sel_dragged = True
        selection = (min(ox, ax), min(oy, ay), max(ox, ax), max(oy, ay))
        words = self.doc.words(self._sel_page)
        selected_indices: list[int] = []
        selected_rects: list[tuple[float, float, float, float]] = []
        for index, word in enumerate(words):
            word_rect = (word[0], word[1], word[2], word[3])
            mapped = self._map_rect(self._sel_page, word_rect)
            if self._intersects(mapped, selection):
                selected_indices.append(index)
                selected_rects.append(mapped)
        self._set_selection_from_indices(self._sel_page, words, selected_indices, selected_rects)
        self._draw_overlays(self._sel_page)

    def _set_selection_from_indices(self, page: int, words: list[tuple], indices: list[int],
                                   rects: list[tuple[float, float, float, float]]) -> None:
        self.selection_page = page
        self.selection_rects = rects
        self.selection_page_rects = [
            (words[index][0], words[index][1], words[index][2], words[index][3])
            for index in indices
        ]
        chunks: list[str] = []
        current_line: tuple | None = None
        current: list[str] = []
        for index in indices:
            word = words[index]
            line_key = (word[5], word[6])
            if current_line is not None and line_key != current_line:
                chunks.append(" ".join(current))
                current = []
            current_line = line_key
            current.append(str(word[4]))
        if current:
            chunks.append(" ".join(current))
        self.selection_text = "\n".join(chunks).strip()

    def _on_button_release(self, event) -> None:
        if self.mode == MODE_READ:
            if not self._sel_dragged and self._pan_origin is not None:
                page = self._page_at_canvas_y(event.y)
                if page is not None:
                    ax = self.canvas.canvasx(event.x)
                    ay = self.canvas.canvasy(event.y)
                    self._open_link_at(page, ax, ay)
            self._pan_origin = None
            self._update_cursor()
            return

        if self.mode == MODE_SELECT:
            if getattr(self, "_selection_active", False) is False:
                self._update_cursor()
                return
            self._selection_active = False
            self.canvas.configure(cursor="xterm")
            if not self._sel_dragged and self._sel_page is not None and self._press_point is not None:
                ax = self.canvas.canvasx(event.x)
                ay = self.canvas.canvasy(event.y)
                if self._open_link_at(self._sel_page, ax, ay):
                    self.clear_selection()
                    self._update_cursor()
                    return
            if self.selection_text:
                self.app.set_status(f"Selected {len(self.selection_text)} characters — Ctrl+C to copy")
                if self._sel_dragged:
                    self._show_selection_popup(event.x_root, event.y_root)
            else:
                self.clear_selection()
            self._press_point = None
            self._sel_origin = None
            self._update_cursor()
            return

        self._update_cursor()

    def _show_selection_popup(self, x_root: int, y_root: int) -> None:
        if not self.selection_text or self.doc is None:
            return
        page = self.selection_page if self.selection_page is not None else self.current_page()
        popup = tk.Menu(self, tearoff=0)
        popup.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy_selection)
        popup.add_command(label="✎ Edit Selected Text…", command=lambda: self.app.edit_selection(page))
        popup.add_command(label="✂ Delete (Redact) Selected Text", command=lambda: self.app.delete_selection(page))
        popup.add_separator()
        popup.add_command(label="Select All on Page", accelerator="Ctrl+A", command=self.select_all_page)
        popup.add_command(label="Select Entire Document", accelerator="Ctrl+Shift+A", command=self.select_all_document)
        popup.add_separator()
        popup.add_command(label="Copy Page Text", command=self.copy_page_text)
        popup.add_command(label="Copy Entire Document", command=self.copy_document_text)
        try:
            popup.tk_popup(x_root, y_root)
        finally:
            popup.grab_release()

    def _on_double_click(self, event) -> None:
        if self.doc is None:
            return
        if self.mode == MODE_READ:
            return
        page = self._page_at_canvas_y(event.y)
        if page is None:
            return
        ax = self.canvas.canvasx(event.x)
        ay = self.canvas.canvasy(event.y)
        words = self.doc.words(page)
        for index, word in enumerate(words):
            rect = self._map_rect(page, (word[0], word[1], word[2], word[3]))
            if rect[0] <= ax <= rect[2] and rect[1] <= ay <= rect[3]:
                self._set_selection_from_indices(page, words, [index], [rect])
                self._draw_overlays(page)
                self.app.set_status("Word selected — Ctrl+C to copy")
                self._show_selection_popup(event.x_root, event.y_root)
                return

    def select_all_page(self) -> None:
        if self.doc is None:
            return
        page = self.current_page()
        words = self.doc.words(page)
        rects = [self._map_rect(page, (w[0], w[1], w[2], w[3])) for w in words]
        self._set_selection_from_indices(page, words, list(range(len(words))), rects)
        self.selection_text = self.doc.text(page).strip()
        self._draw_overlays(page)
        self.app.set_status(f"Selected all text on page {page + 1} — Ctrl+C to copy")

    def select_all_document(self) -> None:
        if self.doc is None or self.doc.page_count == 0:
            return
        pages_text: list[str] = []
        for page_idx in range(self.doc.page_count):
            txt = self.doc.text(page_idx).strip()
            if txt:
                pages_text.append(f"--- Page {page_idx + 1} ---\n{txt}")
        page = self.current_page()
        words = self.doc.words(page)
        rects = [self._map_rect(page, (w[0], w[1], w[2], w[3])) for w in words]
        self._set_selection_from_indices(page, words, list(range(len(words))), rects)
        self.selection_text = "\n\n".join(pages_text)
        self._draw_overlays(page)
        self.app.set_status(
            f"Selected entire document ({len(self.selection_text)} characters across {self.doc.page_count} pages) — Ctrl+C to copy"
        )

    def copy_selection(self) -> bool:
        if not self.selection_text:
            if self.doc is not None:
                self.copy_page_text()
                return True
            self.app.set_status("No text selected")
            return False
        try:
            self.clipboard_clear()
            self.clipboard_append(self.selection_text)
            self.app.set_status(f"Copied {len(self.selection_text)} characters")
            return True
        except tk.TclError as exc:
            self.app.set_status(f"Clipboard error: {exc}")
            return False

    def copy_page_text(self) -> None:
        if self.doc is None:
            return
        page = self.current_page()
        text = self.doc.text(page)
        if not text.strip():
            self.app.set_status(f"Page {page + 1} has no extractable text")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.app.set_status(f"Copied text from page {page + 1}")
        except tk.TclError as exc:
            self.app.set_status(f"Clipboard error: {exc}")

    def copy_document_text(self) -> bool:
        if self.doc is None or self.doc.page_count == 0:
            self.app.set_status("No document open")
            return False
        pages_text: list[str] = []
        for page_idx in range(self.doc.page_count):
            txt = self.doc.text(page_idx).strip()
            if txt:
                pages_text.append(f"--- Page {page_idx + 1} ---\n{txt}")
        if not pages_text:
            self.app.set_status("Document contains no extractable text")
            return False
        full_text = "\n\n".join(pages_text)
        try:
            self.clipboard_clear()
            self.clipboard_append(full_text)
            self.app.set_status(f"Copied entire document ({len(full_text)} characters across {self.doc.page_count} pages)")
            return True
        except tk.TclError as exc:
            self.app.set_status(f"Clipboard error: {exc}")
            return False

    def clear_selection(self) -> None:
        self.canvas.delete("selpage")
        self.selection_page = None
        self.selection_rects = []
        self.selection_page_rects = []
        self.selection_text = ""

    def _on_motion_hover(self, event) -> None:
        if self.doc is None:
            self.canvas.configure(cursor="")
            return
        if self._placing_signature:
            self.canvas.configure(cursor="crosshair")
            return
        if self.mode == MODE_EDIT:
            self.canvas.configure(cursor="pencil")
            return
        page = self._page_at_canvas_y(event.y)
        if page is None:
            self.canvas.configure(cursor="")
            return
        ax = self.canvas.canvasx(event.x)
        ay = self.canvas.canvasy(event.y)
        if self._link_at(page, ax, ay):
            self.canvas.configure(cursor="hand2")
            return
        if self.mode == MODE_READ:
            self.canvas.configure(cursor="hand2")
        else:
            self.canvas.configure(cursor="xterm")

    def _link_at(self, page: int, x: float, y: float) -> dict | None:
        if self.doc is None:
            return None
        for link in self.doc.links(page):
            raw = link.get("from")
            if raw is None:
                continue
            mapped = self._map_rect(page, (raw.x0, raw.y0, raw.x1, raw.y1))
            if mapped[0] <= x <= mapped[2] and mapped[1] <= y <= mapped[3]:
                return link
        return None

    @staticmethod
    def _sanitize_link_text(text: str, max_len: int = 200) -> str:
        """Strip control characters, tabs, and newlines to prevent dialog spoofing."""
        cleaned = "".join(ch if (ch.isprintable() and ch not in "\r\n\t") else " " for ch in text)
        cleaned = " ".join(cleaned.split())
        return cleaned[:max_len] + ("…" if len(cleaned) > max_len else "")

    def _open_link_at(self, page: int, x: float, y: float) -> bool:
        link = self._link_at(page, x, y)
        if link is None:
            return False
        kind = link.get("kind")
        uri = link.get("uri")
        if uri:
            try:
                parsed = urlsplit(uri)
                scheme = parsed.scheme.lower()
            except Exception:
                self.app.set_status("Malformed link rejected for security")
                return True

            # Block dangerous schemes (file:, javascript:, data:, ms-msdt:, etc.) and UNC/relative paths
            if scheme not in ("http", "https"):
                safe_display = self._sanitize_link_text(uri)
                if scheme in ("file", "javascript", "vbscript", "data", "ms-msdt", "cmd", "powershell") or uri.startswith(("//", "\\\\")):
                    messagebox.showwarning(
                        "Blocked Link",
                        f"PDF Lite blocked opening this link for security:\n\n{safe_display}",
                    )
                else:
                    messagebox.showwarning(
                        "Unsupported Link",
                        f"Only web links (http:// and https://) are supported:\n\n{safe_display}",
                    )
                return True

            # Disallow UNC-style authority (e.g. http://\evil.com or missing host)
            if not parsed.netloc or "\\" in parsed.netloc:
                self.app.set_status("Invalid link format rejected for security")
                return True

            safe_display = self._sanitize_link_text(uri)
            if messagebox.askyesno("Open external link", f"Open this link in your browser?\n\n{safe_display}"):
                try:
                    webbrowser.open(uri)
                except Exception as exc:
                    messagebox.showerror("Link error", str(exc))
            return True
        target = link.get("page")
        if target is not None:
            try:
                self.app.go_to_page(int(target) + 1, remember=True)
                return True
            except (TypeError, ValueError):
                return False
        if kind in (fitz.LINK_LAUNCH, fitz.LINK_GOTOR):
            self.app.set_status("This link targets an external file; it was blocked for security")
            return True
        return False

    def _on_context_menu(self, event) -> None:
        if self.doc is None:
            return
        if self._placing_signature:
            page = self._page_at_canvas_y(event.y)
            if page is None:
                return
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Cancel Signature Placement",
                             command=self.cancel_signature_placement)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return
        page = self._page_at_canvas_y(event.y)
        if page is None:
            return
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        page_coords = self._screen_to_page(page, cx, cy) or (50.0, 100.0)
        elem = get_element_at_point(self.doc, page, page_coords)

        menu = tk.Menu(self, tearoff=0)

        # Context-aware direct click edit options at the top
        if elem.get("type") == "text_line":
            txt_preview = elem.get("text", "")[:28]
            menu.add_command(
                label=f"✎ Edit Clicked Text: “{txt_preview}…”",
                command=lambda: self.app.handle_click_to_edit(page, page_coords),
            )
            menu.add_separator()
        elif elem.get("type") == "image":
            menu.add_command(
                label="🖼 Edit / Replace Clicked Image…",
                command=lambda: self.app.handle_click_to_edit(page, page_coords),
            )
            menu.add_separator()
        elif elem.get("type") == "table":
            menu.add_command(
                label="📊 Edit Clicked Table…",
                command=lambda: self.app.handle_click_to_edit(page, page_coords),
            )
            menu.add_separator()

        state = "normal" if self.selection_text else "disabled"
        menu.add_command(label="Copy", command=self.copy_selection, state=state, accelerator="Ctrl+C")
        menu.add_command(label="Copy Page Text", command=self.copy_page_text, accelerator="Ctrl+Shift+C")
        menu.add_command(label="Copy Entire Document", command=self.copy_document_text)
        menu.add_separator()
        menu.add_command(label="Select All on Page", command=self.select_all_page, accelerator="Ctrl+A")
        menu.add_command(label="Select Entire Document", command=self.select_all_document, accelerator="Ctrl+Shift+A")
        menu.add_separator()

        # Selection editing / deleting options
        if self.selection_text:
            menu.add_command(label="✎ Edit Selected Text…", command=lambda: self.app.edit_selection(page))
            menu.add_command(label="✂ Delete (Redact) Selected Text", command=lambda: self.app.delete_selection(page))
            menu.add_separator()

        # Mode toggles
        menu.add_command(label="✋ Read Mode (Touch / Pan)", command=self.app.set_read_mode, accelerator="Ctrl+1")
        menu.add_command(label="I Select Mode (Text Selection)", command=self.app.set_select_mode, accelerator="Ctrl+2")
        if hasattr(self.app, "edit_mode_var") and self.app.edit_mode_var is not None:
            menu.add_checkbutton(label="✎ Click-to-Edit Mode", variable=self.app.edit_mode_var,
                                 command=self.app.toggle_edit_mode, accelerator="Ctrl+E")
        menu.add_separator()

        menu.add_command(label="+ Insert Text Box Here…", command=lambda: self.app.add_text_at(page, page_coords))
        menu.add_command(label="🖼 Insert Image Here…", command=lambda: self.app.add_image_at(page, page_coords))
        menu.add_command(label="📊 Insert Table Here…", command=lambda: self.app.add_table_at(page, page_coords))
        menu.add_separator()
        menu.add_command(label="Manage Images on Page…", command=lambda: self.app.manage_images(page))
        menu.add_command(label="Edit Tables on Page…", command=lambda: self.app.edit_tables_on_page(page))
        menu.add_separator()

        menu.add_command(label="Find…", command=self.app.show_find, accelerator="Ctrl+F")
        menu.add_command(label="Fit Width", command=self.fit_width)
        menu.add_command(label="Fit Page", command=self.fit_page)
        menu.add_command(label="Actual Size", command=self.actual_size, accelerator="Ctrl+0")
        link = self._link_at(page, cx, cy)
        if link is not None:
            menu.add_separator()
            label = link.get("uri") or "Go to linked page"
            menu.add_command(label=f"Open Link — {label}",
                             command=lambda: self._open_link_at(page, cx, cy))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # Hand-drawn signature placement
    # ------------------------------------------------------------------
    def begin_signature_placement(self, strokes: list[list[tuple[float, float]]],
                                  ratio: float, width_pt: float) -> None:
        self._signature_strokes = strokes
        self._signature_ratio = max(0.05, min(2.0, ratio))
        self._signature_width = max(40.0, min(300.0, width_pt))
        self._placing_signature = True
        self.clear_selection()
        self.canvas.configure(cursor="crosshair")
        self.app.set_status(
            "Click on a page to place the signature. Esc or right-click cancels.", 15000)

    def cancel_signature_placement(self, silent: bool = False) -> None:
        if not self._placing_signature:
            return
        self._placing_signature = False
        self._signature_strokes = []
        self.canvas.configure(cursor="")
        if not silent:
            self.app.set_status("Signature placement cancelled")

    def _place_signature_at(self, number: int, x: float, y: float) -> None:
        if self.doc is None or not self._signature_strokes:
            return
        point = self._screen_to_page(number, x, y)
        if point is None:
            return
        page_rect = self.doc.page(number).rect
        margin = 6.0
        width = min(self._signature_width, max(40.0, page_rect.width * 0.5))
        height = max(20.0, width * self._signature_ratio)
        if width + 2 * margin > page_rect.width:
            width = max(20.0, page_rect.width - 2 * margin)
            height = max(10.0, width * self._signature_ratio)
        if height + 2 * margin > page_rect.height:
            height = max(10.0, page_rect.height - 2 * margin)
            width = max(10.0, height / max(0.05, self._signature_ratio))
        start_x = max(page_rect.x0 + margin,
                      min(point[0], page_rect.x1 - width - margin))
        start_y = max(page_rect.y0 + margin,
                      min(point[1], page_rect.y1 - height - margin))
        pdf_strokes: list[list[tuple[float, float]]] = []
        for stroke in self._signature_strokes:
            converted = [(start_x + px * width, start_y + py * height)
                         for px, py in stroke]
            if len(converted) >= 2:
                pdf_strokes.append(converted)
        if not pdf_strokes:
            self.app.set_status("Signature is too small to place")
            return
        try:
            self.doc.add_ink_signature(number, pdf_strokes)
        except Exception as exc:
            self.app.set_status(f"Could not add signature: {exc}")
            return
        self._placing_signature = False
        self._signature_strokes = []
        self.canvas.configure(cursor="")
        self._invalidate_page(number)
        self.app.mark_modified()
        self.app.set_status(
            f"Signature added to page {number + 1} — Ctrl+S to save a signed copy", 9000)

    def _invalidate_page(self, number: int) -> None:
        keys = [key for key in self._cache if key[0] == number]
        for key in keys:
            entry = self._cache.pop(key)
            self._cache_bytes -= entry["bytes"]
        self.canvas.delete(f"img{number}")
        self.canvas.delete(f"ov{number}")
        self._schedule_render()

    # ------------------------------------------------------------------
    # Events and scrolling
    # ------------------------------------------------------------------
    def _on_configure(self, _event=None) -> None:
        size = self._canvas_size()
        if size == self._last_canvas_size:
            return
        self._last_canvas_size = size
        if self.doc is None:
            self._draw_empty_state()
            return
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(90, self._run_resize)

    def _run_resize(self) -> None:
        self._resize_after = None
        if self.doc is not None:
            self._relayout(preserve=True)

    def _on_mousewheel(self, event) -> str:
        if self.doc is None:
            return "break"
        delta = event.delta
        if delta == 0:
            return "break"
        steps = 3 if abs(delta) < 120 else max(1, abs(delta) // 40)
        direction = -1 if delta > 0 else 1
        self.canvas.yview_scroll(direction * steps, "units")
        self._after_scroll()
        return "break"

    def _on_shift_wheel(self, event) -> str:
        if self.doc is None:
            return "break"
        delta = event.delta
        if delta == 0:
            return "break"
        steps = 3 if abs(delta) < 120 else max(1, abs(delta) // 40)
        direction = -1 if delta > 0 else 1
        self.canvas.xview_scroll(direction * steps, "units")
        return "break"

    def _on_ctrl_wheel(self, event) -> str:
        if self.doc is None:
            return "break"
        factor = 1.10 if event.delta > 0 else 1 / 1.10
        self.set_zoom(self.zoom * factor)
        return "break"

    def _on_linux_wheel(self, event, direction: int) -> str:
        if self.doc is None:
            return "break"
        self.canvas.yview_scroll(direction * 3, "units")
        self._after_scroll()
        return "break"

    def _after_scroll(self) -> None:
        self._schedule_notify()
        if self._render_after is not None:
            self.after_cancel(self._render_after)
            self._render_after = None
        self._render_after = self.after(60, self._render_visible)

    def _update_vscroll(self, first: str, last: str) -> None:
        self.vscroll.set(first, last)


def repair_placeholder() -> None:
    """Kept for compatibility; no-op."""
    return None

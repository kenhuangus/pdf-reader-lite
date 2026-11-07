"""Interactive PDF content editing: text, tables, and images."""

from __future__ import annotations

import io
import math
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import TYPE_CHECKING

import fitz
from PIL import Image

if TYPE_CHECKING:
    from .core import PdfDocument


# Standard base-14 PDF fonts
FONT_CHOICES = [
    ("Helvetica", "helv"),
    ("Helvetica Bold", "hebo"),
    ("Helvetica Oblique", "heit"),
    ("Times Roman", "times"),
    ("Times Bold", "tibo"),
    ("Times Italic", "tiit"),
    ("Courier", "cour"),
    ("Courier Bold", "cobo"),
]

# Image dimension and pixel constraints to prevent decompression bombs and memory exhaustion
MAX_IMG_DIM = 4096
MAX_IMG_PIXELS = 32_000_000


def _validate_finite_rect(rect: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    """Validate and normalize bounding box coordinates."""
    if len(rect) != 4:
        raise ValueError("Rect must contain exactly 4 coordinates")
    x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
    if not (math.isfinite(x0) and math.isfinite(y0) and math.isfinite(x1) and math.isfinite(y1)):
        raise ValueError("Rect coordinates must be finite numbers")
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _validate_finite_point(point: tuple[float, float] | list[float]) -> tuple[float, float]:
    """Validate that point coordinates are finite numbers."""
    if len(point) != 2:
        raise ValueError("Point must contain exactly 2 coordinates")
    x, y = float(point[0]), float(point[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("Point coordinates must be finite numbers")
    return (x, y)


# ----------------------------------------------------------------------
# Low-level PDF manipulation routines
# ----------------------------------------------------------------------

def replace_text_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    rect: tuple[float, float, float, float],
    new_text: str,
    fontname: str = "helv",
    fontsize: float = 11.0,
    text_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fill_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> tuple[float, float, float, float]:
    """Redact old text in rect and insert new text with auto-fitting."""
    valid_rect = _validate_finite_rect(rect)
    fontsize = max(4.0, min(144.0, float(fontsize)))
    page = doc.page(page_num)
    orig_r = fitz.Rect(valid_rect)

    # Calculate required bounding box dimensions
    lines = new_text.splitlines() or [""]
    max_line_len = max(fitz.get_text_length(line, fontname=fontname, fontsize=fontsize) for line in lines)
    needed_w = max(orig_r.width, max_line_len + 6.0)
    needed_h = max(orig_r.height, fontsize * 1.35 * len(lines) + 4.0)
    target_r = fitz.Rect(orig_r.x0, orig_r.y0, orig_r.x0 + needed_w, orig_r.y0 + needed_h)

    # Redact target area to ensure clean background for new text
    page.add_redact_annot(target_r, fill=fill_color)
    page.apply_redactions()

    # Insert updated text
    page.insert_textbox(
        target_r,
        new_text,
        fontname=fontname,
        fontsize=fontsize,
        color=text_color,
    )
    doc.invalidate_page_cache(page_num)
    return (target_r.x0, target_r.y0, target_r.x1, target_r.y1)


def replace_selected_text_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    rect: tuple[float, float, float, float],
    new_text: str,
    fontname: str = "helv",
    fontsize: float = 11.0,
    text_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fill_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> tuple[float, float, float, float]:
    """Redact strictly the selected text bounding box and insert updated text."""
    valid_rect = _validate_finite_rect(rect)
    fontsize = max(4.0, min(144.0, float(fontsize)))
    page = doc.page(page_num)
    orig_r = fitz.Rect(valid_rect)

    # Redact ONLY the exact selected bounding box to preserve surrounding words
    page.add_redact_annot(orig_r, fill=fill_color)
    page.apply_redactions()

    if new_text.strip():
        lines = new_text.splitlines() or [""]
        max_line_len = max(fitz.get_text_length(line, fontname=fontname, fontsize=fontsize) for line in lines)
        needed_w = max(orig_r.width, max_line_len + 2.0)
        needed_h = max(orig_r.height, fontsize * 1.35 * len(lines))
        # Provide vertical tolerance to prevent ascender clipping in PyMuPDF
        target_r = fitz.Rect(
            orig_r.x0,
            orig_r.y0 - 1.0,
            orig_r.x0 + needed_w,
            max(orig_r.y1 + 1.5, orig_r.y0 + needed_h + 1.0),
        )
        rc = page.insert_textbox(
            target_r,
            new_text,
            fontname=fontname,
            fontsize=fontsize,
            color=text_color,
        )
        if rc < 0:
            rc2 = page.insert_textbox(
                target_r,
                new_text,
                fontname=fontname,
                fontsize=max(4.0, fontsize - 1.0),
                color=text_color,
            )
            if rc2 < 0:
                page.insert_text(
                    (orig_r.x0, min(page.rect.height, orig_r.y1 - 1.0)),
                    new_text,
                    fontname=fontname,
                    fontsize=fontsize,
                    color=text_color,
                )

    doc.invalidate_page_cache(page_num)
    return (orig_r.x0, orig_r.y0, orig_r.x1, orig_r.y1)


def get_text_properties_at_rect(
    doc: "PdfDocument",
    page_num: int,
    rect: tuple[float, float, float, float],
) -> dict:
    """Extract font properties (fontname, fontsize, color) for text overlapping rect."""
    valid_rect = _validate_finite_rect(rect)
    page = doc.page(page_num)
    target = fitz.Rect(valid_rect)
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_rect = fitz.Rect(span["bbox"])
                    if span_rect.intersects(target):
                        fontsize = float(span.get("size", 11.0))
                        color_int = span.get("color", 0)
                        r = ((color_int >> 16) & 0xFF) / 255.0
                        g = ((color_int >> 8) & 0xFF) / 255.0
                        b = (color_int & 0xFF) / 255.0
                        fontname_raw = str(span.get("font", "")).lower()
                        fontname = "helv"
                        if "times" in fontname_raw:
                            fontname = "tibo" if "bold" in fontname_raw else "times"
                        elif "courier" in fontname_raw:
                            fontname = "cobo" if "bold" in fontname_raw else "cour"
                        elif "bold" in fontname_raw:
                            fontname = "hebo"
                        elif "oblique" in fontname_raw or "italic" in fontname_raw:
                            fontname = "heit"
                        return {
                            "fontname": fontname,
                            "fontsize": fontsize,
                            "color": (r, g, b),
                        }
    return {
        "fontname": "helv",
        "fontsize": 11.0,
        "color": (0.0, 0.0, 0.0),
    }


def insert_text_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    rect_or_point: tuple[float, float, float, float] | tuple[float, float],
    text: str,
    fontname: str = "helv",
    fontsize: float = 11.0,
    text_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bg_color: tuple[float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Insert a new text box or line on the page."""
    fontsize = max(4.0, min(144.0, float(fontsize)))
    page = doc.page(page_num)
    lines = text.splitlines() or [""]
    max_line_len = max(fitz.get_text_length(line, fontname=fontname, fontsize=fontsize) for line in lines)
    needed_w = max_line_len + 10.0
    needed_h = fontsize * 1.35 * len(lines) + 6.0

    if len(rect_or_point) == 2:
        x, y = _validate_finite_point(rect_or_point)
        target_r = fitz.Rect(x, y, x + needed_w, y + needed_h)
    else:
        valid_rect = _validate_finite_rect(rect_or_point)
        r = fitz.Rect(valid_rect)
        target_r = fitz.Rect(r.x0, r.y0, max(r.x1, r.x0 + needed_w), max(r.y1, r.y0 + needed_h))

    if bg_color is not None:
        page.draw_rect(target_r, fill=bg_color, color=None)

    page.insert_textbox(
        target_r,
        text,
        fontname=fontname,
        fontsize=fontsize,
        color=text_color,
    )
    doc.invalidate_page_cache(page_num)
    return (target_r.x0, target_r.y0, target_r.x1, target_r.y1)


def delete_content_rect(
    doc: "PdfDocument",
    page_num: int,
    rect: tuple[float, float, float, float],
    fill_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> None:
    """Redact and remove content inside rect."""
    valid_rect = _validate_finite_rect(rect)
    page = doc.page(page_num)
    page.add_redact_annot(fitz.Rect(valid_rect), fill=fill_color)
    page.apply_redactions()
    doc.invalidate_page_cache(page_num)


def insert_image_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    image_path: str,
    rect: tuple[float, float, float, float],
) -> None:
    """Insert an image file onto a page using Pillow normalization and decompression safety."""
    if str(image_path).startswith(("\\\\", "//")):
        raise ValueError("Remote network (UNC) paths are blocked for security")
    valid_rect = _validate_finite_rect(rect)
    abs_path = os.path.abspath(image_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    page = doc.page(page_num)
    with Image.open(abs_path) as img:
        # Check against decompression bomb limits
        if (img.width * img.height > MAX_IMG_PIXELS) or (img.width > MAX_IMG_DIM) or (img.height > MAX_IMG_DIM):
            img = img.copy()
            img.thumbnail((MAX_IMG_DIM, MAX_IMG_DIM), Image.Resampling.LANCZOS)
        else:
            img = img.copy()

        # Convert to RGB or RGBA PNG bytes
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "transparency" in img.info or img.mode == "PA" else "RGB")

        fmt = "PNG"
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        page.insert_image(fitz.Rect(valid_rect), stream=buf.getvalue())
    doc.invalidate_page_cache(page_num)


def replace_image_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    old_rect: tuple[float, float, float, float],
    new_image_path: str,
) -> None:
    """Replace an existing image rect with a new image."""
    delete_content_rect(doc, page_num, old_rect)
    insert_image_in_pdf(doc, page_num, new_image_path, old_rect)


def get_page_images(doc: "PdfDocument", page_num: int) -> list[dict]:
    """Retrieve metadata and bounding boxes for all images on a page."""
    page = doc.page(page_num)
    raw_images = page.get_images()
    results = []
    seen_rects = set()
    for idx, img_info in enumerate(raw_images):
        xref = img_info[0]
        rects = page.get_image_rects(xref)
        for r in rects:
            tup = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
            if tup in seen_rects:
                continue
            seen_rects.add(tup)
            results.append({
                "index": idx + 1,
                "xref": xref,
                "rect": (r.x0, r.y0, r.x1, r.y1),
                "width": r.width,
                "height": r.height,
            })
    return results


def draw_table_in_pdf(
    doc: "PdfDocument",
    page_num: int,
    rect: tuple[float, float, float, float],
    data: list[list[str]],
    has_header: bool = True,
    fontsize: float = 9.0,
    header_bg: tuple[float, float, float] = (0.91, 0.94, 0.98),
    cell_bg: tuple[float, float, float] = (1.0, 1.0, 1.0),
    alt_bg: tuple[float, float, float] = (0.98, 0.98, 0.99),
    border_color: tuple[float, float, float] = (0.75, 0.78, 0.82),
    text_color: tuple[float, float, float] = (0.12, 0.15, 0.20),
) -> None:
    """Draw a structured table onto the page."""
    if not data or not data[0]:
        return
    valid_rect = _validate_finite_rect(rect)
    fontsize = max(4.0, min(144.0, float(fontsize)))
    page = doc.page(page_num)
    r = fitz.Rect(valid_rect)
    num_rows = len(data)
    num_cols = max(len(row) for row in data)
    cell_w = r.width / max(1, num_cols)
    cell_h = r.height / max(1, num_rows)

    for r_idx, row in enumerate(data):
        is_header = has_header and r_idx == 0
        current_bg = header_bg if is_header else (cell_bg if r_idx % 2 == 0 else alt_bg)
        fontname = "hebo" if is_header else "helv"
        for c_idx in range(num_cols):
            cell_text = str(row[c_idx]) if c_idx < len(row) else ""
            cx0 = r.x0 + c_idx * cell_w
            cy0 = r.y0 + r_idx * cell_h
            cx1 = cx0 + cell_w
            cy1 = cy0 + cell_h
            cell_box = fitz.Rect(cx0, cy0, cx1, cy1)
            page.draw_rect(cell_box, color=border_color, fill=current_bg, width=0.7)
            padding = fitz.Rect(cx0 + 4, cy0 + 3, cx1 - 4, cy1 - 3)
            page.insert_textbox(
                padding,
                cell_text,
                fontname=fontname,
                fontsize=fontsize,
                color=text_color,
            )
    doc.invalidate_page_cache(page_num)


def get_page_tables(doc: "PdfDocument", page_num: int) -> list[dict]:
    """Detect existing tables on a page via PyMuPDF layout analysis."""
    page = doc.page(page_num)
    try:
        tabs = page.find_tables()
    except Exception:
        return []
    tables = []
    for idx, t in enumerate(tabs.tables):
        tables.append({
            "index": idx + 1,
            "bbox": (t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3]),
            "data": t.extract(),
            "rows": len(t.rows),
            "cols": len(t.header.names) if hasattr(t.header, "names") else len(t.extract()[0] if t.extract() else []),
        })
    return tables


def get_element_at_point(doc: "PdfDocument", page_num: int, point: tuple[float, float]) -> dict:
    """Identify if a page coordinate (x, y) is on a table, image, text line, or empty space."""
    px, py = _validate_finite_point(point)
    page = doc.page(page_num)

    # 1. Check tables
    tables = get_page_tables(doc, page_num)
    for tab in tables:
        bx0, by0, bx1, by1 = tab["bbox"]
        if bx0 - 3 <= px <= bx1 + 3 and by0 - 3 <= py <= by1 + 3:
            return {"type": "table", "data": tab}

    # 2. Check images
    images = get_page_images(doc, page_num)
    for img in images:
        ix0, iy0, ix1, iy1 = img["rect"]
        if ix0 <= px <= ix1 and iy0 <= py <= iy1:
            return {"type": "image", "data": img}

    # 3. Check text lines and blocks
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                lx0, ly0, lx1, ly1 = line["bbox"]
                if (lx0 - 4 <= px <= lx1 + 4) and (ly0 - 3 <= py <= ly1 + 3):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    first_span = line["spans"][0] if line.get("spans") else {}
                    fontsize = float(first_span.get("size", 11.0))
                    color_int = first_span.get("color", 0)
                    r = ((color_int >> 16) & 0xFF) / 255.0
                    g = ((color_int >> 8) & 0xFF) / 255.0
                    b = (color_int & 0xFF) / 255.0
                    fontname_raw = str(first_span.get("font", "")).lower()
                    fontname = "helv"
                    if "times" in fontname_raw:
                        fontname = "tibo" if "bold" in fontname_raw else "times"
                    elif "courier" in fontname_raw:
                        fontname = "cobo" if "bold" in fontname_raw else "cour"
                    elif "bold" in fontname_raw:
                        fontname = "hebo"

                    return {
                        "type": "text_line",
                        "text": line_text,
                        "bbox": (lx0, ly0, lx1, ly1),
                        "fontname": fontname,
                        "fontsize": fontsize,
                        "color": (r, g, b),
                    }

    # 4. Empty space
    return {"type": "empty", "point": (px, py)}


# ----------------------------------------------------------------------
# Interactive Dialogs
# ----------------------------------------------------------------------

class InsertChoiceDialog(tk.Toplevel):
    def __init__(self, parent, point: tuple[float, float]) -> None:
        super().__init__(parent)
        self.title("Insert Content")
        self.transient(parent)
        self.resizable(False, False)
        self.result: str | None = None
        px, py = int(point[0]), int(point[1])
        frame = ttk.Frame(self, padding=(22, 16))
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"Click detected on empty space at ({px}, {py}) pt.\nChoose what you would like to insert:",
            font=("Segoe UI", 9, "bold"),
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        btn_box = ttk.Frame(frame)
        btn_box.pack(fill="x", pady=(0, 8))

        def choose(choice: str) -> None:
            self.result = choice
            self.destroy()

        ttk.Button(btn_box, text="✎ Text Box", style="Accent.TButton", command=lambda: choose("text")).pack(side="left", padx=(0, 6))
        ttk.Button(btn_box, text="🖼 Image…", command=lambda: choose("image")).pack(side="left", padx=(0, 6))
        ttk.Button(btn_box, text="📊 Table…", command=lambda: choose("table")).pack(side="left", padx=(0, 12))
        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side="left")

        self.grab_set()
        self.wait_window(self)

class EditTextDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        initial_text: str = "",
        initial_size: float = 11.0,
        initial_font: str = "helv",
        initial_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        title: str = "Edit Text",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(True, True)
        self.result: dict | None = None

        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Text Content:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        self.text_widget = tk.Text(frame, width=50, height=7, wrap="word", font=("Segoe UI", 10))
        self.text_widget.pack(fill="both", expand=True, pady=(0, 10))
        self.text_widget.insert("1.0", initial_text)

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 12))

        ttk.Label(controls, text="Font:").pack(side="left", padx=(0, 4))
        font_name_lookup = {code: name for name, code in FONT_CHOICES}
        default_font_display = font_name_lookup.get(initial_font, "Helvetica")
        self.font_var = tk.StringVar(value=default_font_display)
        font_box = ttk.Combobox(
            controls,
            textvariable=self.font_var,
            values=[name for name, _ in FONT_CHOICES],
            state="readonly",
            width=15,
        )
        font_box.pack(side="left", padx=(0, 12))

        ttk.Label(controls, text="Size:").pack(side="left", padx=(0, 4))
        self.size_var = tk.StringVar(value=str(round(initial_size, 1) if initial_size % 1 else int(initial_size)))
        size_box = ttk.Combobox(
            controls,
            textvariable=self.size_var,
            values=["8", "9", "10", "11", "12", "14", "16", "18", "22", "28", "36", "48"],
            width=5,
        )
        size_box.pack(side="left", padx=(0, 12))

        cr = int(max(0.0, min(1.0, initial_color[0])) * 255)
        cg = int(max(0.0, min(1.0, initial_color[1])) * 255)
        cb = int(max(0.0, min(1.0, initial_color[2])) * 255)
        self.color_hex = f"#{cr:02x}{cg:02x}{cb:02x}"
        fg_text = "#ffffff" if (cr * 0.299 + cg * 0.587 + cb * 0.114) < 128 else "#000000"
        self.color_btn = tk.Button(
            controls,
            text="Color",
            bg=self.color_hex,
            fg=fg_text,
            relief="groove",
            command=self._pick_color,
            width=7,
        )
        self.color_btn.pack(side="left", padx=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Apply Changes", style="Accent.TButton", command=self._on_ok).pack(side="right")

        self.text_widget.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _pick_color(self) -> None:
        chosen = colorchooser.askcolor(self.color_hex, parent=self)[1]
        if chosen:
            self.color_hex = chosen
            cr = int(chosen[1:3], 16)
            cg = int(chosen[3:5], 16)
            cb = int(chosen[5:7], 16)
            fg = "#ffffff" if (cr * 0.299 + cg * 0.587 + cb * 0.114) < 128 else "#000000"
            self.color_btn.configure(bg=chosen, fg=fg)

    def _on_ok(self) -> None:
        text = self.text_widget.get("1.0", "end-1c")
        if not text:
            messagebox.showwarning("Warning", "Text cannot be completely empty.", parent=self)
            return
        font_map = dict(FONT_CHOICES)
        fontname = font_map.get(self.font_var.get(), "helv")
        try:
            size = float(self.size_var.get())
        except ValueError:
            size = 11.0
        size = max(4.0, min(144.0, size))

        r = int(self.color_hex[1:3], 16) / 255.0
        g = int(self.color_hex[3:5], 16) / 255.0
        b = int(self.color_hex[5:7], 16) / 255.0

        self.result = {
            "text": text,
            "fontname": fontname,
            "fontsize": size,
            "color": (r, g, b),
        }
        self.destroy()


class AddImageDialog(tk.Toplevel):
    def __init__(self, parent, page_w: float, page_h: float) -> None:
        super().__init__(parent)
        self.title("Insert Image")
        self.transient(parent)
        self.resizable(False, False)
        self.result: dict | None = None
        self.image_path: str = ""

        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill="both", expand=True)

        file_frame = ttk.Frame(frame)
        file_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(file_frame, text="Image File:").pack(side="left", padx=(0, 6))
        self.path_lbl = ttk.Label(file_frame, text="(None chosen)", width=32, relief="sunken")
        self.path_lbl.pack(side="left", padx=(0, 8), fill="x", expand=True)
        ttk.Button(file_frame, text="Browse…", command=self._browse).pack(side="left")

        dims_frame = ttk.Frame(frame)
        dims_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(dims_frame, text="Width (pt):").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.w_var = tk.StringVar(value="240")
        ttk.Entry(dims_frame, textvariable=self.w_var, width=8).grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(dims_frame, text="Height (pt):").grid(row=0, column=2, padx=4, pady=4, sticky="w")
        self.h_var = tk.StringVar(value="180")
        ttk.Entry(dims_frame, textvariable=self.h_var, width=8).grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(dims_frame, text="Position X:").grid(row=1, column=0, padx=4, pady=4, sticky="w")
        self.x_var = tk.StringVar(value="60")
        ttk.Entry(dims_frame, textvariable=self.x_var, width=8).grid(row=1, column=1, padx=4, pady=4)

        ttk.Label(dims_frame, text="Position Y:").grid(row=1, column=2, padx=4, pady=4, sticky="w")
        self.y_var = tk.StringVar(value="100")
        ttk.Entry(dims_frame, textvariable=self.y_var, width=8).grid(row=1, column=3, padx=4, pady=4)

        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_bar, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_bar, text="Insert Image", style="Accent.TButton", command=self._on_ok).pack(side="right")

        self.grab_set()
        self.wait_window(self)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select Image to Insert",
            filetypes=[
                ("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.ico;*.gif"),
                ("All files", "*.*"),
            ],
        )
        if path:
            if str(path).startswith(("\\\\", "//")):
                messagebox.showwarning("Security Warning", "Remote network (UNC) paths are blocked for security.", parent=self)
                return
            self.image_path = path
            self.path_lbl.configure(text=os.path.basename(path))
            try:
                with Image.open(path) as img:
                    orig_w, orig_h = img.size
                    ratio = orig_w / max(1, orig_h)
                    target_w = min(300.0, float(orig_w))
                    target_h = target_w / ratio
                    self.w_var.set(str(int(target_w)))
                    self.h_var.set(str(int(target_h)))
            except Exception:
                pass

    def _on_ok(self) -> None:
        if not self.image_path or not os.path.isfile(self.image_path):
            messagebox.showwarning("Warning", "Please select a valid image file first.", parent=self)
            return
        if str(self.image_path).startswith(("\\\\", "//")):
            messagebox.showwarning("Security Warning", "Remote network (UNC) paths are blocked for security.", parent=self)
            return
        try:
            x = float(self.x_var.get())
            y = float(self.y_var.get())
            w = float(self.w_var.get())
            h = float(self.h_var.get())
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(w) and math.isfinite(h)):
                raise ValueError("Dimensions must be finite numbers")
            if w <= 0 or h <= 0:
                raise ValueError("Width and height must be positive numbers")
        except ValueError as err:
            messagebox.showerror("Error", f"Invalid numeric dimensions:\n{err}", parent=self)
            return
        self.result = {
            "image_path": self.image_path,
            "rect": (x, y, x + w, y + h),
        }
        self.destroy()


class ManageImagesDialog(tk.Toplevel):
    def __init__(self, parent, images: list[dict], page_num: int) -> None:
        super().__init__(parent)
        self.title(f"Images on Page {page_num + 1}")
        self.transient(parent)
        self.geometry("540x360")
        self.result: dict | None = None

        frame = ttk.Frame(self, padding=(18, 14))
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"Found {len(images)} image(s) on Page {page_num + 1}. Select an image to replace or delete:",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 8))

        columns = ("Index", "Width", "Height", "X", "Y")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=90, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(0, 12))

        self.images_map = {}
        for item in images:
            idx = item["index"]
            r = item["rect"]
            w = int(r[2] - r[0])
            h = int(r[3] - r[1])
            x = int(r[0])
            y = int(r[1])
            item_id = self.tree.insert("", "end", values=(idx, w, h, x, y))
            self.images_map[item_id] = item

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Delete Image", command=self._delete_image).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Replace Image…", style="Accent.TButton", command=self._replace_image).pack(side="right")

        self.grab_set()
        self.wait_window(self)

    def _get_selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Image", "Please select an image row from the list.", parent=self)
            return None
        return self.images_map.get(sel[0])

    def _delete_image(self) -> None:
        item = self._get_selected()
        if not item:
            return
        if messagebox.askyesno("Confirm Delete", f"Delete Image #{item['index']} from the page?", parent=self):
            self.result = {"action": "delete", "rect": item["rect"]}
            self.destroy()

    def _replace_image(self) -> None:
        item = self._get_selected()
        if not item:
            return
        path = filedialog.askopenfilename(
            parent=self,
            title=f"Replace Image #{item['index']}",
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.ico"), ("All files", "*.*")],
        )
        if path:
            if str(path).startswith(("\\\\", "//")):
                messagebox.showwarning("Security Warning", "Remote network (UNC) paths are blocked for security.", parent=self)
                return
            self.result = {"action": "replace", "rect": item["rect"], "new_path": path}
            self.destroy()


class TableEditorDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        initial_data: list[list[str]] | None = None,
        title: str = "Table Editor",
        initial_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.geometry("680x480")
        self.result: dict | None = None
        self.rect = initial_rect or (48.0, 100.0, 564.0, 260.0)

        data = initial_data or [
            ["Column 1", "Column 2", "Column 3"],
            ["Item A", "Active", "$100"],
            ["Item B", "Pending", "$250"],
            ["Item C", "Complete", "$500"],
        ]
        self.rows_data = [list(map(str, row)) for row in data]

        frame = ttk.Frame(self, padding=(16, 12))
        frame.pack(fill="both", expand=True)

        top_info = ttk.Frame(frame)
        top_info.pack(fill="x", pady=(0, 8))
        ttk.Label(top_info, text="Edit table cells directly below:", font=("Segoe UI", 9, "bold")).pack(side="left")

        # Table dimensions controls
        dim_frame = ttk.Frame(frame)
        dim_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(dim_frame, text="Width (pt):").pack(side="left", padx=(0, 4))
        self.w_var = tk.StringVar(value=str(int(self.rect[2] - self.rect[0])))
        ttk.Entry(dim_frame, textvariable=self.w_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(dim_frame, text="Height (pt):").pack(side="left", padx=(0, 4))
        self.h_var = tk.StringVar(value=str(int(self.rect[3] - self.rect[1])))
        ttk.Entry(dim_frame, textvariable=self.h_var, width=6).pack(side="left", padx=(0, 10))

        self.header_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dim_frame, text="First row is header", variable=self.header_var).pack(side="left", padx=(0, 10))

        # Grid view container
        self.grid_container = ttk.Frame(frame)
        self.grid_container.pack(fill="both", expand=True, pady=(0, 10))
        self._entries: list[list[tk.Entry]] = []
        self._rebuild_grid()

        # Row / Column modification buttons
        mod_bar = ttk.Frame(frame)
        mod_bar.pack(fill="x", pady=(0, 12))
        ttk.Button(mod_bar, text="+ Add Row", command=self._add_row).pack(side="left", padx=(0, 6))
        ttk.Button(mod_bar, text="- Remove Row", command=self._remove_row).pack(side="left", padx=(0, 12))
        ttk.Button(mod_bar, text="+ Add Column", command=self._add_column).pack(side="left", padx=(0, 6))
        ttk.Button(mod_bar, text="- Remove Column", command=self._remove_column).pack(side="left")

        # Apply / Cancel buttons
        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill="x", side="bottom")
        ttk.Button(btn_bar, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_bar, text="Apply Table", style="Accent.TButton", command=self._on_ok).pack(side="right")

        self.grab_set()
        self.wait_window(self)

    def _rebuild_grid(self) -> None:
        for child in self.grid_container.winfo_children():
            child.destroy()
        self._entries = []

        # Create canvas with scrollbars for large tables
        canvas = tk.Canvas(self.grid_container, borderwidth=0, highlightthickness=0)
        v_scroll = ttk.Scrollbar(self.grid_container, orient="vertical", command=canvas.yview)
        h_scroll = ttk.Scrollbar(self.grid_container, orient="horizontal", command=canvas.xview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        canvas.pack(side="top", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")

        for r_idx, row in enumerate(self.rows_data):
            row_entries = []
            for c_idx, val in enumerate(row):
                bg = "#eef3fa" if (r_idx == 0 and self.header_var.get()) else "#ffffff"
                e = tk.Entry(
                    scrollable_frame,
                    width=18,
                    bg=bg,
                    font=("Segoe UI", 9, "bold" if (r_idx == 0 and self.header_var.get()) else "normal"),
                )
                e.grid(row=r_idx, column=c_idx, padx=2, pady=2, sticky="nsew")
                e.insert(0, str(val))
                row_entries.append(e)
            self._entries.append(row_entries)

    def _sync_data(self) -> None:
        new_data = []
        for row in self._entries:
            new_data.append([e.get() for e in row])
        self.rows_data = new_data

    def _add_row(self) -> None:
        self._sync_data()
        cols = len(self.rows_data[0]) if self.rows_data else 3
        self.rows_data.append([""] * cols)
        self._rebuild_grid()

    def _remove_row(self) -> None:
        self._sync_data()
        if len(self.rows_data) > 1:
            self.rows_data.pop()
            self._rebuild_grid()

    def _add_column(self) -> None:
        self._sync_data()
        for row in self.rows_data:
            row.append("")
        self._rebuild_grid()

    def _remove_column(self) -> None:
        self._sync_data()
        if self.rows_data and len(self.rows_data[0]) > 1:
            for row in self.rows_data:
                row.pop()
            self._rebuild_grid()

    def _on_ok(self) -> None:
        self._sync_data()
        try:
            w = float(self.w_var.get())
            h = float(self.h_var.get())
            if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
                raise ValueError
        except ValueError:
            w = 516.0
            h = 160.0

        x0, y0 = self.rect[0], self.rect[1]
        self.result = {
            "data": self.rows_data,
            "has_header": self.header_var.get(),
            "rect": (x0, y0, x0 + w, y0 + h),
        }
        self.destroy()

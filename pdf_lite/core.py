"""Core PDF handling for PDF Lite.

The implementation deliberately depends only on PyMuPDF (fitz), Pillow and the
Python standard library.  The GUI is plain Tkinter, so the whole reader stays
small and starts in well under a second on a normal Windows machine.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable

import fitz
from PIL import Image

PDF_POINTS_PER_INCH = 72.0
SCREEN_DPI = 96.0
BASE_SCALE = SCREEN_DPI / PDF_POINTS_PER_INCH  # 100% zoom = 96 px per PDF inch

ROTATE_CW = {0: 0, 90: -90, 180: 180, 270: 90}


class PasswordRequired(Exception):
    """The document is encrypted and needs a password."""


class WrongPassword(Exception):
    """The supplied password did not open the document."""


@dataclass
class SearchProgress:
    scanned: int
    total: int
    hits: int


@dataclass
class DocumentInfo:
    path: str
    title: str
    author: str
    subject: str
    keywords: str
    creator: str
    producer: str
    created: str
    modified: str
    format: str
    encryption: str
    page_count: int
    file_size: int
    outline: list[tuple[int, str, int]] = field(default_factory=list)


class PdfDocument:
    """A small wrapper around a PyMuPDF document."""

    def __init__(self, path: str, password: str | None = None) -> None:
        if str(path).startswith(("\\\\", "//")):
            raise ValueError("Remote network (UNC) paths are blocked for security")
        self.path = os.path.abspath(path)
        self.password = password
        self._doc = fitz.open(self.path)
        if self._doc.needs_pass:
            if password is None:
                self._doc.close()
                self._doc = None
                raise PasswordRequired
            if not self._doc.authenticate(password):
                self._doc.close()
                self._doc = None
                raise WrongPassword
        self._words: dict[int, list[tuple]] = {}
        self._text: dict[int, str] = {}
        self._links: dict[int, list[dict]] = {}

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "_doc", None) is not None:
            self._doc.close()
            self._doc = None

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc is not None else 0

    def page(self, number: int):
        return self._doc[number]

    def page_size(self, number: int, view_rotation: int = 0) -> tuple[float, float]:
        """Width and height of a page in PDF points, including built-in rotation."""
        rect = self._doc[number].rect
        width, height = rect.width, rect.height
        if view_rotation in (90, 270):
            width, height = height, width
        return width, height

    def is_slides(self) -> bool:
        """Return True if document pages are predominantly in landscape/presentation aspect ratio."""
        if not self.page_count:
            return False
        sample = min(5, self.page_count)
        landscape = sum(1 for i in range(sample) if self.page_size(i)[0] > self.page_size(i)[1] * 1.1)
        return landscape >= (sample / 2.0)

    def outline(self) -> list[tuple[int, str, int]]:
        """Return (level, title, 1-based page) entries."""
        try:
            toc = self._doc.get_toc(simple=True)
        except Exception:
            return []
        clean: list[tuple[int, str, int]] = []
        for level, title, page in toc:
            try:
                level_i = max(1, int(level))
                page_i = int(page)
            except (TypeError, ValueError):
                continue
            if page_i > 0:
                clean.append((level_i, str(title).strip() or "Untitled", page_i))
        return clean

    def info(self) -> DocumentInfo:
        meta = self._doc.metadata or {}
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return DocumentInfo(
            path=self.path,
            title=(meta.get("title") or os.path.basename(self.path)).strip(),
            author=(meta.get("author") or "").strip(),
            subject=(meta.get("subject") or "").strip(),
            keywords=(meta.get("keywords") or "").strip(),
            creator=(meta.get("creator") or "").strip(),
            producer=(meta.get("producer") or "").strip(),
            created=(meta.get("creationDate") or "").strip(),
            modified=(meta.get("modDate") or "").strip(),
            format=(meta.get("format") or "").strip(),
            encryption=(meta.get("encryption") or "").strip(),
            page_count=self.page_count,
            file_size=size,
            outline=self.outline(),
        )

    def render_page(self, number: int, scale: float, view_rotation: int = 0) -> Image.Image:
        """Render one page to an RGB Pillow image.

        scale is screen pixels per PDF point (1.3333 means 100% zoom).
        """
        page = self._doc[number]
        rect = page.rect
        # Bound maximum render dimensions to protect against memory exhaustion / decompression bombs
        max_px = 10_000.0
        max_total_px = 64_000_000.0
        target_w = max(1.0, rect.width) * scale
        target_h = max(1.0, rect.height) * scale
        if target_w > max_px or target_h > max_px or (target_w * target_h > max_total_px):
            scale = min(scale, (max_px / target_w) * scale, (max_px / target_h) * scale)

        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        angle = ROTATE_CW.get(view_rotation, 0)
        if angle:
            image = image.rotate(angle, expand=True)
        return image

    def add_ink_signature(self, number: int, strokes: list[list[tuple[float, float]]]) -> None:
        """Add a hand-drawn ink annotation to a page.

        strokes is a list of polylines in PDF page coordinates.  The returned
        annotation is visible and remains editable in PDF viewers.
        """
        if not strokes:
            raise ValueError("The signature has no strokes")
        for poly in strokes:
            for pt in poly:
                if not (math.isfinite(pt[0]) and math.isfinite(pt[1])):
                    raise ValueError("Stroke coordinates must be finite numbers")
        annot = self._doc[number].add_ink_annot(strokes)
        annot.set_border(width=1.8)
        annot.set_colors(stroke=(0.08, 0.18, 0.32))
        annot.update()

    def invalidate_page_cache(self, number: int) -> None:
        """Clear cached words, extracted text, and links for a modified page."""
        self._words.pop(number, None)
        self._text.pop(number, None)
        self._links.pop(number, None)

    def save(self, path: str | None = None) -> str:
        """Save the modified PDF in-place atomically, or to a new destination."""
        if self._doc is None:
            raise RuntimeError("No document is open")
        target = os.path.abspath(path) if path else self.path
        if str(target).startswith(("\\\\", "//")):
            raise ValueError("Saving to remote network (UNC) paths is blocked for security")
        if os.path.normcase(target) == os.path.normcase(self.path):
            # Atomic in-place save using a temporary file
            orig_path = self.path
            temp_dir = os.path.dirname(orig_path)
            try:
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="pdflite_save_", suffix=".pdf", dir=temp_dir or None)
            except OSError:
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="pdflite_save_", suffix=".pdf")
            os.close(tmp_fd)
            try:
                self._doc.save(tmp_path, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                self._doc.close()
                os.replace(tmp_path, orig_path)
                self._doc = fitz.open(orig_path)
                if self.password:
                    self._doc.authenticate(self.password)
            except Exception:
                if os.path.isfile(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                if getattr(self, "_doc", None) is None and os.path.isfile(orig_path):
                    try:
                        self._doc = fitz.open(orig_path)
                    except Exception:
                        pass
                raise
            self._words.clear()
            self._text.clear()
            self._links.clear()
            return orig_path
        else:
            self._doc.save(target, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            return target

    def save_copy(self, path: str) -> None:
        """Save the in-memory document (including new annotations) as a copy."""
        if str(path).startswith(("\\\\", "//")):
            raise ValueError("Saving to remote network (UNC) paths is blocked for security")
        target = os.path.abspath(path)
        if os.path.normcase(target) == os.path.normcase(self.path):
            raise ValueError("Refusing to overwrite the active PDF in place; use a new file path")
        self.save(path)

    def words(self, number: int) -> list[tuple]:
        """Cached word boxes for text selection.

        Each tuple is (x0, y0, x1, y1, word, block, line, word_number) in page
        coordinates.  PyMuPDF already accounts for the page's built-in rotation.
        """
        cached = self._words.get(number)
        if cached is not None:
            return cached
        words = self._doc[number].get_text("words", sort=True)
        self._words[number] = words
        return words

    def text(self, number: int) -> str:
        cached = self._text.get(number)
        if cached is None:
            cached = self._doc[number].get_text("text", sort=True)
            self._text[number] = cached
        return cached

    def links(self, number: int) -> list[dict]:
        cached = self._links.get(number)
        if cached is None:
            try:
                cached = self._doc[number].get_links()
            except Exception:
                cached = []
            self._links[number] = cached
        return cached


def search_document(
    path: str,
    query: str,
    password: str | None,
    cancel: threading.Event,
    progress: Callable[[SearchProgress, dict[int, list[tuple[float, float, float, float]]], int], None] | None = None,
    max_hits: int = 20_000,
) -> tuple[dict[int, list[tuple[float, float, float, float]]], int]:
    """Search a complete PDF in a worker thread.

    Returns a mapping of zero-based page number to list of hit rectangles and the
    total hit count.  The query is case-insensitive, matching Acrobat Reader.
    """
    doc = None
    if str(path).startswith(("\\\\", "//")):
        return {}, 0
    try:
        doc = fitz.open(path)
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                return {}, 0
        results: dict[int, list[tuple[float, float, float, float]]] = {}
        total = 0
        for page_no in range(doc.page_count):
            if cancel.is_set():
                break
            try:
                rects = doc[page_no].search_for(query)
            except Exception:
                rects = []
            if rects:
                thin = [(r.x0, r.y0, r.x1, r.y1) for r in rects]
                results[page_no] = thin
                total += len(thin)
                if total >= max_hits:
                    break
            if progress is not None:
                progress(SearchProgress(page_no + 1, doc.page_count, total), results, total)
        return results, total
    finally:
        if doc is not None:
            doc.close()


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def build_sample_pdf(path: str) -> None:
    """Create a small demo PDF used by --selftest and --make-sample."""
    doc = fitz.open()
    letter = (612.0, 792.0)
    accent = (0.72, 0.08, 0.06)
    ink = (0.15, 0.17, 0.20)
    muted = (0.42, 0.45, 0.50)
    light = (0.93, 0.94, 0.96)

    def add_page(title: str, body: str, page_number: int, total: int) -> None:
        page = doc.new_page(width=letter[0], height=letter[1])
        page.draw_rect(fitz.Rect(0, 0, 612, 56), color=None, fill=light)
        page.insert_text((48, 35), "PDF LITE  /  OWNER'S MANUAL", fontsize=9, fontname="hebo", color=accent)
        page.insert_text((564, 35), f"{page_number} / {total}", fontsize=9, fontname="helv", color=muted)
        page.insert_text((48, 105), title, fontsize=23, fontname="hebo", color=ink)
        page.draw_line((48, 125), (564, 125), color=(0.82, 0.84, 0.88), width=0.8)
        page.insert_textbox(
            fitz.Rect(48, 145, 564, 725),
            body,
            fontsize=11.5,
            fontname="helv",
            color=ink,
            lineheight=1.35,
        )
        page.insert_text(
            (48, 760),
            "Ctrl+O open   |   Ctrl+F find   |   Ctrl+C copy   |   F11 full screen",
            fontsize=8.5,
            fontname="helv",
            color=muted,
        )

    total = 8
    add_page(
        "PDF Lite",
        "A lightweight, Acrobat-style PDF reader for Windows.\n\n"
        "This built-in sample demonstrates continuous scrolling, text selection, "
        "search, bookmarks, thumbnails, rotation and full-screen reading.\n\n"
        "Try Ctrl+F and search for the word 'lightweight'. Use the Bookmarks tab "
        "to jump between sections. Drag across text to select it, then press "
        "Ctrl+C.\n\n"
        "Keyboard essentials\n"
        "  Ctrl+O      Open a PDF\n"
        "  Ctrl+F      Find text\n"
        "  Ctrl+G      Go to a page\n"
        "  Ctrl++/-    Zoom in or out\n"
        "  Ctrl+1      Actual size\n"
        "  F11         Full screen\n"
        "  F4          Show or hide the sidebar",
        1,
        total,
    )
    add_page(
        "1.  Reading and navigation",
        "PDF Lite uses a continuous page view, just like Acrobat Reader. Scroll "
        "with the mouse wheel, Page Up/Down, or the scrollbar. The page box in "
        "the toolbar accepts a page number.\n\n"
        "The back/forward buttons remember page jumps during the current "
        "session. Acrobat's page controls remain familiar: First, Previous, "
        "Next and Last.\n\n"
        "View modes\n"
        "  Fit Width     Fill the window width with the current page.\n"
        "  Fit Page      Show the entire current page.\n"
        "  Actual Size   Return to 100% zoom.\n\n"
        "When you resize the window while Fit Width or Fit Page is active, the "
        "page is re-fitted automatically.",
        2,
        total,
    )
    add_page(
        "2.  Selecting and copying text",
        "PDF Lite has a text tool that works on normal, selectable PDFs. Press "
        "the left mouse button inside a page and drag across the text. Selected "
        "words are highlighted in blue. Press Ctrl+C to copy the selection, or "
        "right-click for a context menu.\n\n"
        "Select All on Page copies every word on the visible page. Copy Page "
        "Text does the same through the Edit menu.\n\n"
        "Scanned PDFs may contain only images. Those documents need OCR before "
        "their text can be selected; PDF Lite intentionally stays lightweight "
        "and does not bundle an OCR engine.\n\n"
        "Selection clears when you zoom or rotate, which prevents highlight "
        "positions from drifting.",
        3,
        total,
    )
    add_page(
        "3.  Finding text",
        "Press Ctrl+F or click Find in the toolbar. Type a word or phrase and "
        "press Enter. PDF Lite searches the whole document in a background "
        "thread and highlights every match.\n\n"
        "Use Next and Previous to step through matches. The current match is "
        "shown in red; all other matches are yellow. Matching ignores case, "
        "like Acrobat Reader.\n\n"
        "Search example: the word 'lightweight' appears in this document more "
        "than once. Search for 'rotation' to test navigation across pages.",
        4,
        total,
    )
    add_page(
        "4.  Bookmarks and thumbnails",
        "F4 toggles the left sidebar. The Pages tab shows live page thumbnails "
        "and the Bookmarks tab shows the PDF outline. Click either one to jump "
        "to that page.\n\n"
        "This sample PDF contains an embedded outline, so the Bookmarks tab is "
        "populated. Many PDFs do not include bookmarks; in that case PDF Lite "
        "shows a short explanation and the page field and navigation buttons "
        "remain available.",
        5,
        total,
    )
    add_page(
        "5.  Appearance and display",
        "Use View > Dark Mode to switch the application chrome between light "
        "and dark. PDF pages themselves keep their original white background, "
        "which matches the readable behavior of Acrobat.\n\n"
        "Page rotation is available under View. Rotate the whole document "
        "clockwise or counter-clockwise, and reset it at any time.\n\n"
        "F11 enters full-screen mode. Escape leaves it.",
        6,
        total,
    )
    add_page(
        "6.  A page for searching",
        "lightweight reader\nlightweight rendering\nlightweight startup\n\n"
        "Search for 'lightweight' on this page to see several hits grouped "
        "close together. Use Next to move through each rectangle.\n\n"
        "The status bar reports page number, zoom, page count and file size. "
        "Long documents scroll continuously and render only the pages near the "
        "viewport, keeping memory use modest.",
        7,
        total,
    )
    add_page(
        "7.  File properties and help",
        "File > Properties shows title, author, subject, keywords, producer, "
        "page count and file size.\n\n"
        "This program is a source-based alternative for everyday reading. It "
        "does not edit, sign, redact or fill PDF forms. For those workflows, "
        "keep Adobe Acrobat or another dedicated editor.\n\n"
        "Close the document with Ctrl+W. Open another PDF with Ctrl+O.\n\n"
        "A note about PDF support\n"
        "PDF is a complex format. This reader covers common reading workflows "
        "rather than every optional PDF feature. Encrypted files are supported "
        "when you know the password.",
        8,
        total,
    )

    doc.set_metadata(
        {
            "title": "PDF Lite Owner's Manual",
            "author": "Ken Huang",
            "subject": "A lightweight Acrobat-style PDF reader for Windows",
            "keywords": "PDF reader, Acrobat, Windows, lightweight, search",
            "creator": "PDF Lite",
            "producer": "PyMuPDF",
        }
    )
    doc.set_toc(
        [
            [1, "Cover", 1],
            [1, "1.  Reading and navigation", 2],
            [1, "2.  Selecting and copying text", 3],
            [1, "3.  Finding text", 4],
            [1, "4.  Bookmarks and thumbnails", 5],
            [1, "5.  Appearance and display", 6],
            [1, "6.  A page for searching", 7],
            [1, "7.  File properties and help", 8],
        ]
    )
    doc.save(path, garbage=3, deflate=True)
    doc.close()

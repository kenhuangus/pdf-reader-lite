#!/usr/bin/env python3
"""PDF Lite entry point and command-line diagnostics.

Run without arguments for the GUI.  A PDF path can be passed directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


APP_NAME = "PDF Lite"
APP_VERSION = "1.0.0"


def _enable_windows_quality() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ken.PDFLite.1")
        except Exception:
            pass
    except Exception:
        pass


def _sample_path() -> str:
    with tempfile.NamedTemporaryFile(prefix="pdflite_sample_", suffix=".pdf", delete=False) as f:
        path = f.name
    from pdf_lite.core import build_sample_pdf
    build_sample_pdf(path)
    return path


def run_selftest(path: str | None) -> int:
    from pdf_lite.core import PdfDocument, build_sample_pdf, search_document
    import threading

    sample = path
    generated_sample = False
    if not sample or not os.path.isfile(sample):
        sample = _sample_path()
        generated_sample = True
    print(f"[selftest] sample: {sample}")
    checks: list[tuple[str, bool]] = []
    doc = PdfDocument(sample)
    try:
        checks.append(("page count = 8", doc.page_count == 8))
        checks.append(("first page text", "PDF Lite" in doc.text(0)))
        checks.append(("word extraction", len(doc.words(2)) > 20))
        outline = doc.outline()
        checks.append(("bookmark outline", len(outline) == 8))
        info = doc.info()
        checks.append(("metadata title", info.title == "PDF Lite Owner's Manual"))
        image = doc.render_page(0, 1.75)
        checks.append(("page render", image.width > 500 and image.height > 700))
        results, total = search_document(sample, "lightweight", None,
                                         threading.Event())
        checks.append(("full text search", total >= 3 and bool(results)))
        first_hit_rect = next(iter(results.values()))[0]
        checks.append(("search rectangles", len(first_hit_rect) == 4))
    finally:
        doc.close()

    with tempfile.NamedTemporaryFile(prefix="pdflite_signed_", suffix=".pdf", delete=False) as f1:
        signed_path = f1.name
    with tempfile.NamedTemporaryFile(prefix="pdflite_word_", suffix=".docx", delete=False) as f2:
        word_path = f2.name
    try:
        signed = PdfDocument(sample)
        signed.add_ink_signature(0, [[(100.0, 700.0), (130.0, 680.0), (160.0, 705.0)]])
        signed.save_copy(signed_path)
        signed.close()
        import fitz
        reopened = fitz.open(signed_path)
        checks.append(("hand-drawn signature saved", len(list(reopened[0].annots())) >= 1))
        reopened.close()
        from pdf_lite.convert import convert_pdf_to_word
        from docx import Document
        method = convert_pdf_to_word(sample, word_path)
        word = Document(word_path)
        word_text = "\n".join(p.text for p in word.paragraphs)
        checks.append(("Word conversion", "lightweight" in word_text and bool(method)))
    except Exception as exc:
        print(f"[selftest] feature error: {exc}")
        checks.append(("signature/Word features", False))
    finally:
        for item in (signed_path, word_path):
            try:
                os.remove(item)
            except OSError:
                pass
        if generated_sample and os.path.isfile(sample):
            try:
                os.remove(sample)
            except OSError:
                pass

    ok = all(value for _, value in checks)
    for label, value in checks:
        print(f"  {'PASS' if value else 'FAIL'}  {label}")
    print(f"[selftest] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _capture_window(app, out_path: str) -> str:
    from PIL import ImageGrab

    app.lift()
    try:
        app.focus_force()
    except Exception:
        pass
    app.update_idletasks()
    app.update()
    x = app.winfo_rootx()
    y = app.winfo_rooty()
    width = app.winfo_width()
    height = app.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def run_smoke(path: str | None, out_path: str, close_when_done: bool = True) -> int:
    from pdf_lite.ui import PDFLiteApp

    sample_path = path
    generated = False
    if not sample_path:
        sample_path = _sample_path()
        generated = True

    app = PDFLiteApp(sample_path)
    state = {"ok": False, "error": None}

    def begin() -> None:
        try:
            if app.doc is None:
                state["error"] = "document did not open"
                finish()
                return
            app.view.set_zoom(1.25)
            app.show_find()
            app.find_var.set("lightweight")
            app._find_from_entry()
            app.after(180, wait_for_search)
        except Exception as exc:
            state["error"] = str(exc)
            finish()

    def wait_for_search(attempts: int = 0) -> None:
        if app._search_running and attempts < 80:
            app.after(100, lambda: wait_for_search(attempts + 1))
            return
        app.after(300, capture)

    def capture() -> None:
        try:
            actual_out = _capture_window(app, out_path)
            state["ok"] = True
            state["screenshot"] = actual_out
            state["pages"] = app.doc.page_count if app.doc else 0
            state["search_hits"] = len(app._search_matches)
            state["zoom"] = round(app.view.zoom, 3)
            state["search_running"] = app._search_running
        except Exception as exc:
            state["error"] = str(exc)
        finish()

    def finish() -> None:
        try:
            if app.doc is not None:
                app.doc.close()
                app.doc = None
            if close_when_done:
                app.destroy()
        except Exception:
            pass

    app.after(450, begin)
    try:
        app.mainloop()
    except Exception as exc:
        state["error"] = str(exc)
    print(json.dumps(state, indent=2))
    if generated and os.path.isfile(sample_path):
        try:
            os.remove(sample_path)
        except OSError:
            pass
    return 0 if state["ok"] else 1


def make_icon(out_path: str) -> None:
    from PIL import Image, ImageDraw

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for size in sizes:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = max(1, size // 12)
        draw.rounded_rectangle((margin, margin // 2, size - margin, size - margin // 2),
                               radius=max(2, size // 8), fill=(166, 30, 30, 255))
        # White page + red folded corner.
        page = (size * 0.22, size * 0.14, size * 0.78, size * 0.84)
        draw.rectangle(page, fill=(255, 255, 255, 255))
        fold = size * 0.18
        draw.polygon([
            (size * 0.78 - fold, size * 0.14),
            (size * 0.78, size * 0.14 + fold),
            (size * 0.78 - fold, size * 0.14 + fold),
        ], fill=(220, 224, 230, 255))
        for i in range(3):
            y = size * (0.35 + i * 0.12)
            draw.rectangle((size * 0.31, y, size * 0.66, y + max(1, size // 32)),
                           fill=(190, 195, 205, 255))
        images.append(image)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    first = images[-1]
    first.save(out_path, format="ICO", sizes=[(s, s) for s in sizes])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PDF Lite — a lightweight Acrobat-style PDF reader for Windows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("file", nargs="?", help="PDF file to open")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--selftest", action="store_true", help="run headless self-test")
    parser.add_argument("--make-sample", metavar="PDF",
                        help="write the built-in demo PDF to a path")
    parser.add_argument("--make-icon", metavar="ICO",
                        help="generate assets/icon.ico")
    parser.add_argument("--smoke", metavar="PNG",
                        help="launch the GUI, open/search, capture PNG and exit")
    parser.add_argument("--no-open", action="store_true",
                        help="start with an empty window (ignore file argument)")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args.make_icon:
        make_icon(args.make_icon)
        print(f"Wrote {args.make_icon}")
        return 0
    if args.make_sample:
        from pdf_lite.core import build_sample_pdf
        build_sample_pdf(args.make_sample)
        print(f"Wrote sample PDF: {args.make_sample}")
        return 0
    if args.selftest:
        return run_selftest(args.file)
    if args.smoke:
        _enable_windows_quality()
        return run_smoke(args.file, args.smoke)

    _enable_windows_quality()
    path = None if args.no_open else args.file
    if path:
        if str(path).startswith(("\\\\", "//")):
            print("Remote network (UNC) paths are blocked for security.", file=sys.stderr)
            return 2
        if not os.path.isfile(path):
            print(f"File not found: {path}", file=sys.stderr)
            return 2
    from pdf_lite.ui import PDFLiteApp
    app = PDFLiteApp(path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

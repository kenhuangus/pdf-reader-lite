"""Interaction and persistence regressions for inline editing and draft objects."""

import os
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from PIL import Image

from pdf_lite.core import PdfDocument, build_sample_pdf
from pdf_lite.editor import apply_inline_text
from pdf_lite.ui import PDFLiteApp
from pdf_lite.view import MODE_EDIT, MODE_READ


class TestInlineEditing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "working.pdf")
        build_sample_pdf(self.path)
        self.settings = patch.object(PDFLiteApp, "_save_settings").start()
        self.recent = patch.object(PDFLiteApp, "_add_recent").start()
        self.app = PDFLiteApp()
        self.app.update()
        self.app.open_path(self.path)
        self.app.update()
        self.view = self.app.view
        self.app.set_mode(MODE_EDIT)

    def tearDown(self):
        self.view.cancel_inline_text()
        self.app.doc.close()
        for callback in self.app.tk.call("after", "info"):
            self.app.tk.call("after", "cancel", callback)
        self.app.destroy()
        patch.stopall()
        self.temp.cleanup()

    def event_at(self, page, point):
        x, y, _, _ = self.view._map_rect(page, (*point, *point))
        return SimpleNamespace(x=x-self.view.canvas.canvasx(0),
                               y=y-self.view.canvas.canvasy(0), state=0)

    def test_double_click_types_inline_and_save_includes_open_editor(self):
        event = self.event_at(0, (100, 650))
        with patch("pdf_lite.ui.messagebox.showerror") as error:
            self.view._on_double_click(event)
            self.assertIsInstance(self.view._inline["widget"], tk.Text)
            self.assertFalse(any(isinstance(w, tk.Toplevel) for w in self.app.winfo_children()))
            self.view._inline["widget"].insert("1.0", "INLINE SAVE MARKER")
            self.assertTrue(self.app.save_document())
            error.assert_not_called()
        self.assertIsNone(self.view._inline)
        with fitz.open(self.path) as saved:
            self.assertIn("INLINE SAVE MARKER", saved[0].get_text())

    def test_single_click_and_drag_pan_in_edit_mode_without_editor(self):
        self.view.canvas.scan_mark = unittest.mock.MagicMock()
        self.view.canvas.scan_dragto = unittest.mock.MagicMock()
        event = self.event_at(0, (100, 300))
        self.view._on_button_press(event)
        self.view._on_drag(SimpleNamespace(x=event.x, y=event.y-70))
        self.view._on_button_release(SimpleNamespace(x=event.x, y=event.y-70))
        self.assertIsNone(self.view._inline)
        self.view.canvas.scan_dragto.assert_called_once()
        self.assertFalse(self.app.modified)

    def test_cancel_and_read_mode_do_not_insert_text(self):
        self.view.begin_inline_text(0, (90, 650))
        self.view._inline["widget"].insert("1.0", "CANCELLED MARKER")
        self.assertTrue(self.app.has_unsaved_changes)
        self.view._cancel_inline_key()
        self.assertFalse(self.app.has_unsaved_changes)
        self.assertNotIn("CANCELLED MARKER", self.app.doc.text(0))
        self.app.set_mode(MODE_READ)
        self.view._on_double_click(self.event_at(0, (100, 600)))
        self.assertIsNone(self.view._inline)

    def test_replacement_preserves_nearby_text_and_drawing(self):
        page = self.app.doc.page(0)
        page.insert_text((80, 650), "Original", fontsize=11)
        page.insert_text((80, 675), "NEIGHBOR", fontsize=11)
        page.draw_line((70, 655), (250, 655), color=(1, 0, 0))
        self.view.begin_inline_text(0, (95, 646))
        widget = self.view._inline["widget"]
        self.assertEqual(widget.get("1.0", "end-1c"), "Original")
        widget.delete("1.0", "end")
        widget.insert("1.0", "Replacement")
        self.assertTrue(self.view.commit_inline_text())
        text = self.app.doc.text(0)
        self.assertIn("Replacement", text)
        self.assertNotIn("Original", text)
        self.assertIn("NEIGHBOR", text)
        self.assertTrue(any(d["color"] == (1.0, 0.0, 0.0) for d in self.app.doc.page(0).get_drawings()))

    def test_overflow_does_not_delete_original_or_discard_editor(self):
        page = self.app.doc.page(0)
        page.insert_text((80, 650), "Original", fontsize=11)
        self.view.begin_inline_text(0, (95, 646))
        widget = self.view._inline["widget"]
        widget.delete("1.0", "end")
        widget.insert("1.0", "Too many lines\n" * 80)
        self.assertFalse(self.view.commit_inline_text())
        self.assertIn("Original", self.app.doc.page(0).get_text())
        self.assertIsNotNone(self.view._inline)
        self.assertFalse(self.app.save_document())

    def test_drafts_survive_read_mode_zoom_then_cross_page_move_and_save(self):
        self.view.begin_image_placement(Image.new("RGBA", (60, 40), "red"))
        self.view._on_button_press(self.event_at(0, (100, 600)))
        figure = self.view._pending_objects[0]
        self.view._on_button_press(self.event_at(0, (120, 620)))
        self.assertIsNotNone(self.view._object_drag)
        self.view._on_drag(self.event_at(0, (220, 620)))
        self.view._on_button_release(self.event_at(0, (220, 620)))
        self.assertAlmostEqual(figure["rect"][0], 200, delta=.5)
        self.view.begin_signature_placement([[(0, 0), (.5, .8), (1, .2)]], .4, 120)
        self.view._on_button_press(self.event_at(0, (100, 450)))
        signature = self.view._pending_objects[1]
        self.app.set_mode(MODE_READ)
        old_rect = figure["rect"]
        self.view.move_object(figure, 1, (150, 400))
        self.assertEqual(figure["rect"], old_rect)
        self.view.set_zoom(.75)
        self.assertGreater(len(self.view.canvas.find_withtag("draft")), 0)
        self.app.set_mode(MODE_EDIT)
        self.view.move_object(figure, 1, (150, 400))
        self.view.move_object(signature, 1, (100, 600))
        self.assertTrue(self.app.save_document())
        self.assertEqual(self.view._pending_objects, [])
        self.view.move_object(figure, 0, (20, 20))
        self.assertEqual(figure["page"], 1)
        with fitz.open(self.path) as saved:
            self.assertEqual(len(saved[0].get_image_info()), 0)
            self.assertEqual(len(saved[1].get_image_info()), 1)
            self.assertFalse(list(saved[1].annots() or []))
            self.assertTrue(saved[1].get_drawings())

    def test_failed_save_retains_movable_drafts_and_text_without_duplicates(self):
        self.view.begin_inline_text(0, (100, 650))
        self.view._inline["widget"].insert("1.0", "RETAINED TEXT")
        self.assertTrue(self.view.commit_inline_text())
        self.view.begin_image_placement(Image.new("RGB", (60, 40), "blue"))
        self.view._place_object(0, (100, 450))
        with patch("pdf_lite.core.os.replace", side_effect=PermissionError("locked")), patch("pdf_lite.ui.messagebox.showerror"):
            self.assertFalse(self.app.save_document())
        self.assertEqual(len(self.view._pending_objects), 1)
        self.assertIn("RETAINED TEXT", self.app.doc.text(0))
        self.assertEqual(len(self.app.doc.page(0).get_images()), 0)
        self.assertTrue(self.app.modified)
        self.assertTrue(self.app.save_document())
        with fitz.open(self.path) as saved:
            self.assertEqual(len(saved[0].get_images()), 1)
            self.assertIn("RETAINED TEXT", saved[0].get_text())

    def test_save_as_fixes_objects_and_preserves_original(self):
        self.view.begin_signature_placement([[(0, 0), (.5, 1), (1, .2)]], .4, 120)
        self.view._place_object(0, (100, 600))
        copy_path = os.path.join(self.temp.name, "copy.pdf")
        with patch("pdf_lite.ui.filedialog.asksaveasfilename", return_value=copy_path):
            self.assertTrue(self.app.save_as(quiet=True))
        self.assertFalse(self.view._pending_objects)
        self.assertEqual(self.app.path, copy_path)
        with fitz.open(copy_path) as copy, fitz.open(self.path) as source:
            self.assertGreater(len(copy[0].get_drawings()), len(source[0].get_drawings()))
            self.assertFalse(list(copy[0].annots() or []))

    def test_cancelled_save_as_keeps_objects_movable(self):
        self.view.begin_image_placement(Image.new("RGB", (60, 40), "green"))
        self.view._place_object(0, (100, 400))
        item = self.view._pending_objects[0]
        with patch("pdf_lite.ui.filedialog.asksaveasfilename", return_value=""):
            self.assertFalse(self.app.save_as())
        self.view.move_object(item, 1, (100, 300))
        self.assertEqual(item["page"], 1)
        self.assertTrue(self.app.modified)

    def test_failed_text_only_save_retains_unsaved_text(self):
        self.view.begin_inline_text(0, (100, 650))
        self.view._inline["widget"].insert("1.0", "RETRY TEXT ONLY")
        with patch("pdf_lite.core.os.replace", side_effect=PermissionError("locked")), patch("pdf_lite.ui.messagebox.showerror"):
            self.assertFalse(self.app.save_document())
        self.assertIn("RETRY TEXT ONLY", self.app.doc.text(0))
        self.assertTrue(self.app.save_document())

    def test_rotated_page_maps_clicks_and_saves_at_the_same_position(self):
        self.app.doc.page(0).set_rotation(90)
        self.view._relayout()
        point = (100, 300)
        event = self.event_at(0, point)
        page, mapped = self.view._event_page_point(event)
        self.assertEqual(page, 0)
        self.assertAlmostEqual(mapped[0], point[0], delta=.5)
        self.assertAlmostEqual(mapped[1], point[1], delta=.5)
        self.view._on_double_click(event)
        self.view._inline["widget"].insert("1.0", "ROTATED MARKER")
        self.assertTrue(self.app.save_document())
        with fitz.open(self.path) as saved:
            self.assertEqual(saved[0].rotation, 90)
            self.assertIn("ROTATED MARKER", saved[0].get_text())

    def test_arrows_navigate_document_but_keep_typing_caret_local(self):
        self.view.canvas.focus_force()
        self.app.update()
        self.app._key_right(None)
        self.assertGreater(self.view.canvas.canvasy(0), 300)
        self.view.begin_inline_text(1, (100, 200))
        self.view._inline["widget"].focus_force()
        self.app.update()
        before = self.view.canvas.canvasy(0)
        self.app._key_right(None)
        self.assertEqual(self.view.canvas.canvasy(0), before)


if __name__ == "__main__":
    unittest.main()

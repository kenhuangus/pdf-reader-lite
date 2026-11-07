"""Tests for Read Mode, Select Mode, touch/pan browsing, and whole document copying."""

import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import MagicMock

from pdf_lite.core import PdfDocument, build_sample_pdf
from pdf_lite.view import (
    ContinuousView,
    MODE_READ,
    MODE_SELECT,
    MODE_EDIT,
)


class TestModesAndSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.geometry("800x600")
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.sample_pdf = os.path.join(cls.temp_dir.name, "sample.pdf")
        build_sample_pdf(cls.sample_pdf)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        cls.root.destroy()

    def setUp(self):
        self.doc = PdfDocument(self.sample_pdf)
        self.mock_app = MagicMock()
        self.mock_app.palette = {
            "workspace": "#dfe1e5",
            "muted": "#666b75",
            "accent": "#b51c1c",
            "shadow": "#c6c9cf",
            "page_border": "#b9bdc5",
            "bg": "#f1f2f4",
        }
        self.view = ContinuousView(self.root, self.mock_app)
        self.view.pack(fill="both", expand=True)
        self.root.update()
        self.view.set_document(self.doc)
        self.root.update()

    def tearDown(self):
        self.view.destroy()
        self.doc.close()

    def test_mode_transitions(self):
        """Verify transitions between Read Mode, Select Mode, and Edit Mode."""
        # Initial default mode is Read Mode
        self.assertEqual(self.view.mode, MODE_READ)
        self.assertFalse(self.view.edit_mode)

        # Switch to Select Mode
        self.view.set_mode(MODE_SELECT)
        self.assertEqual(self.view.mode, MODE_SELECT)
        self.assertFalse(self.view.edit_mode)

        # Switch to Edit Mode
        self.view.set_mode(MODE_EDIT)
        self.assertEqual(self.view.mode, MODE_EDIT)
        self.assertTrue(self.view.edit_mode)

        # Back to Read Mode
        self.view.set_mode(MODE_READ)
        self.assertEqual(self.view.mode, MODE_READ)
        self.assertFalse(self.view.edit_mode)

        # Invalid mode raises ValueError
        with self.assertRaises(ValueError):
            self.view.set_mode("unknown_mode")

    def test_cursor_behavior_per_mode(self):
        """Verify canvas cursor configuration according to active mode."""
        self.view.set_mode(MODE_READ)
        self.assertEqual(self.view.canvas.cget("cursor"), "hand2")

        self.view.set_mode(MODE_SELECT)
        self.assertEqual(self.view.canvas.cget("cursor"), "xterm")

        self.view.set_mode(MODE_EDIT)
        self.assertEqual(self.view.canvas.cget("cursor"), "pencil")

    def test_read_mode_pan_does_not_select_text(self):
        """Verify that dragging in Read Mode performs canvas scan and does not select text."""
        self.view.set_mode(MODE_READ)
        self.view.canvas.scan_mark = MagicMock()
        self.view.canvas.scan_dragto = MagicMock()

        # Simulate button press
        press_event = MagicMock()
        press_event.x = 100
        press_event.y = 100
        press_event.state = 0
        self.view._on_button_press(press_event)
        self.view.canvas.scan_mark.assert_called_once_with(100, 100)

        # Simulate drag
        drag_event = MagicMock()
        drag_event.x = 120
        drag_event.y = 150
        self.view._on_drag(drag_event)
        self.view.canvas.scan_dragto.assert_called_once_with(120, 150, gain=1)

        # In Read Mode, no text selection is created
        self.assertEqual(self.view.selection_text, "")
        self.assertEqual(len(self.view.selection_rects), 0)

        # Simulate button release
        release_event = MagicMock()
        release_event.x = 120
        release_event.y = 150
        release_event.x_root = 200
        release_event.y_root = 250
        self.view._on_button_release(release_event)
        self.assertIsNone(self.view._pan_origin)

    def test_select_mode_drag_selects_text(self):
        """Verify that dragging across text in Select Mode selects words."""
        self.view.set_mode(MODE_SELECT)
        # Position over page 0 text (x=100, y=100 to x=400, y=300)
        press_event = MagicMock()
        press_event.x = 50
        press_event.y = 50
        press_event.state = 0
        self.view._on_button_press(press_event)

        drag_event = MagicMock()
        drag_event.x = 400
        drag_event.y = 250
        self.view._on_drag(drag_event)

        self.assertTrue(len(self.view.selection_text) > 0)
        self.assertTrue(len(self.view.selection_rects) > 0)

    def test_copy_document_text(self):
        """Verify copying text across all pages in the document."""
        success = self.view.copy_document_text()
        self.assertTrue(success)
        clipboard_content = self.root.clipboard_get()
        # Verify all 8 pages are represented
        self.assertIn("--- Page 1 ---", clipboard_content)
        self.assertIn("--- Page 8 ---", clipboard_content)
        self.assertIn("PDF Lite", clipboard_content)

    def test_select_all_document(self):
        """Verify selecting the entire document text."""
        self.view.select_all_document()
        self.assertTrue(len(self.view.selection_text) > 500)
        self.assertIn("--- Page 1 ---", self.view.selection_text)
        self.assertIn("--- Page 8 ---", self.view.selection_text)

    def test_copy_selection_fallback_to_page(self):
        """Verify that copy_selection falls back to copying current page if nothing is selected."""
        self.view.clear_selection()
        self.view.copy_page_text = MagicMock()
        self.view.copy_selection()
        self.view.copy_page_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()

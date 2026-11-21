"""Save-location and automatic-opening behavior for PDF-to-Word export."""

import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import fitz
from docx import Document

from pdf_lite.convert import convert_pdf_to_word
from pdf_lite.ui import PDFLiteApp


class TestWordExportFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.pdf"
        self.target = Path(self.temp.name) / "Chosen folder" / "My document.docx"
        self.target.parent.mkdir()
        self.app = SimpleNamespace(
            doc=SimpleNamespace(path=str(self.source)),
            _word_dialog_open=False,
            _choose_word_mode=MagicMock(return_value="reflow"),
            _start_word_conversion=MagicMock(),
            set_status=MagicMock(),
            after=MagicMock(),
        )

    def poll(self, *messages):
        pending = queue.Queue()
        for message in messages:
            pending.put(message)
        self.app._word_dialog_open = True
        dialog = MagicMock()
        PDFLiteApp._poll_word(self.app, pending, dialog, str(self.target), "reflow",
                             MagicMock(), MagicMock(), MagicMock())
        return dialog

    def test_save_picker_runs_first_and_selected_location_is_used(self):
        calls = []
        self.app._choose_word_mode.side_effect = lambda available: calls.append("mode") or "reflow"
        with patch("pdf_lite.ui.filedialog.asksaveasfilename",
                   side_effect=lambda **kwargs: calls.append("save") or str(self.target)) as picker, \
                patch("pdf_lite.ui.find_soffice", return_value=None):
            PDFLiteApp.export_word(self.app)
        self.assertEqual(calls, ["save", "mode"])
        self.assertEqual(picker.call_args.kwargs["defaultextension"], ".docx")
        self.assertTrue(picker.call_args.kwargs["confirmoverwrite"])
        self.app._start_word_conversion.assert_called_once_with(str(self.target), "reflow")

    def test_cancel_save_picker_does_not_start_conversion(self):
        with patch("pdf_lite.ui.filedialog.asksaveasfilename", return_value=""):
            PDFLiteApp.export_word(self.app)
        self.app._choose_word_mode.assert_not_called()
        self.app._start_word_conversion.assert_not_called()

    def test_cancel_mode_does_not_create_output(self):
        self.app._choose_word_mode.return_value = None
        with patch("pdf_lite.ui.filedialog.asksaveasfilename", return_value=str(self.target)), \
                patch("pdf_lite.ui.find_soffice", return_value=None):
            PDFLiteApp.export_word(self.app)
        self.app._start_word_conversion.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_network_destination_is_blocked_before_conversion(self):
        with patch("pdf_lite.ui.filedialog.asksaveasfilename", return_value=r"\\server\share\output.docx"), \
                patch("pdf_lite.ui.messagebox.showwarning") as warning:
            PDFLiteApp.export_word(self.app)
        warning.assert_called_once()
        self.app._choose_word_mode.assert_not_called()
        self.app._start_word_conversion.assert_not_called()

    def test_real_generated_document_opens_without_completion_popup(self):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "WORD EXPORT MARKER")
            pdf.save(self.source)
        method = convert_pdf_to_word(str(self.source), str(self.target), mode="reflow")
        text = "\n".join(p.text for p in Document(self.target).paragraphs)
        self.assertIn("WORD EXPORT MARKER", text)
        with patch("pdf_lite.ui.os.startfile") as launch, \
                patch("pdf_lite.ui.messagebox.showinfo") as info:
            dialog = self.poll(("done", method))
        dialog.grab_release.assert_called_once()
        dialog.destroy.assert_called_once()
        launch.assert_called_once_with(str(self.target))
        info.assert_not_called()
        self.assertFalse(self.app._word_dialog_open)
        self.app.after.assert_not_called()

    def test_open_failure_keeps_document_and_reports_its_path(self):
        Document().save(self.target)
        with patch("pdf_lite.ui.os.startfile", side_effect=OSError("No app associated")), \
                patch("pdf_lite.ui.messagebox.showwarning") as warning:
            self.poll(("done", "reflow"))
        self.assertTrue(self.target.is_file())
        self.assertIn(str(self.target), warning.call_args.args[1])
        self.assertFalse(self.app._word_dialog_open)

    def test_missing_output_is_not_opened(self):
        with patch("pdf_lite.ui.os.startfile") as launch, \
                patch("pdf_lite.ui.messagebox.showwarning") as warning:
            self.poll(("done", "reflow"))
        launch.assert_not_called()
        warning.assert_called_once()

    def test_conversion_failure_does_not_open_a_stale_document(self):
        Document().save(self.target)
        with patch("pdf_lite.ui.os.startfile") as launch, \
                patch("pdf_lite.ui.messagebox.showerror") as error:
            self.poll(("error", "Conversion failed"))
        launch.assert_not_called()
        error.assert_called_once()
        self.assertFalse(self.app._word_dialog_open)


if __name__ == "__main__":
    unittest.main()

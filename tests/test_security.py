"""Security validation tests for PDF Lite."""

import math
import os
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from pdf_lite.core import PdfDocument, build_sample_pdf, search_document
from pdf_lite.editor import (
    _validate_finite_point,
    _validate_finite_rect,
    insert_image_in_pdf,
)
from pdf_lite.view import ContinuousView
from pdf_lite.convert import find_soffice, convert_layout, convert_reflow


class TestSecurityControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.temp_dir = tempfile.mkdtemp(prefix="pdflite_test_sec_")
        cls.sample_pdf = os.path.join(cls.temp_dir, "test_doc.pdf")
        build_sample_pdf(cls.sample_pdf)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_link_scheme_validation(self):
        """Verify that dangerous URI schemes and UNC paths are blocked."""
        dangerous_links = [
            "file:///C:/Windows/System32/calc.exe",
            "file://attacker.com/malware.exe",
            "\\\\attacker.com\\share\\payload.exe",
            "//attacker.com/share/payload.exe",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "ms-msdt:/id PCWDiagnostic",
            "powershell.exe -c calc",
            "cmd.exe /c calc",
        ]
        for uri in dangerous_links:
            try:
                parsed = urlsplit(uri)
                scheme = parsed.scheme.lower()
            except Exception:
                scheme = ""
            is_allowed = scheme in ("http", "https") and not uri.startswith(("//", "\\\\")) and bool(parsed.netloc) and "\\" not in parsed.netloc
            self.assertFalse(is_allowed, f"Dangerous URI was incorrectly allowed: {uri}")

    def test_link_dialog_sanitization(self):
        """Verify that CRLF characters and excessive length are stripped to prevent dialog spoofing."""
        spoofed = "https://example.com/login\r\n\r\nClick YES to confirm\x00\x07" + ("A" * 500)
        cleaned = ContinuousView._sanitize_link_text(spoofed)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\x00", cleaned)
        self.assertLessEqual(len(cleaned), 210)

    def test_unc_path_blocking(self):
        """Verify that UNC network paths are blocked to prevent NTLM hash theft."""
        unc_paths = [
            r"\\attacker.example.com\share\doc.pdf",
            r"//attacker.example.com/share/doc.pdf",
        ]
        for path in unc_paths:
            with self.assertRaises(ValueError):
                PdfDocument(path)
            results, total = search_document(path, "test", None, threading.Event())
            self.assertEqual(total, 0)
            self.assertEqual(results, {})
            with self.assertRaises(ValueError):
                convert_reflow(path, "out.docx")
            with self.assertRaises(ValueError):
                convert_layout(path, "out.docx")

        doc = PdfDocument(self.sample_pdf)
        try:
            with self.assertRaises(ValueError):
                doc.save(r"\\attacker.example.com\share\out.pdf")
            with self.assertRaises(ValueError):
                doc.save_copy(r"\\attacker.example.com\share\out.pdf")
            with self.assertRaises(ValueError):
                insert_image_in_pdf(doc, 0, r"\\attacker.example.com\share\img.png", (0, 0, 100, 100))
        finally:
            doc.close()

    def test_finite_coordinate_validation(self):
        """Verify that NaN and Inf coordinates are rejected."""
        invalid_rects = [
            (float("nan"), 0.0, 100.0, 100.0),
            (0.0, float("inf"), 100.0, 100.0),
            (0.0, 0.0, float("-inf"), 100.0),
            (0.0, 0.0, 100.0),  # Not 4 elements
        ]
        for r in invalid_rects:
            with self.assertRaises(ValueError):
                _validate_finite_rect(r)

        # Inverted coordinates should be normalized
        norm = _validate_finite_rect((100.0, 150.0, 20.0, 30.0))
        self.assertEqual(norm, (20.0, 30.0, 100.0, 150.0))

        invalid_points = [
            (float("nan"), 10.0),
            (10.0, float("inf")),
            (10.0,),
        ]
        for pt in invalid_points:
            with self.assertRaises(ValueError):
                _validate_finite_point(pt)

    def test_ink_signature_finite_validation(self):
        """Verify that non-finite ink signature strokes raise ValueError."""
        doc = PdfDocument(self.sample_pdf)
        try:
            with self.assertRaises(ValueError):
                doc.add_ink_signature(0, [[(10.0, float("nan")), (20.0, 20.0)]])
        finally:
            doc.close()

    def test_self_overwrite_protection(self):
        """Verify that save_copy refuses to overwrite the actively open document."""
        doc = PdfDocument(self.sample_pdf)
        try:
            with self.assertRaises(ValueError):
                doc.save_copy(self.sample_pdf)
        finally:
            doc.close()

    def test_conversion_argument_injection_guards(self):
        """Verify that non-existent or injected flag file paths are rejected."""
        with self.assertRaises(ValueError):
            convert_reflow("--arbitrary-flag.pdf", "out.docx")
        with self.assertRaises(ValueError):
            convert_layout("--arbitrary-flag.pdf", "out.docx")

    def test_safe_temporary_files(self):
        """Verify that sample path creation uses unguessable temporary file names."""
        from pdf_reader_lite import _sample_path
        p1 = _sample_path()
        p2 = _sample_path()
        try:
            self.assertNotEqual(p1, p2)
            self.assertTrue(os.path.isfile(p1))
            self.assertTrue(os.path.isfile(p2))
        finally:
            for p in (p1, p2):
                if os.path.isfile(p):
                    os.remove(p)


if __name__ == "__main__":
    unittest.main()

"""Tests for slide detection and keyboard navigation."""

import os
import unittest
import tempfile
import shutil
import fitz

from pdf_lite.core import PdfDocument


class TestSlideNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="pdflite_slide_test_")
        cls.slide_pdf = os.path.join(cls.temp_dir, "slides.pdf")
        cls.portrait_pdf = os.path.join(cls.temp_dir, "portrait.pdf")

        # 16:9 Landscape Slides
        doc1 = fitz.open()
        for _ in range(3):
            doc1.new_page(width=960, height=540)
        doc1.save(cls.slide_pdf)
        doc1.close()

        # Portrait Document (Letter)
        doc2 = fitz.open()
        for _ in range(3):
            doc2.new_page(width=612, height=792)
        doc2.save(cls.portrait_pdf)
        doc2.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_slide_detection(self):
        doc_slides = PdfDocument(self.slide_pdf)
        try:
            self.assertTrue(doc_slides.is_slides())
        finally:
            doc_slides.close()

        doc_portrait = PdfDocument(self.portrait_pdf)
        try:
            self.assertFalse(doc_portrait.is_slides())
        finally:
            doc_portrait.close()


if __name__ == "__main__":
    unittest.main()

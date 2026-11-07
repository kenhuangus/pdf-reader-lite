"""Tests for PDF editing routines: text, images, tables, and saving."""

import os
import tempfile
import unittest

import fitz
from PIL import Image

from pdf_lite.convert import convert_reflow
from pdf_lite.core import PdfDocument, build_sample_pdf
from pdf_lite.editor import (
    delete_content_rect,
    draw_table_in_pdf,
    get_element_at_point,
    get_page_images,
    get_page_tables,
    get_text_properties_at_rect,
    insert_image_in_pdf,
    insert_text_in_pdf,
    replace_image_in_pdf,
    replace_selected_text_in_pdf,
    replace_text_in_pdf,
)


class TestEditorRoutines(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sample_pdf = os.path.join(self.temp_dir.name, "sample.pdf")
        build_sample_pdf(self.sample_pdf)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_text_replacement(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            initial_text = doc.text(0)
            self.assertIn("PDF Lite", initial_text)

            # Find the position of 'PDF Lite'
            rects = doc.page(0).search_for("PDF Lite")
            self.assertTrue(len(rects) > 0)
            target_rect = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1)

            # Replace with updated text
            replace_text_in_pdf(doc, 0, target_rect, "EDITED TITLE HERE")
            hits = doc.page(0).search_for("EDITED TITLE HERE")
            self.assertTrue(len(hits) > 0)
        finally:
            doc.close()

    def test_selected_text_replacement_partial_line(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            initial_text = doc.text(0)
            self.assertIn("A lightweight, Acrobat-style PDF reader for Windows.", initial_text)

            # Find only the word 'lightweight' on line 0
            rects = doc.page(0).search_for("lightweight")
            self.assertTrue(len(rects) > 0)
            target_rect = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1)

            props = get_text_properties_at_rect(doc, 0, target_rect)
            self.assertEqual(props["fontname"], "helv")
            self.assertAlmostEqual(props["fontsize"], 11.5, delta=0.5)

            # Replace strictly the selected word
            replace_selected_text_in_pdf(
                doc,
                0,
                target_rect,
                "fast",
                fontname=props["fontname"],
                fontsize=props["fontsize"],
                text_color=props["color"],
            )

            updated_text = doc.text(0)
            self.assertIn("fast", updated_text)
            self.assertIn("A ", updated_text)
            self.assertIn("Acrobat-style PDF reader for Windows.", updated_text)
            self.assertNotIn("A lightweight", updated_text)
        finally:
            doc.close()

    def test_text_insertion(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            # Insert text at a specific coordinate
            insert_text_in_pdf(doc, 0, (100.0, 700.0), "Floating text annotation")
            hits = doc.page(0).search_for("Floating text annotation")
            self.assertTrue(len(hits) > 0)
        finally:
            doc.close()

    def test_delete_content_rect(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            rects = doc.page(0).search_for("lightweight")
            self.assertTrue(len(rects) > 0)
            for r in rects:
                delete_content_rect(doc, 0, (r.x0, r.y0, r.x1, r.y1))
            hits = doc.page(0).search_for("lightweight")
            self.assertEqual(len(hits), 0)
            self.assertNotIn("lightweight", doc.text(0))
        finally:
            doc.close()

    def test_image_insertion_and_management(self):
        # Create a test image
        img_path = os.path.join(self.temp_dir.name, "test_img.png")
        img = Image.new("RGB", (60, 40), color="blue")
        img.save(img_path)

        doc = PdfDocument(self.sample_pdf)
        try:
            # Verify initial images count
            imgs_before = get_page_images(doc, 0)
            count_before = len(imgs_before)

            # Insert image
            insert_image_in_pdf(doc, 0, img_path, (100.0, 300.0, 200.0, 380.0))
            imgs_after = get_page_images(doc, 0)
            self.assertEqual(len(imgs_after), count_before + 1)

            # Replace image
            new_img_path = os.path.join(self.temp_dir.name, "new_img.png")
            Image.new("RGB", (40, 40), color="green").save(new_img_path)
            target_rect = imgs_after[-1]["rect"]
            replace_image_in_pdf(doc, 0, target_rect, new_img_path)

            imgs_replaced = get_page_images(doc, 0)
            self.assertEqual(len(imgs_replaced), count_before + 1)
        finally:
            doc.close()

    def test_table_insertion_and_detection(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            table_data = [
                ["Col 1", "Col 2"],
                ["Row 1", "Data 1"],
                ["Row 2", "Data 2"],
            ]
            draw_table_in_pdf(doc, 0, (50.0, 400.0, 300.0, 500.0), table_data, has_header=True)
            text = doc.text(0)
            self.assertIn("Col 1", text)
            self.assertIn("Data 1", text)

            # Detect tables on page
            tables = get_page_tables(doc, 0)
            self.assertTrue(len(tables) >= 1)
            self.assertEqual(tables[0]["cols"], 2)
        finally:
            doc.close()

    def test_atomic_inplace_save(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            insert_text_in_pdf(doc, 0, (70.0, 70.0), "PERSISTED EDIT MARKER")
            saved_path = doc.save()
            self.assertEqual(os.path.normcase(saved_path), os.path.normcase(self.sample_pdf))
        finally:
            doc.close()

        # Reopen saved PDF and verify modifications persisted
        reopened = PdfDocument(self.sample_pdf)
        try:
            text = reopened.text(0)
            self.assertIn("PERSISTED EDIT MARKER", text)
        finally:
            reopened.close()

    def test_get_element_at_point(self):
        doc = PdfDocument(self.sample_pdf)
        try:
            # Point on the title line "PDF LITE / OWNER'S MANUAL" at (50, 30)
            elem_text = get_element_at_point(doc, 0, (50.0, 30.0))
            self.assertEqual(elem_text.get("type"), "text_line")
            self.assertIn("PDF LITE", elem_text.get("text", ""))

            # Point on empty space at (300, 700)
            elem_empty = get_element_at_point(doc, 0, (300.0, 700.0))
            self.assertEqual(elem_empty.get("type"), "empty")

            # Draw a table and check table hit
            draw_table_in_pdf(doc, 0, (50.0, 500.0, 250.0, 600.0), [["A", "B"], ["1", "2"]])
            elem_tab = get_element_at_point(doc, 0, (100.0, 550.0))
            self.assertEqual(elem_tab.get("type"), "table")
        finally:
            doc.close()

    def test_word_conversion_page_progress(self):
        progress_calls = []

        def on_progress(curr, total, detail=""):
            progress_calls.append((curr, total, detail))

        out_docx = os.path.join(self.temp_dir.name, "out.docx")
        convert_reflow(self.sample_pdf, out_docx, progress_callback=on_progress)

        self.assertTrue(os.path.isfile(out_docx))
        self.assertEqual(len(progress_calls), 8)
        self.assertEqual(progress_calls[0][0], 1)
        self.assertEqual(progress_calls[0][1], 8)
        self.assertEqual(progress_calls[-1][0], 8)
        self.assertIn("8 of 8", progress_calls[-1][2])


if __name__ == "__main__":
    unittest.main()

"""Tests for PDF to Word conversion contrast and color remapping."""

import unittest
from pdf_lite.convert import (
    _parse_color_rgb,
    _relative_luminance,
    _contrast_ratio,
    _needs_contrast_remap,
    _remap_xml_text_colors,
)


class TestContrastAndRemapping(unittest.TestCase):
    def test_relative_luminance_known_values(self):
        # Pure white = 1.0, pure black = 0.0
        self.assertAlmostEqual(_relative_luminance(255, 255, 255), 1.0, places=3)
        self.assertAlmostEqual(_relative_luminance(0, 0, 0), 0.0, places=3)

    def test_contrast_ratio_wcag(self):
        # White vs Black has 21:1 contrast
        cr = _contrast_ratio(1.0, 0.0)
        self.assertAlmostEqual(cr, 21.0, places=1)

    def test_needs_contrast_remap_on_white(self):
        # White on white (contrast 1:1) must be remapped
        self.assertTrue(_needs_contrast_remap((255, 255, 255), (255, 255, 255)))
        # CACACA light gray on white (contrast ~1.6:1) must be remapped
        self.assertTrue(_needs_contrast_remap((202, 202, 202), (255, 255, 255)))
        # FFD966 pale yellow on white (contrast ~1.4:1) must be remapped
        self.assertTrue(_needs_contrast_remap((255, 217, 102), (255, 255, 255)))
        # Dark charcoal #111827 on white (contrast ~17.7:1) must NOT be remapped
        self.assertFalse(_needs_contrast_remap((17, 24, 39), (255, 255, 255)))
        # Deep blue #002699 on white (contrast ~11.8:1) must NOT be remapped
        self.assertFalse(_needs_contrast_remap((0, 38, 153), (255, 255, 255)))

    def test_needs_contrast_remap_on_dark(self):
        # White text on dark navy #002699 (contrast ~11.8:1) must NOT be remapped
        self.assertFalse(_needs_contrast_remap((255, 255, 255), (0, 38, 153)))
        # Dark text on dark navy (contrast < 4.5:1) MUST be remapped
        self.assertTrue(_needs_contrast_remap((17, 24, 39), (0, 38, 153)))

    def test_xml_text_color_remapping(self):
        xml_in = (
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body>'
            b'<w:p><w:r><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:t>White Title</w:t></w:r></w:p>'
            b'<w:p><w:r><w:rPr><w:color w:val="CACACA"/></w:rPr><w:t>Gray Subtitle</w:t></w:r></w:p>'
            b'<w:p><w:r><w:rPr><w:color w:val="002699"/></w:rPr><w:t>Dark Blue Text</w:t></w:r></w:p>'
            b'</w:body></w:document>'
        )
        xml_out = _remap_xml_text_colors(xml_in)
        self.assertIn(b'w:val="111827"', xml_out)
        self.assertIn(b'w:val="002699"', xml_out)
        self.assertNotIn(b'w:val="FFFFFF"', xml_out)
        self.assertNotIn(b'w:val="CACACA"', xml_out)


if __name__ == "__main__":
    unittest.main()

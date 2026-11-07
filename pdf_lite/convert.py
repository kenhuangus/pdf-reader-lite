"""PDF to Word conversion (editable reflow or LibreOffice layout import)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import fitz

from .core import PasswordRequired, WrongPassword


def find_soffice() -> str | None:
    # Check known trusted system and user installation paths first
    for base in (
        Path("C:/Program Files/LibreOffice/program"),
        Path("C:/Program Files (x86)/LibreOffice/program"),
        Path.home() / "Apps" / "LibreOfficePortable" / "App" / "libreoffice" / "program",
        Path.home() / "AppData" / "Local" / "Programs" / "LibreOffice" / "program",
    ):
        for name in ("soffice.com", "soffice.exe"):
            candidate = str(base / name)
            if os.path.isfile(candidate):
                return candidate

    # Fallback to PATH search only with absolute path check outside current directory
    cwd = os.path.abspath(os.getcwd())
    for name in ("soffice.com", "soffice.exe"):
        found = shutil.which(name)
        if found:
            abs_found = os.path.abspath(found)
            # Guard against DLL/binary planting in current working directory
            if not abs_found.lower().startswith(cwd.lower()) and os.path.isfile(abs_found):
                return abs_found
    return None


def _decrypt_for_conversion(path: str, password: str | None, tmpdir: str) -> str:
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise WrongPassword
            decrypted = os.path.join(tmpdir, "decrypted-input.pdf")
            doc.save(decrypted, encryption=fitz.PDF_ENCRYPT_NONE,
                     garbage=3, deflate=True)
            return decrypted
        return path
    finally:
        doc.close()


DOCX_NAMESPACES = {
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w10": "urn:schemas-microsoft-com:office:word",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}

for prefix, uri in DOCX_NAMESPACES.items():
    ET.register_namespace(prefix, uri)


def _parse_color_rgb(val: str | None) -> tuple[int, int, int] | None:
    if not val:
        return None
    val = val.strip().lower()
    if val in ("auto", "none", "transparent"):
        return None
    named = {
        "white": (255, 255, 255),
        "black": (0, 0, 0),
        "red": (255, 0, 0),
        "green": (0, 128, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
        "navy": (0, 0, 128),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
        "cyan": (0, 255, 255),
        "magenta": (255, 0, 255),
    }
    if val in named:
        return named[val]
    val = val.lstrip("#")
    if len(val) == 6:
        try:
            return (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))
        except ValueError:
            return None
    if len(val) == 3:
        try:
            return (int(val[0] * 2, 16), int(val[1] * 2, 16), int(val[2] * 2, 16))
        except ValueError:
            return None
    return None


def _srgb_to_linear(c: int) -> float:
    val = c / 255.0
    return val / 12.92 if val <= 0.04045 else ((val + 0.055) / 1.055) ** 2.4


def _relative_luminance(r: int, g: int, b: int) -> float:
    """Standard WCAG relative luminance: L = 0.2126*R + 0.7152*G + 0.0722*B."""
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    """WCAG contrast ratio between two relative luminances."""
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _needs_contrast_remap(text_rgb: tuple[int, int, int],
                          bg_rgb: tuple[int, int, int] | None = None) -> bool:
    """Determine whether text_rgb lacks sufficient contrast against bg_rgb (default white).

    On white / light backgrounds, any text color with relative luminance > 0.40 or
    contrast ratio < 4.5:1 (such as light yellow, light green, pastel colors,
    light gray, cyan, white) is illegible and must be remapped.
    """
    text_lum = _relative_luminance(*text_rgb)
    resolved_bg = bg_rgb if bg_rgb is not None else (255, 255, 255)
    bg_lum = _relative_luminance(*resolved_bg)
    cr = _contrast_ratio(bg_lum, text_lum)

    if bg_lum >= 0.50:
        return text_lum > 0.40 or cr < 4.5
    return cr < 4.5


def _remap_xml_text_colors(xml_bytes: bytes,
                           dark_replacement: str = "111827",
                           light_replacement: str = "FFFFFF") -> bytes:
    # Reject entity definitions and DTD declarations to prevent XXE and XML entity expansion attacks
    if b"<!ENTITY" in xml_bytes or b"<!DOCTYPE" in xml_bytes:
        return xml_bytes
    try:
        root = ET.fromstring(xml_bytes)  # nosec B314
    except Exception:
        return xml_bytes

    ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    val_attr = f"{{{ns_w}}}val"
    theme_attr = f"{{{ns_w}}}themeColor"

    # Resolve document-wide page background color if explicitly set
    doc_bg_rgb: tuple[int, int, int] | None = None
    doc_bg = root.find(f".//{{{ns_w}}}background")
    if doc_bg is not None:
        doc_bg_rgb = _parse_color_rgb(doc_bg.attrib.get(f"{{{ns_w}}}color"))

    parent_map = {child: parent for parent in root.iter() for child in parent}
    modified = False

    for elem in root.findall(f".//{{{ns_w}}}color"):
        color_val = elem.attrib.get(val_attr, "")
        text_rgb = _parse_color_rgb(color_val)
        if not text_rgb:
            continue

        # Resolve effective background color from enclosing containers
        curr = elem
        effective_bg_rgb = doc_bg_rgb
        while curr in parent_map:
            curr = parent_map[curr]
            tag = curr.tag.split("}")[-1]

            # 1. Wordprocessing shading: <w:shd w:fill="HEX"/>
            shd = curr.find(f"{{{ns_w}}}shd")
            if shd is not None:
                fill_val = shd.attrib.get(f"{{{ns_w}}}fill") or shd.attrib.get("fill")
                parsed_fill = _parse_color_rgb(fill_val)
                if parsed_fill is not None:
                    effective_bg_rgb = parsed_fill
                    break

            # 2. DrawingML shape: <wps:wsp> -> <wps:spPr> -> <a:solidFill>
            if tag == "wsp":
                for child in curr:
                    if child.tag.endswith("spPr"):
                        # If explicitly noFill, container is transparent
                        if child.find("{http://schemas.openxmlformats.org/drawingml/2006/main}noFill") is not None:
                            effective_bg_rgb = None
                            break
                        sf = child.find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")
                        if sf is not None:
                            for clr in sf:
                                fill_val = clr.attrib.get("val") or clr.attrib.get("lastClr")
                                parsed_sf = _parse_color_rgb(fill_val)
                                if parsed_sf is not None:
                                    effective_bg_rgb = parsed_sf
                                    break
                        break
                if effective_bg_rgb is not None:
                    break

            # 3. VML shape: <v:shape fillcolor="HEX"> -> <v:fill ...>
            if tag == "shape":
                v_fill = curr.find("{urn:schemas-microsoft-com:vml}fill")
                fill_on = v_fill.attrib.get("on") != "false" if v_fill is not None else True
                if not fill_on:
                    effective_bg_rgb = None
                    break
                fc = curr.attrib.get("fillcolor")
                parsed_fc = _parse_color_rgb(fc)
                if parsed_fc is not None:
                    effective_bg_rgb = parsed_fc
                    break
                if v_fill is not None:
                    c2 = v_fill.attrib.get("color2") or v_fill.attrib.get("color")
                    parsed_c2 = _parse_color_rgb(c2)
                    if parsed_c2 is not None:
                        effective_bg_rgb = parsed_c2
                        break

        if _needs_contrast_remap(text_rgb, effective_bg_rgb):
            resolved_bg = effective_bg_rgb if effective_bg_rgb is not None else (255, 255, 255)
            bg_lum = _relative_luminance(*resolved_bg)
            elem.attrib[val_attr] = dark_replacement if bg_lum >= 0.50 else light_replacement
            if theme_attr in elem.attrib:
                del elem.attrib[theme_attr]
            modified = True

    if not modified:
        return xml_bytes

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _postprocess_layout_docx(produced_path: str, target_path: str) -> None:
    """Remap invisible/low-contrast text colors in LibreOffice layout DOCX exports to legible high-contrast text."""
    max_entry_size = 64_000_000
    max_total_size = 256_000_000
    total_uncompressed = 0
    temp_target = target_path + ".tmp.docx"
    try:
        with zipfile.ZipFile(produced_path, "r") as zin, \
             zipfile.ZipFile(temp_target, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                # Defend against Zip Slip path traversal
                if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                    continue
                # Defend against zip decompression bombs
                if item.file_size > max_entry_size:
                    raise ValueError(f"Zip entry {name} exceeds size limit")
                total_uncompressed += item.file_size
                if total_uncompressed > max_total_size:
                    raise ValueError("Total uncompressed archive size exceeds limit")

                content = zin.read(item.filename)
                if item.filename == "word/document.xml" or (
                    item.filename.startswith("word/")
                    and any(k in item.filename for k in ("header", "footer", "footnote", "endnote"))
                    and item.filename.endswith(".xml")
                ):
                    content = _remap_xml_text_colors(content)
                zout.writestr(item, content)
        if os.path.isfile(target_path):
            os.remove(target_path)
        os.replace(temp_target, target_path)
    except Exception:
        if os.path.isfile(temp_target):
            try:
                os.remove(temp_target)
            except OSError:
                pass
        shutil.copy2(produced_path, target_path)


def convert_layout(path: str, out_path: str, password: str | None = None,
                   progress_callback: Callable[[int, int, str], None] | None = None) -> str:
    """Use LibreOffice Writer's PDF import to preserve page layout."""
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Remote network (UNC) paths are blocked for security")
    if str(out_path).startswith(("\\\\", "//")):
        raise ValueError("Saving to remote network (UNC) paths is blocked for security")
    base_name = os.path.basename(path)
    if base_name.startswith(("-", "/")):
        raise ValueError(f"Dangerous input file name: {base_name}")

    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice was not found on this computer")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Input file not found: {path}")
    target = os.path.abspath(out_path)
    tmpdir = tempfile.mkdtemp(prefix="pdflite_word_")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    try:
        if progress_callback is not None:
            progress_callback(1, 3, "Preparing PDF and checking encryption…")
        source = _decrypt_for_conversion(path, password, tmpdir)
        source = os.path.abspath(source)
        profile = Path(tmpdir) / "lo-profile"
        profile.mkdir(parents=True, exist_ok=True)
        outdir = Path(tmpdir) / "out"
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--writer",
            "--infilter=writer_pdf_import",
            "--convert-to", "docx:Office Open XML Text",
            "--outdir", str(outdir),
            f"-env:UserInstallation={profile.as_uri()}",
            source,
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if progress_callback is not None:
            progress_callback(2, 3, "Converting layout via LibreOffice Writer…")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240,
                                creationflags=flags)
        produced = outdir / (Path(source).stem + ".docx")
        if result.returncode != 0 or not produced.is_file():
            detail = (result.stdout or "").strip() + "\n" + (result.stderr or "").strip()
            detail = detail.strip() or f"LibreOffice exited with code {result.returncode}"
            raise RuntimeError(f"LibreOffice conversion failed:\n{detail[-1200:]}")
        if progress_callback is not None:
            progress_callback(3, 3, "Applying high-contrast formatting…")
        _postprocess_layout_docx(str(produced), target)
        return "LibreOffice Writer PDF import (layout-faithful)"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def convert_reflow(path: str, out_path: str, password: str | None = None,
                   progress_callback: Callable[[int, int, str], None] | None = None) -> str:
    """Build an editable Word document from the PDF text layer.

    The output uses ordinary Word paragraphs and headings, so text is easy to
    edit.  Absolute page positioning and images are intentionally omitted.
    """
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Remote network (UNC) paths are blocked for security")
    if str(out_path).startswith(("\\\\", "//")):
        raise ValueError("Saving to remote network (UNC) paths is blocked for security")
    base_name = os.path.basename(path)
    if base_name.startswith(("-", "/")):
        raise ValueError(f"Dangerous input file name: {base_name}")

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Input file not found: {path}")
    target = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError("python-docx is required for editable conversion") from exc

    src = fitz.open(path)
    if src.needs_pass:
        if not password or not src.authenticate(password):
            src.close()
            raise WrongPassword
    try:
        word = Document()
        section = word.sections[0]
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)
        styles = word.styles
        styles["Normal"].font.name = "Calibri"
        styles["Normal"].font.size = Pt(11)
        try:
            styles["Normal"].font.color.rgb = RGBColor(0x11, 0x18, 0x27)
            styles["Normal"].paragraph_format.space_after = Pt(4)
            styles["Normal"].paragraph_format.line_spacing = 1.15
        except Exception:
            pass
        for style_name in ("Heading 1", "Heading 2", "Heading 3"):
            try:
                styles[style_name].font.name = "Calibri"
                styles[style_name].font.color.rgb = RGBColor(0x11, 0x18, 0x27)
                styles[style_name].paragraph_format.space_before = Pt(8)
                styles[style_name].paragraph_format.space_after = Pt(4)
            except KeyError:
                pass

        def _clean_str(val: object) -> str:
            s = str(val or "")
            return "".join(c for c in s if c.isprintable() or c in "\r\n\t").strip()

        meta = src.metadata or {}
        title = _clean_str(meta.get("title") or Path(path).stem)
        word.core_properties.title = title
        if meta.get("author"):
            word.core_properties.author = _clean_str(meta["author"])
        if meta.get("subject"):
            word.core_properties.subject = _clean_str(meta["subject"])
        if meta.get("keywords"):
            word.core_properties.keywords = _clean_str(meta["keywords"])

        total_pages = src.page_count
        for page_number in range(total_pages):
            if progress_callback is not None:
                progress_callback(page_number + 1, total_pages, f"Converting page {page_number + 1} of {total_pages}…")
            page = src[page_number]
            data = page.get_text("dict", sort=True)
            blocks = [b for b in data.get("blocks", []) if b.get("type") == 0]
            wrote_something = False
            for block in blocks:
                lines = block.get("lines", [])
                if not lines:
                    continue
                first_size = 0.0
                for line in lines:
                    for span in line.get("spans", []):
                        first_size = max(first_size, float(span.get("size", 0.0)))
                text_probe = "".join(span.get("text", "") for line in lines
                                     for span in line.get("spans", [])).strip()
                if not text_probe:
                    continue
                # Drop obvious running headers and footers from the reflow.
                bbox = block.get("bbox", (0, 0, 0, 0))
                if first_size <= 9.5 and (bbox[1] < 80 or bbox[3] > page.rect.height - 70):
                    continue
                if text_probe.startswith("PDF LITE  /  OWNER'S MANUAL"):
                    continue
                if text_probe.startswith("Ctrl+O open"):
                    continue
                is_heading = first_size >= 14.5
                paragraph = word.add_paragraph(
                    style="Heading 1" if first_size >= 20
                    else "Heading 2" if first_size >= 14.5
                    else None)
                if is_heading:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                for line_index, line in enumerate(lines):
                    if line_index:
                        paragraph.add_run().add_break()
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text:
                            continue
                        run = paragraph.add_run(text)
                        flags = int(span.get("flags", 0))
                        if not is_heading:
                            try:
                                run.font.size = Pt(float(span.get("size", 11)))
                            except Exception:
                                pass
                        run.bold = bool(flags & 8) or is_heading
                        run.italic = bool(flags & 2)
                        font_name = span.get("font", "")
                        if font_name and not is_heading:
                            run.font.name = font_name
                        span_color = span.get("color")
                        if span_color is not None:
                            r = (span_color >> 16) & 0xFF
                            g = (span_color >> 8) & 0xFF
                            b = span_color & 0xFF
                            if _needs_contrast_remap((r, g, b), (255, 255, 255)):
                                run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
                            else:
                                run.font.color.rgb = RGBColor(r, g, b)
                        else:
                            run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
                wrote_something = True
            if page_number < total_pages - 1 and wrote_something:
                word.add_page_break()
        word.save(out_path)
        return "Editable text reflow (paragraphs and headings)"
    finally:
        src.close()


def convert_pdf_to_word(path: str, out_path: str, password: str | None = None,
                        mode: str = "reflow",
                        progress_callback: Callable[[int, int, str], None] | None = None) -> str:
    if mode == "layout":
        return convert_layout(path, out_path, password, progress_callback)
    return convert_reflow(path, out_path, password, progress_callback)


__all__ = ["convert_pdf_to_word", "convert_layout", "convert_reflow", "find_soffice"]

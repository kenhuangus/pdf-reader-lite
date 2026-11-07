"""PDF Lite — a small Acrobat-style PDF reader."""

from .core import BASE_SCALE, PdfDocument, PasswordRequired, WrongPassword, build_sample_pdf

__version__ = "1.0.0"

__all__ = [
    "BASE_SCALE",
    "PdfDocument",
    "PasswordRequired",
    "WrongPassword",
    "build_sample_pdf",
]

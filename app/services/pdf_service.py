"""PDF analysis and text extraction using PyMuPDF."""

from __future__ import annotations

import fitz

from app.core.exceptions import PdfProcessingError


class PdfService:
    """Stateless helpers around PyMuPDF.

    All methods are synchronous and CPU/IO bound; call them from a worker
    thread (``asyncio.to_thread``) when used inside async code.
    """

    @staticmethod
    def _open(path: str) -> fitz.Document:
        try:
            return fitz.open(path)
        except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several types
            raise PdfProcessingError(
                "The PDF could not be opened. It may be corrupted.", details=str(exc)
            ) from exc

    def analyze(self, path: str, max_pages: int = 20) -> "PdfAnalysis":
        document = self._open(path)

        try:
            if document.is_encrypted:
                raise PdfProcessingError("Password-protected PDFs are not supported.")

            page_count = document.page_count
            if page_count == 0:
                raise PdfProcessingError("The PDF contains no pages.")
            if page_count > max_pages:
                raise PdfProcessingError(
                    f"PDF exceeds the {max_pages}-page limit.",
                    details={"pages": page_count, "limit": max_pages},
                )

            text_by_page: list[str] = []
            for page in document:
                try:
                    text_by_page.append(page.get_text("text", sort=True) or "")
                except TypeError:  # older PyMuPDF without the sort keyword
                    text_by_page.append(page.get_text("text") or "")
        finally:
            document.close()

        total_chars = sum(len(text.strip()) for text in text_by_page)
        printable = sum(char.isalnum() for text in text_by_page for char in text)
        alnum_ratio = printable / total_chars if total_chars else 0.0

        # A digital PDF has a meaningful text layer with mostly real characters.
        is_digital = total_chars >= 40 and alnum_ratio >= 0.25

        warnings: list[str] = []
        if 0 < total_chars < 40:
            warnings.append("Very little selectable text found; treating as scanned.")

        return PdfAnalysis(
            page_count=page_count,
            is_digital=is_digital,
            total_chars=total_chars,
            text_by_page=text_by_page,
            warnings=warnings,
        )

    def extract_text(self, path: str) -> str:
        document = self._open(path)
        try:
            pages = []
            for index, page in enumerate(document, start=1):
                try:
                    text = page.get_text("text", sort=True) or ""
                except TypeError:
                    text = page.get_text("text") or ""
                pages.append(f"--- Page {index} ---\n{text.strip()}")
            return "\n\n".join(pages).strip()
        finally:
            document.close()

    def render_page_png(self, path: str, page_index: int, dpi: int = 300) -> bytes:
        document = self._open(path)
        try:
            if page_index < 0 or page_index >= document.page_count:
                raise PdfProcessingError("Requested page does not exist.")
            page = document.load_page(page_index)
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return pixmap.tobytes("png")
        finally:
            document.close()

    def render_all_pages_png(self, path: str, dpi: int = 300) -> list[bytes]:
        document = self._open(path)
        try:
            images: list[bytes] = []
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for page in document:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                images.append(pixmap.tobytes("png"))
            return images
        finally:
            document.close()


class PdfAnalysis:
    """Result of inspecting a PDF's text layer."""

    __slots__ = ("page_count", "is_digital", "total_chars", "text_by_page", "warnings")

    def __init__(
        self,
        *,
        page_count: int,
        is_digital: bool,
        total_chars: int,
        text_by_page: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        self.page_count = page_count
        self.is_digital = is_digital
        self.total_chars = total_chars
        self.text_by_page = text_by_page
        self.warnings = warnings or []

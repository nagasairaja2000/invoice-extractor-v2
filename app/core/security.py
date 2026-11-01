"""Upload security: validation, safe filenames and streaming persistence."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz
from fastapi import UploadFile

from app.config import Settings
from app.core.exceptions import (
    FileTooLargeError,
    InvalidPdfError,
    PdfProcessingError,
    UnsupportedFileTypeError,
)

PDF_EXTENSION = ".pdf"
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
}
PDF_MAGIC = b"%PDF-"
CHUNK_SIZE = 1024 * 1024  # 1 MiB
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(slots=True)
class SavedUpload:
    path: Path
    original_name: str
    display_name: str
    size: int
    sha256: str


def sanitize_filename(name: str | None) -> str:
    """Return a safe display filename (never used as a path)."""
    candidate = Path(name or "invoice.pdf").name
    candidate = _UNSAFE_CHARS.sub("_", candidate).strip(" ._")
    if not candidate.lower().endswith(PDF_EXTENSION):
        candidate = f"{candidate}.pdf" if candidate else "invoice.pdf"
    return candidate[:120]


def _check_extension_and_type(upload: UploadFile) -> None:
    name = upload.filename or ""
    if Path(name).suffix.lower() != PDF_EXTENSION:
        raise UnsupportedFileTypeError(
            "Only PDF files are supported.", details={"filename": name}
        )

    content_type = (upload.content_type or "").lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedFileTypeError(
            "The uploaded file does not look like a PDF.",
            details={"content_type": content_type},
        )


def _assert_pdf_magic(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(1024)
    if PDF_MAGIC not in header:
        raise InvalidPdfError("The file is not a valid PDF (missing PDF signature).")


async def save_upload(upload: UploadFile, settings: Settings) -> SavedUpload:
    """Stream an upload to a unique file, enforcing size limits.

    The file is written under a UUID name; the original filename is kept only
    for display. Any failure removes the partial file.
    """
    _check_extension_and_type(upload)

    original_name = upload.filename or "invoice.pdf"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.upload_dir / f"{uuid.uuid4().hex}{PDF_EXTENSION}"

    digest = hashlib.sha256()
    size = 0

    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.max_file_size_bytes:
                    raise FileTooLargeError(
                        f"File exceeds the {settings.max_file_size_mb} MB limit.",
                        details={"filename": original_name, "limit_mb": settings.max_file_size_mb},
                    )
                digest.update(chunk)
                handle.write(chunk)

        if size == 0:
            raise InvalidPdfError("The uploaded file is empty.", details={"filename": original_name})

        _assert_pdf_magic(destination)

        return SavedUpload(
            path=destination,
            original_name=original_name,
            display_name=sanitize_filename(original_name),
            size=size,
            sha256=digest.hexdigest(),
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def validate_pdf_structure(path: Path, settings: Settings) -> None:
    """Open the PDF with PyMuPDF and enforce structural limits.

    Runs in a worker thread because PyMuPDF is CPU/IO bound. The file is read
    into memory and opened as a stream: when MuPDF rejects a corrupt file it
    can keep a file handle open on Windows, which would block cleanup.
    """
    try:
        document = fitz.open(stream=path.read_bytes(), filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - library raises many exception types
        raise InvalidPdfError("The PDF could not be opened.", details=str(exc)) from exc

    try:
        if document.is_encrypted:
            raise PdfProcessingError("Password-protected PDFs are not supported.")
        if document.page_count == 0:
            raise PdfProcessingError("The PDF contains no pages.")
        if document.page_count > settings.max_pages:
            raise PdfProcessingError(
                f"PDF exceeds the {settings.max_pages}-page limit.",
                details={"pages": document.page_count, "limit": settings.max_pages},
            )
    finally:
        document.close()

"""
apps/talent/resume_parser/extraction.py

Extract raw text from a Resume file (PDF, DOCX, plain-text).
"""

import re
from io import BytesIO

from .exceptions import ManualReviewRequired

MIN_EXTRACTED_LENGTH = 50


def extract_text(resume) -> tuple:
    """
    Open resume.file and extract text.
    Returns (raw_text, cleaned_text, engine_name, confidence).
    Raises ManualReviewRequired on any unrecoverable extraction failure.
    """
    content_type = (resume.content_type or '').lower().strip()
    original_filename = resume.original_filename or ''
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''

    try:
        resume.file.open('rb')
        raw_bytes = resume.file.read()
        resume.file.close()
    except Exception as exc:
        raise ManualReviewRequired(f"Cannot open resume file: {exc}")

    file_obj = BytesIO(raw_bytes)

    # Determine format
    is_pdf = content_type == 'application/pdf' or ext == 'pdf'
    is_docx = (
        content_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        or ext == 'docx'
    )
    is_doc = content_type == 'application/msword' or ext == 'doc'
    is_text = content_type == 'text/plain' or ext == 'txt'

    if is_pdf:
        return _extract_pdf(file_obj)

    if is_docx:
        return _extract_docx(file_obj)

    if is_doc:
        raise ManualReviewRequired(
            "Legacy .doc format is not supported; please convert to DOCX or PDF."
        )

    if is_text:
        return _extract_plain_text(raw_bytes)

    # Unknown type — attempt PDF → DOCX → text
    for attempt in (_extract_pdf, _extract_docx):
        try:
            file_obj.seek(0)
            return attempt(file_obj)
        except ManualReviewRequired:
            pass

    try:
        return _extract_plain_text(raw_bytes)
    except ManualReviewRequired:
        pass

    raise ManualReviewRequired(
        f"Cannot extract text from file type '{content_type or ext or 'unknown'}'."
    )


# ─── Format-specific helpers ──────────────────────────────────────────────────

def _extract_pdf(file_obj: BytesIO) -> tuple:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ManualReviewRequired("pypdf not installed; PDF extraction unavailable.")

    try:
        reader = PdfReader(file_obj)
        pages = [page.extract_text() or '' for page in reader.pages]
        raw_text = '\n'.join(pages)
    except Exception as exc:
        raise ManualReviewRequired(f"PDF extraction failed: {exc}")

    cleaned = _clean_text(raw_text)
    if not cleaned:
        raise ManualReviewRequired(
            "PDF contains no extractable text — may be a scanned image."
        )

    return raw_text, cleaned, 'pypdf', _confidence(raw_text, cleaned)


def _extract_docx(file_obj: BytesIO) -> tuple:
    try:
        from docx import Document
    except ImportError:
        raise ManualReviewRequired("python-docx not installed; DOCX extraction unavailable.")

    try:
        doc = Document(file_obj)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        raw_text = '\n'.join(paragraphs)
    except Exception as exc:
        raise ManualReviewRequired(f"DOCX extraction failed: {exc}")

    cleaned = _clean_text(raw_text)
    if not cleaned:
        raise ManualReviewRequired("DOCX contains no extractable text.")

    return raw_text, cleaned, 'python-docx', _confidence(raw_text, cleaned)


def _extract_plain_text(raw_bytes: bytes) -> tuple:
    try:
        raw_text = raw_bytes.decode('utf-8', errors='replace')
    except Exception as exc:
        raise ManualReviewRequired(f"Text decoding failed: {exc}")

    cleaned = _clean_text(raw_text)
    if not cleaned:
        raise ManualReviewRequired("Text file is empty.")

    return raw_text, cleaned, 'plain_text', 0.9


# ─── Utilities ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Strip control characters, collapse excess whitespace."""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{3,}', '  ', text)
    return text.strip()


def _confidence(raw: str, cleaned: str) -> float:
    if not raw:
        return 0.0
    ratio = len(cleaned) / max(len(raw), 1)
    if len(cleaned) >= 500:
        return round(min(0.9, 0.5 + ratio * 0.4), 2)
    if len(cleaned) >= 100:
        return 0.65
    return 0.45

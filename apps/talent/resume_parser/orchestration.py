"""
apps/talent/resume_parser/orchestration.py

Coordinates the full resume parsing pipeline:
  extracting → parsing → validating → (normalize + persist) → indexed

Status transitions are written to the DB at each step.
ManualReviewRequired → manual_review.
Any other exception is caught at the top level → failed.
"""

_MIN_TEXT_LENGTH = 50


def run_pipeline(resume) -> None:
    """
    Entry point called by the Celery task.
    Never raises — all outcomes are written to resume.status.
    """
    try:
        _run(resume)
    except Exception as exc:
        _set_status_with_error(resume, 'failed', str(exc)[:2000])


def _run(resume) -> None:
    from apps.talent.models import Resume
    from .exceptions import ManualReviewRequired
    from .extraction import extract_text
    from .deterministic_parser import parse_resume_text
    from .normalization import normalize_parsed_json
    from .persistence import persist_parsed_data
    from .validation import validate_parsed_json

    # ── 1. Extract ──────────────────────────────────────────────────────────
    _set_status(resume, 'extracting')
    try:
        raw_text, cleaned_text, engine, ext_confidence = extract_text(resume)
    except ManualReviewRequired as exc:
        _set_manual_review(resume, str(exc))
        return

    Resume.objects.filter(pk=resume.pk).update(
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        extraction_engine=engine,
        extraction_confidence=ext_confidence,
    )
    resume.raw_text = raw_text
    resume.cleaned_text = cleaned_text

    if len(cleaned_text.strip()) < _MIN_TEXT_LENGTH:
        _set_manual_review(resume, 'Extracted text is too short for reliable parsing.')
        return

    # ── 2. Parse ────────────────────────────────────────────────────────────
    _set_status(resume, 'parsing')
    try:
        parsed_json = parse_resume_text(cleaned_text)
    except ManualReviewRequired as exc:
        _set_manual_review(resume, str(exc))
        return

    # ── 3. Validate ─────────────────────────────────────────────────────────
    _set_status(resume, 'validating')
    validation_errors, missing_fields = validate_parsed_json(parsed_json)

    if 'no_identifier' in validation_errors:
        _set_manual_review(resume, validation_errors['no_identifier'])
        return

    # ── 4. Normalize ────────────────────────────────────────────────────────
    normalized_json = normalize_parsed_json(parsed_json)

    # ── 5. Persist ──────────────────────────────────────────────────────────
    confidence = parsed_json.get('confidence')
    persist_parsed_data(
        resume, normalized_json, parsed_json,
        validation_errors, missing_fields, confidence,
    )

    # ── 6. Done ─────────────────────────────────────────────────────────────
    from decimal import Decimal, InvalidOperation
    conf_dec = None
    if confidence is not None:
        try:
            conf_dec = Decimal(str(confidence)).quantize(Decimal('0.01'))
            conf_dec = max(Decimal('0.00'), min(Decimal('1.00'), conf_dec))
        except InvalidOperation:
            pass

    Resume.objects.filter(pk=resume.pk).update(
        status='indexed',
        parser_engine='deterministic_v1',
        parser_confidence=conf_dec,
    )
    resume.status = 'indexed'


# ─── Status helpers ───────────────────────────────────────────────────────────

def _set_status(resume, status: str) -> None:
    from apps.talent.models import Resume
    Resume.objects.filter(pk=resume.pk).update(status=status)
    resume.status = status


def _set_manual_review(resume, reason: str) -> None:
    from apps.talent.models import Resume
    Resume.objects.filter(pk=resume.pk).update(
        status='manual_review',
        manual_review_reason=reason[:2000],
    )
    resume.status = 'manual_review'
    resume.manual_review_reason = reason[:2000]


def _set_status_with_error(resume, status: str, error: str) -> None:
    from apps.talent.models import Resume
    Resume.objects.filter(pk=resume.pk).update(
        status=status,
        error_message=error,
    )
    resume.status = status
    resume.error_message = error

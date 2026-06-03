"""
apps/intake/services.py

Intake submission business logic:
  normalize_mobile, get_or_create_candidate, detect_duplicate_submission,
  create_intake_submission, create_intake_documents.
"""

import re
from pathlib import Path
from datetime import timedelta

from django.db import IntegrityError, connection
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.talent.models import Candidate
from .models import (
    IntakeSubmission,
    IntakeSubmissionAnswer,
    IntakeDocument,
    FormField,
    FormTemplateField,
)


# Constants

_FAKE_NUMBERS = {'0000000000', '1111111111', '1234567890', '9999999999'}
_ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'}
_ALLOWED_CONTENT_TYPES = {
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'image/jpeg',
    'image/png',
}
MAX_FILES_PER_SUBMISSION = 5
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024


# Normalization

def normalize_mobile(mobile: str) -> str:
    mobile = mobile.strip().replace(' ', '').replace('-', '')
    if mobile.startswith('+91'):
        mobile = mobile[3:]
    elif mobile.startswith('91') and len(mobile) == 12:
        mobile = mobile[2:]
    return mobile


def validate_mobile(value: str) -> str:
    normalized = normalize_mobile(value)
    if not re.match(r'^[6-9]\d{9}$', normalized):
        raise serializers.ValidationError("Enter a valid 10-digit Indian mobile number.")
    if normalized in _FAKE_NUMBERS:
        raise serializers.ValidationError("Please enter a valid mobile number.")
    return normalized


def normalize_role_title(title: str) -> str:
    return ' '.join(title.strip().lower().split())


# Candidate get-or-create

def get_or_create_candidate(org, phone_normalized: str, first_name: str,
                             middle_name: str, last_name: str):
    """
    Get or create a talent.Candidate by (org, phone_normalized).
    Updates name fields on re-submission.
    """
    defaults = {
        'phone': phone_normalized,
        'first_name': first_name or 'Unknown',
        'last_name': last_name or 'Unknown',
        'middle_name': middle_name or '',
        'source': 'qr',
    }
    try:
        candidate, created = Candidate.objects.get_or_create(
            org=org,
            phone_normalized=phone_normalized,
            defaults=defaults,
        )
    except IntegrityError:
        candidate = Candidate.objects.get(org=org, phone_normalized=phone_normalized)
        created = False

    if not created and (first_name or last_name):
        update_fields = []
        if first_name and candidate.first_name != first_name:
            candidate.first_name = first_name
            update_fields.append('first_name')
        if middle_name and candidate.middle_name != middle_name:
            candidate.middle_name = middle_name
            update_fields.append('middle_name')
        if last_name and candidate.last_name != last_name:
            candidate.last_name = last_name
            update_fields.append('last_name')
        if update_fields:
            candidate.save(update_fields=update_fields)

    return candidate, created


# Duplicate detection

def detect_duplicate_submission(candidate, campaign, job_role,
                                 other_role_title_normalized='') -> tuple:
    """
    Returns (is_duplicate, reason_str).
    A duplicate is a submission from the same candidate within 24h for the
    same campaign+role combination.
    """
    threshold = timezone.now() - timedelta(hours=24)
    qs = IntakeSubmission.objects.filter(
        candidate=candidate,
        campaign=campaign,
        submitted_at__gte=threshold,
    )
    if job_role:
        qs = qs.filter(job_role=job_role, other_role_title_normalized='')
    else:
        qs = qs.filter(job_role__isnull=True,
                       other_role_title_normalized=other_role_title_normalized)

    if qs.exists():
        if job_role:
            reason = "Same mobile submitted for same campaign and role within 24 hours"
        else:
            reason = (
                f"Same mobile submitted for 'Other: {other_role_title_normalized}' "
                "within 24 hours"
            )
        return True, reason
    return False, ''


# File validation

def validate_submission_file(uploaded_file):
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise serializers.ValidationError(
            f"File '{uploaded_file.name}' has an unsupported file type."
        )
    ct = getattr(uploaded_file, 'content_type', '')
    if ct and ct not in _ALLOWED_CONTENT_TYPES:
        raise serializers.ValidationError(
            f"File '{uploaded_file.name}' has an unsupported content type."
        )
    if uploaded_file.size > MAX_UPLOAD_SIZE_BYTES:
        raise serializers.ValidationError(
            f"File '{uploaded_file.name}' exceeds the 10 MB limit."
        )
    return uploaded_file


def _iter_uploaded_files(files):
    all_files = []
    for field_name in files:
        for f in files.getlist(field_name):
            all_files.append((field_name, f))
    return all_files


def validate_submission_documents(campaign, job_role, files) -> None:
    all_files = _iter_uploaded_files(files)

    if len(all_files) > MAX_FILES_PER_SUBMISSION:
        raise serializers.ValidationError(
            f"A maximum of {MAX_FILES_PER_SUBMISSION} files can be uploaded."
        )

    for _, f in all_files:
        validate_submission_file(f)

    provided_names = {field_name for field_name, _ in all_files}
    missing = []

    if campaign.form_template_id:
        required_file_fields = FormTemplateField.objects.filter(
            section__template=campaign.form_template,
            is_active=True,
            is_required=True,
            field_type='file',
        ).filter(Q(role__isnull=True) | Q(role=job_role))
        for field in required_file_fields:
            allowed_names = {field.field_key, f'template_field_{field.id}'}
            if not provided_names.intersection(allowed_names):
                missing.append(field.label)
    else:
        required_file_fields = FormField.objects.filter(
            campaign=campaign,
            is_active=True,
            is_required=True,
            field_type='file',
        ).filter(Q(role__isnull=True) | Q(role=job_role))
        for field in required_file_fields:
            allowed_names = {field.field_key, f'field_{field.id}', f'file_{field.id}'}
            if not provided_names.intersection(allowed_names):
                missing.append(field.label)

    if missing:
        raise serializers.ValidationError(
            f"Required documents missing: {', '.join(missing)}."
        )


def _document_type_from_field_name(field_name: str) -> str:
    n = field_name.lower()
    if n == 'resume':
        return 'resume'
    if n in {'id_proof', 'idproof', 'identity'}:
        return 'id_proof'
    if n in {'certificate', 'certificates'}:
        return 'certificate'
    return 'other'


def _get_field_fk_for_file(submission, field_name: str):
    """
    Returns (campaign_field_or_none, template_field_or_none).
    Checks template_field_ prefix first, then campaign field_ / file_ prefixes.
    """
    if field_name.startswith('template_field_'):
        raw_id = field_name[len('template_field_'):]
        if raw_id.isdigit() and submission.campaign.form_template_id:
            tmpl_field = FormTemplateField.objects.filter(
                id=int(raw_id),
                section__template=submission.campaign.form_template,
                field_type='file',
                is_active=True,
            ).first()
            if tmpl_field:
                return None, tmpl_field
    for prefix in ('field_', 'file_'):
        if field_name.startswith(prefix):
            raw_id = field_name[len(prefix):]
            if raw_id.isdigit():
                campaign_field = submission.campaign.form_fields.filter(
                    id=int(raw_id), field_type='file', is_active=True,
                ).first()
                if campaign_field:
                    return campaign_field, None
    field = submission.campaign.form_fields.filter(
        field_key=field_name,
        field_type='file',
        is_active=True,
    ).first()
    if field:
        return field, None
    return None, None


# Submission creation

def _get_client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def create_intake_submission(validated_data: dict, request=None) -> IntakeSubmission:
    """
    Core submission creation service.
    validated_data comes from SubmissionCreateSerializer.validate().
    """
    campaign = validated_data['campaign']
    job_role = validated_data.get('job_role')
    other_role_title = validated_data.get('other_role_title', '').strip()
    other_role_title_normalized = normalize_role_title(other_role_title) if other_role_title else ''

    first_name = validated_data.get('first_name', '').strip()
    middle_name = validated_data.get('middle_name', '').strip()
    last_name = validated_data.get('last_name', '').strip()
    full_name = ' '.join(p for p in [first_name, middle_name, last_name] if p)

    mobile_raw = validated_data['mobile_number']
    mobile_normalized = normalize_mobile(mobile_raw)

    candidate, _ = get_or_create_candidate(
        org=campaign.org,
        phone_normalized=mobile_normalized,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
    )

    if connection.vendor != 'sqlite':
        candidate = Candidate.objects.select_for_update().get(pk=candidate.pk)

    is_duplicate, duplicate_reason = detect_duplicate_submission(
        candidate, campaign, job_role, other_role_title_normalized,
    )

    if is_duplicate and not campaign.allow_duplicates:
        raise serializers.ValidationError(
            "You have already submitted an application for this role within the last 24 hours."
        )

    ip_address = _get_client_ip(request) if request else None
    user_agent = (request.META.get('HTTP_USER_AGENT', '') if request else '')[:1024]

    submission = IntakeSubmission.objects.create(
        campaign=campaign,
        site=campaign.site,
        candidate=candidate,
        job_role=job_role,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        full_name=full_name,
        other_role_title=other_role_title,
        other_role_title_normalized=other_role_title_normalized,
        mobile_number=mobile_raw,
        mobile_number_normalized=mobile_normalized,
        language=validated_data.get('language', campaign.default_language),
        is_possible_duplicate=is_duplicate,
        duplicate_reason=duplicate_reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    answers_data = validated_data.get('answers', [])
    if answers_data:
        bulk_answers = []
        for ans in answers_data:
            campaign_field = ans.get('field')
            tmpl_field = ans.get('template_field')
            field_obj = campaign_field or tmpl_field
            bulk_answers.append(IntakeSubmissionAnswer(
                submission=submission,
                field=campaign_field,
                template_field=tmpl_field,
                field_source=ans.get('field_source', 'campaign'),
                field_label_snapshot=field_obj.label if field_obj else '',
                field_type_snapshot=field_obj.field_type if field_obj else '',
                value=ans['value'],
            ))
        IntakeSubmissionAnswer.objects.bulk_create(bulk_answers)

    return submission


def _link_resume_if_needed(doc: IntakeDocument, candidate) -> None:
    """If doc is a resume document, ensure exactly one talent.Resume exists for it."""
    if doc.document_type != 'resume':
        return
    from apps.talent.models import Resume
    from apps.talent.services import compute_file_hash, queue_resume_processing

    file_hash = ''
    try:
        file_hash = compute_file_hash(doc.file)
    except Exception:
        pass

    resume, created = Resume.objects.get_or_create(
        source_intake_document=doc,
        defaults={
            'candidate': candidate,
            'file': doc.file,
            'original_filename': doc.original_filename,
            'content_type': doc.content_type,
            'size_bytes': doc.size_bytes,
            'source_type': 'qr_intake',
            'status': 'uploaded',
            'file_hash': file_hash,
        },
    )

    if created:
        queue_resume_processing(resume)


def create_intake_documents(submission: IntakeSubmission, files) -> list:
    validate_submission_documents(submission.campaign, submission.job_role, files)
    all_files = _iter_uploaded_files(files)

    documents = []
    for field_name, f in all_files:
        campaign_field, tmpl_field = _get_field_fk_for_file(submission, field_name)
        doc = IntakeDocument.objects.create(
            submission=submission,
            field=campaign_field,
            template_field=tmpl_field,
            field_source='template' if tmpl_field else 'campaign',
            document_type=_document_type_from_field_name(field_name),
            file=f,
            original_filename=f.name,
            content_type=getattr(f, 'content_type', ''),
            size_bytes=f.size,
        )
        _link_resume_if_needed(doc, submission.candidate)
        documents.append(doc)

    return documents

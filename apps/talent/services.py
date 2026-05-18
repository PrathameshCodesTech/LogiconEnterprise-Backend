"""
apps/talent/services.py

Talent processing service helpers (Phase Talent-Hiring-A/B).
These are synchronous stubs — async/Celery/OCR/LLM dispatch is wired in a
later phase.  The status transitions are the authoritative state machine.
"""

import hashlib
import re

from django.db import transaction
from rest_framework import serializers as drf_serializers

from .models import Resume


def normalize_phone(value: str) -> str:
    """
    Normalize Indian mobile number to a 10-digit string.
    Strips leading +91 / 91 country code if present.
    Raises DRF ValidationError if the result is not a valid 10-digit mobile.
    """
    value = value.strip().replace(' ', '').replace('-', '')
    if value.startswith('+91'):
        value = value[3:]
    elif value.startswith('91') and len(value) == 12:
        value = value[2:]
    if not re.match(r'^[6-9]\d{9}$', value):
        raise drf_serializers.ValidationError(
            "Enter a valid 10-digit Indian mobile number."
        )
    return value


def normalize_skill_name(skill: str) -> str:
    return skill.strip().lower()


def compute_file_hash(f) -> str:
    """Compute SHA-256 hex digest of an uploaded file. Resets file pointer after reading."""
    h = hashlib.sha256()
    if hasattr(f, 'seek'):
        f.seek(0)
    content = f.read() if hasattr(f, 'read') else b''
    h.update(content)
    if hasattr(f, 'seek'):
        f.seek(0)
    return h.hexdigest()


def queue_resume_processing(resume: Resume) -> None:
    """Mark a resume as queued for text extraction and update its status."""
    Resume.objects.filter(pk=resume.pk).update(status='extracting')
    resume.status = 'extracting'


def mark_resume_manual_review(resume: Resume, reason: str) -> None:
    """Flag a resume for manual review with a reason string."""
    Resume.objects.filter(pk=resume.pk).update(
        status='manual_review',
        manual_review_reason=reason,
    )
    resume.status = 'manual_review'
    resume.manual_review_reason = reason


def mark_resume_failed(resume: Resume, error: str) -> None:
    """Mark a resume as failed and record the error message."""
    Resume.objects.filter(pk=resume.pk).update(
        status='failed',
        error_message=error,
    )
    resume.status = 'failed'
    resume.error_message = error


def build_candidate_profile_text(candidate) -> str:
    """
    Return a plain-text summary of a candidate's profile for search indexing
    or LLM context.  Pulls from latest indexed resume + skills + experience.
    """
    parts = [candidate.full_name]
    if candidate.current_role:
        parts.append(candidate.current_role)
    if candidate.current_company:
        parts.append(f"at {candidate.current_company}")
    if candidate.total_experience_years is not None:
        parts.append(f"{candidate.total_experience_years}y exp")

    skills = list(
        candidate.skills.values_list('skill_name', flat=True).order_by('skill_name')
    )
    if skills:
        parts.append("Skills: " + ", ".join(skills))

    for exp in candidate.experiences.order_by('-start_date')[:5]:
        line = f"{exp.job_title} @ {exp.company_name}"
        if exp.start_date:
            line += f" ({exp.start_date.year}"
            if exp.end_date:
                line += f"–{exp.end_date.year}"
            elif exp.is_current:
                line += "–present"
            line += ")"
        parts.append(line)

    return " | ".join(p for p in parts if p)


# Profile fields that can be updated on get_or_create (existing candidate).
# Only non-empty incoming values are applied.
_CANDIDATE_UPDATABLE_FIELDS = [
    'first_name', 'last_name', 'middle_name', 'email',
    'current_role', 'current_location', 'total_experience_years',
    'preferred_location', 'notice_period_days', 'current_company',
    'expected_ctc', 'current_ctc',
]


def manual_resume_intake(user, validated_data: dict) -> dict:
    """
    Create/update candidate, upload resume, tag skills, optionally create
    hiring application — all inside one atomic transaction.

    Returns dict: {candidate, resume, skills, hiring_application}
    """
    from rest_framework.exceptions import ValidationError

    from .models import Candidate, CandidateSkill

    with transaction.atomic():
        phone = validated_data['phone']
        phone_normalized = normalize_phone(phone)
        org = user.org

        # ── 1. Candidate get_or_create ─────────────────────────────────────
        defaults = {
            'phone': phone,
            'first_name': validated_data['first_name'],
            'last_name': validated_data['last_name'],
            'middle_name': validated_data.get('middle_name') or '',
            'email': validated_data.get('email') or '',
            'current_role': validated_data.get('current_role') or '',
            'current_location': validated_data.get('current_location') or '',
            'total_experience_years': validated_data.get('total_experience_years'),
            'preferred_location': validated_data.get('preferred_location') or '',
            'notice_period_days': validated_data.get('notice_period_days'),
            'current_company': validated_data.get('current_company') or '',
            'expected_ctc': validated_data.get('expected_ctc'),
            'current_ctc': validated_data.get('current_ctc'),
            'source': 'manual',
        }
        candidate, created = Candidate.objects.get_or_create(
            org=org,
            phone_normalized=phone_normalized,
            defaults=defaults,
        )

        # ── 2. Update existing candidate — never overwrite with blank ──────
        if not created:
            update_fields = []
            for field in _CANDIDATE_UPDATABLE_FIELDS:
                incoming = validated_data.get(field)
                if incoming is None or incoming == '':
                    continue
                if getattr(candidate, field) != incoming:
                    setattr(candidate, field, incoming)
                    update_fields.append(field)
            if update_fields:
                candidate.save(update_fields=update_fields)

        # ── 3. Resume ──────────────────────────────────────────────────────
        f = validated_data['resume_file']
        resume = Resume.objects.create(
            candidate=candidate,
            file=f,
            original_filename=getattr(f, 'name', ''),
            content_type=getattr(f, 'content_type', ''),
            size_bytes=getattr(f, 'size', 0),
            source_type='recruiter_upload',
            status='uploaded',
            uploaded_by=user,
            file_hash=compute_file_hash(f),
            view_only_note=validated_data.get('view_only_note') or '',
        )

        # ── 4. Skills — idempotent by normalized name ──────────────────────
        skills_out = []
        for skill_name in validated_data.get('skills') or []:
            normalized = normalize_skill_name(skill_name)
            skill, _ = CandidateSkill.objects.get_or_create(
                candidate=candidate,
                normalized_skill_name=normalized,
                defaults={
                    'skill_name': skill_name,
                    'source': 'manual',
                    'source_resume': resume,
                },
            )
            skills_out.append(skill)

        # ── 5. Optional hiring application ─────────────────────────────────
        hiring_app = None
        mrf_li = validated_data.get('mrf_line_item')
        mrf = validated_data.get('mrf')

        if mrf_li:
            from apps.hiring.models import (
                HiringApplication, ApplicationStageHistory, PipelineStage,
            )

            if candidate.org_id != mrf.org_id:
                raise ValidationError(
                    {'candidate': 'Candidate org does not match MRF org.'}
                )

            if HiringApplication.objects.filter(
                candidate=candidate, mrf_line_item=mrf_li,
            ).exists():
                raise ValidationError(
                    {'mrf_line_item': 'Candidate already has an application for this line item.'}
                )

            current_stage = validated_data.get('current_stage')
            if current_stage is None:
                current_stage = (
                    PipelineStage.objects
                    .filter(org=mrf.org, is_active=True)
                    .order_by('order')
                    .first()
                )

            hiring_app = HiringApplication.objects.create(
                org=mrf.org,
                candidate=candidate,
                mrf=mrf,
                mrf_line_item=mrf_li,
                site=mrf.site,
                job_role=mrf_li.job_role,
                current_stage=current_stage,
            )

            ApplicationStageHistory.objects.create(
                hiring_application=hiring_app,
                from_stage=None,
                to_stage=current_stage,
                from_status='',
                to_status=hiring_app.status,
                moved_by=user,
                comment='Application created via manual resume intake.',
            )

        return {
            'candidate': candidate,
            'resume': resume,
            'skills': skills_out,
            'hiring_application': hiring_app,
        }

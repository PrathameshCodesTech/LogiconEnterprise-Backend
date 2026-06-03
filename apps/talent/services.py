"""
apps/talent/services.py

Talent processing service helpers (Phase Talent-Hiring-A/B).
These are synchronous stubs — async/Celery/OCR/LLM dispatch is wired in a
later phase.  The status transitions are the authoritative state machine.
"""

import hashlib
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import ValidationError

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
    """
    Check for duplicate file, then schedule background parsing via Celery.
    Sets status='duplicate_file' and returns early if an indexed resume with
    the same file_hash already exists.
    """
    if resume.file_hash:
        duplicate = (
            Resume.objects
            .filter(file_hash=resume.file_hash, status='indexed')
            .exclude(pk=resume.pk)
            .first()
        )
        if duplicate:
            Resume.objects.filter(pk=resume.pk).update(status='duplicate_file')
            resume.status = 'duplicate_file'
            return

    Resume.objects.filter(pk=resume.pk).update(status='extracting')
    resume.status = 'extracting'

    from apps.talent.tasks import process_resume_task
    process_resume_task.delay(resume.pk)


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


# ─── Review services ──────────────────────────────────────────────────────────

def apply_review_service(resume, user, validated_data: dict):
    """
    Apply HR corrections to a resume: update candidate, replace parsed data,
    set status=indexed, create TalentResumeReview audit record.
    Returns the created TalentResumeReview.
    """
    from .models import (
        Candidate, CandidateSkill, CandidateExperience,
        CandidateEducation, ParsedResume, TalentResumeReview,
    )

    with transaction.atomic():
        candidate = resume.candidate
        previous_status = resume.status

        # 1. Update candidate fields (never overwrite with blank)
        candidate_data = validated_data.get('candidate') or {}
        if candidate_data:
            update_fields = []

            phone_val = (candidate_data.get('phone') or '').strip()
            if phone_val:
                phone_normalized = normalize_phone(phone_val)
                if candidate.phone != phone_val:
                    candidate.phone = phone_val
                    candidate.phone_normalized = phone_normalized
                    update_fields.extend(['phone', 'phone_normalized'])

            for field in [
                'first_name', 'middle_name', 'last_name', 'email',
                'current_role', 'current_company', 'current_location',
                'total_experience_years', 'expected_ctc', 'current_ctc',
                'notice_period_days',
            ]:
                val = candidate_data.get(field)
                if val is None:
                    continue
                if isinstance(val, str) and not val.strip():
                    continue
                if field == 'total_experience_years':
                    try:
                        val = Decimal(str(val))
                    except InvalidOperation:
                        continue
                if getattr(candidate, field, None) != val:
                    setattr(candidate, field, val)
                    update_fields.append(field)

            if update_fields:
                candidate.save(update_fields=list(dict.fromkeys(update_fields)))

        # 2. Replace parsed/reviewed skills — manual skills untouched
        skills_data = validated_data.get('skills')
        if skills_data is not None:
            CandidateSkill.objects.filter(
                candidate=candidate,
                source_resume=resume,
                source__in=['parsed', 'reviewed'],
            ).delete()
            for skill_d in skills_data:
                name = skill_d['skill_name'].strip()
                if not name:
                    continue
                CandidateSkill.objects.create(
                    candidate=candidate,
                    skill_name=name,
                    normalized_skill_name=name.lower(),
                    years_experience=skill_d.get('years_experience'),
                    proficiency=skill_d.get('proficiency') or '',
                    source='reviewed',
                    source_resume=resume,
                )

        # 3. Replace experience for this resume
        experience_data = validated_data.get('experience')
        if experience_data is not None:
            CandidateExperience.objects.filter(candidate=candidate, source_resume=resume).delete()
            for exp_d in experience_data:
                CandidateExperience.objects.create(
                    candidate=candidate,
                    source_resume=resume,
                    job_title=exp_d.get('job_title') or '',
                    company_name=exp_d.get('company_name') or '',
                    industry=exp_d.get('industry') or '',
                    start_date=exp_d.get('start_date'),
                    end_date=exp_d.get('end_date'),
                    is_current=bool(exp_d.get('is_current', False)),
                    duration_months=exp_d.get('duration_months'),
                    description=exp_d.get('description') or '',
                    responsibilities=exp_d.get('responsibilities') or [],
                )

        # 4. Replace education for this resume
        education_data = validated_data.get('education')
        if education_data is not None:
            CandidateEducation.objects.filter(candidate=candidate, source_resume=resume).delete()
            for edu_d in education_data:
                CandidateEducation.objects.create(
                    candidate=candidate,
                    source_resume=resume,
                    degree=edu_d.get('degree') or '',
                    specialization=edu_d.get('specialization') or '',
                    institute=edu_d.get('institute') or '',
                    start_year=edu_d.get('start_year'),
                    end_year=edu_d.get('end_year'),
                )

        # 5. Upsert ParsedResume — clear errors, mark confidence 1.0
        corrected_normalized = {}
        if candidate_data:
            corrected_normalized.update({
                k: v for k, v in candidate_data.items() if k != 'phone'
            })
        if skills_data is not None:
            corrected_normalized['skills'] = [
                {
                    'name': s['skill_name'],
                    'normalized_name': s['skill_name'].lower(),
                    'years_experience': str(s['years_experience']) if s.get('years_experience') is not None else None,
                    'proficiency': s.get('proficiency') or '',
                }
                for s in skills_data
            ]
        if experience_data is not None:
            corrected_normalized['experience'] = [
                {k: str(v) if hasattr(v, 'isoformat') else v for k, v in e.items()}
                for e in experience_data
            ]
        if education_data is not None:
            corrected_normalized['education'] = list(education_data)

        ParsedResume.objects.update_or_create(
            resume=resume,
            defaults={
                'normalized_json': corrected_normalized,
                'validation_errors': [],
                'missing_fields': [],
                'confidence': Decimal('1.00'),
            },
        )

        # 6. Update resume to indexed
        Resume.objects.filter(pk=resume.pk).update(
            status='indexed',
            manual_review_reason='',
            error_message='',
            parser_confidence=Decimal('1.00'),
        )
        resume.status = 'indexed'

        # 7. Create audit record
        review = TalentResumeReview.objects.create(
            org=candidate.org,
            resume=resume,
            candidate=candidate,
            reviewed_by=user,
            review_type='correction',
            previous_status=previous_status,
            new_status='indexed',
            review_note=validated_data.get('review_note', ''),
            correction_payload={
                k: v for k, v in validated_data.items() if k != 'review_note'
            },
        )

    return review


def resolve_duplicate_service(resume, user, validated_data: dict):
    """
    Resolve a duplicate candidate/resume situation.
    Returns the created TalentResumeReview.
    """
    from .models import Candidate, TalentResumeReview

    resolution = validated_data['resolution']
    existing_candidate = validated_data.get('candidate')
    note = validated_data.get('note', '')

    with transaction.atomic():
        candidate = resume.candidate
        previous_status = resume.status

        if resolution == 'link_existing':
            if existing_candidate is None:
                raise ValidationError({'candidate': 'candidate is required for link_existing resolution.'})
            if existing_candidate.org_id != candidate.org_id:
                raise ValidationError({'candidate': 'Target candidate belongs to a different organisation.'})
            resume.candidate = existing_candidate
            resume.save(update_fields=['candidate'])
            Resume.objects.filter(pk=resume.pk).update(
                status='manual_review',
                manual_review_reason='Linked to existing candidate after duplicate review.',
            )
            resume.status = 'manual_review'
            audit_candidate = existing_candidate

        elif resolution == 'mark_duplicate':
            update_c_fields = ['is_duplicate']
            candidate.is_duplicate = True
            if existing_candidate:
                if existing_candidate.org_id != candidate.org_id:
                    raise ValidationError({'candidate': 'Target candidate belongs to a different organisation.'})
                candidate.duplicate_of = existing_candidate
                update_c_fields.append('duplicate_of')
            candidate.save(update_fields=update_c_fields)
            Resume.objects.filter(pk=resume.pk).update(status='duplicate_file')
            resume.status = 'duplicate_file'
            audit_candidate = candidate

        elif resolution == 'keep_separate':
            candidate.is_duplicate = False
            candidate.duplicate_of = None
            candidate.save(update_fields=['is_duplicate', 'duplicate_of'])
            if resume.status == 'duplicate_file':
                Resume.objects.filter(pk=resume.pk).update(
                    status='manual_review',
                    manual_review_reason='Kept as separate candidate after duplicate review.',
                )
                resume.status = 'manual_review'
            audit_candidate = candidate

        else:
            raise ValidationError({'resolution': f'Unknown resolution: {resolution}'})

        review = TalentResumeReview.objects.create(
            org=audit_candidate.org,
            resume=resume,
            candidate=audit_candidate,
            reviewed_by=user,
            review_type='duplicate_resolution',
            previous_status=previous_status,
            new_status=resume.status,
            review_note=note,
            correction_payload={
                'resolution': resolution,
                'candidate': existing_candidate.pk if existing_candidate else None,
            },
        )

    return review

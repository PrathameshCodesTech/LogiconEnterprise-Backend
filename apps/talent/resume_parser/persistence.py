"""
apps/talent/resume_parser/persistence.py

Persist parsed resume data: update Candidate, upsert skills/experience/education,
upsert ParsedResume.  All writes are inside one atomic transaction.
"""

from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.db import transaction


def persist_parsed_data(
    resume,
    normalized_json: dict,
    parsed_json: dict,
    validation_errors: dict,
    missing_fields: list,
    confidence,
) -> None:
    with transaction.atomic():
        _update_candidate(resume.candidate, normalized_json)
        _replace_parsed_skills(resume, normalized_json)
        _replace_parsed_experience(resume, normalized_json)
        _replace_parsed_education(resume, normalized_json)
        _upsert_parsed_resume(
            resume, parsed_json, normalized_json,
            validation_errors, missing_fields, confidence,
        )


# ─── Candidate ────────────────────────────────────────────────────────────────

_FIELD_MAPPING = {
    'first_name': 'first_name',
    'last_name': 'last_name',
    'middle_name': 'middle_name',
    'email': 'email',
    'current_role': 'current_role',
    'current_location': 'current_location',
    'total_experience_years': 'total_experience_years',
    'current_company': 'current_company',
}
_GENERIC_NAMES = {'Unknown', 'unknown', ''}


def _update_candidate(candidate, normalized: dict) -> None:
    from apps.talent.services import _CANDIDATE_UPDATABLE_FIELDS as UPDATABLE

    update_fields = []
    for norm_key, model_field in _FIELD_MAPPING.items():
        if model_field not in UPDATABLE:
            continue
        incoming = normalized.get(norm_key)
        if incoming is None or incoming == '':
            continue
        current = getattr(candidate, model_field, None)
        # Never overwrite a meaningful value with a generic/blank one
        if current and current not in _GENERIC_NAMES:
            # Allow update only if incoming is genuinely different and non-blank
            if not incoming:
                continue
        if model_field == 'total_experience_years' and incoming is not None:
            try:
                incoming = Decimal(str(incoming))
            except InvalidOperation:
                continue
        if getattr(candidate, model_field, None) == incoming:
            continue
        setattr(candidate, model_field, incoming)
        update_fields.append(model_field)

    if update_fields:
        candidate.save(update_fields=update_fields)


# ─── Skills ───────────────────────────────────────────────────────────────────

def _replace_parsed_skills(resume, normalized: dict) -> None:
    from apps.talent.models import CandidateSkill

    CandidateSkill.objects.filter(source_resume=resume, source='parsed').delete()

    for skill in (normalized.get('skills') or []):
        name = (skill.get('name') or '').strip()
        if not name:
            continue
        norm_name = skill.get('normalized_name') or name.lower()
        years = skill.get('years_experience')
        proficiency = skill.get('proficiency') or ''
        if proficiency not in ('beginner', 'intermediate', 'advanced', 'expert'):
            proficiency = ''

        existing = CandidateSkill.objects.filter(
            candidate=resume.candidate,
            normalized_skill_name=norm_name,
        ).first()

        if existing:
            if existing.source == 'manual':
                continue
            fields_to_update = ['source', 'source_resume']
            existing.source = 'parsed'
            existing.source_resume = resume
            if years is not None:
                existing.years_experience = years
                fields_to_update.append('years_experience')
            if proficiency:
                existing.proficiency = proficiency
                fields_to_update.append('proficiency')
            existing.save(update_fields=fields_to_update)
        else:
            CandidateSkill.objects.create(
                candidate=resume.candidate,
                skill_name=name,
                normalized_skill_name=norm_name,
                source='parsed',
                source_resume=resume,
                years_experience=years,
                proficiency=proficiency,
            )


# ─── Experience ───────────────────────────────────────────────────────────────

def _replace_parsed_experience(resume, normalized: dict) -> None:
    from apps.talent.models import CandidateExperience

    CandidateExperience.objects.filter(source_resume=resume).delete()

    for exp in (normalized.get('experience') or []):
        job_title = (exp.get('job_title') or '').strip()
        company_name = (exp.get('company_name') or '').strip()
        if not job_title and not company_name:
            continue

        duration = exp.get('duration_months')
        if duration is not None:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = None

        responsibilities = exp.get('responsibilities') or []
        if not isinstance(responsibilities, list):
            responsibilities = []

        CandidateExperience.objects.create(
            candidate=resume.candidate,
            job_title=job_title,
            company_name=company_name,
            start_date=_parse_date(exp.get('start_date')),
            end_date=_parse_date(exp.get('end_date')),
            is_current=bool(exp.get('is_current', False)),
            duration_months=duration,
            responsibilities=[r for r in responsibilities if isinstance(r, str)],
            source_resume=resume,
        )


# ─── Education ────────────────────────────────────────────────────────────────

def _replace_parsed_education(resume, normalized: dict) -> None:
    from apps.talent.models import CandidateEducation

    CandidateEducation.objects.filter(source_resume=resume).delete()

    for edu in (normalized.get('education') or []):
        degree = (edu.get('degree') or '').strip()
        institute = (edu.get('institute') or '').strip()
        if not degree and not institute:
            continue

        CandidateEducation.objects.create(
            candidate=resume.candidate,
            degree=degree,
            specialization=(edu.get('specialization') or '').strip(),
            institute=institute,
            start_year=_safe_int(edu.get('start_year')),
            end_year=_safe_int(edu.get('end_year')),
            source_resume=resume,
        )


# ─── ParsedResume ─────────────────────────────────────────────────────────────

def _upsert_parsed_resume(
    resume, parsed_json, normalized_json,
    validation_errors, missing_fields, confidence,
) -> None:
    from apps.talent.models import ParsedResume

    conf_decimal = None
    if confidence is not None:
        try:
            conf_decimal = Decimal(str(confidence)).quantize(Decimal('0.01'))
            conf_decimal = max(Decimal('0.00'), min(Decimal('1.00'), conf_decimal))
        except InvalidOperation:
            pass

    ParsedResume.objects.update_or_create(
        resume=resume,
        defaults={
            'parsed_json': parsed_json,
            'normalized_json': normalized_json,
            'summary': (parsed_json.get('summary') or '')[:4096],
            'career_level': (parsed_json.get('career_level') or '')[:64],
            'primary_domain': (parsed_json.get('primary_domain') or '')[:128],
            'validation_errors': validation_errors,
            'missing_fields': missing_fields,
            'confidence': conf_decimal,
        },
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_date(value) -> date_type | None:
    import re
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r'^(\d{4})-(\d{2})$', s)
    if m:
        try:
            return date_type(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    m = re.match(r'^(\d{4})$', s)
    if m:
        try:
            return date_type(int(m.group(1)), 1, 1)
        except ValueError:
            return None
    return None


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

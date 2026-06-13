"""
apps/hiring/matching/services.py

Candidate matching orchestration for an MRF line item.
No AI calls. Uses only persisted Candidate/Skill/Experience data.
"""

from decimal import Decimal

from .scoring import (
    score_role, score_skills, score_experience,
    score_location, score_availability, score_data_quality,
    compute_total_score,
)
from .explain import match_status_label, build_reasons, build_warnings


# ─── Public API ───────────────────────────────────────────────────────────────

def get_required_skills(demand, params: dict) -> list:
    """
    Derive required skill names (lowercased) for a demand.

    Priority:
    1. Query param  `skills`  (comma-separated) or `skill` (single)
    2. MRFLineItem.required_skills  (list of strings)
    3. Empty list (no requirement — neutral scoring)
    """
    raw = (params.get('skills') or params.get('skill') or '').strip()
    if raw:
        return [s.strip().lower() for s in raw.split(',') if s.strip()]

    req = demand.required_skills if hasattr(demand, 'required_skills') else []
    if req and isinstance(req, list):
        return [str(s).strip().lower() for s in req if str(s).strip()]

    return []


def get_location_hint(demand, params: dict) -> str:
    """
    Return a lowercase location string to match against candidates.
    Priority: query param → site city → site location_area name.
    """
    explicit = (params.get('location') or '').strip()
    if explicit:
        return explicit.lower()

    try:
        site = demand.mrf.site
        if site and site.city:
            return site.city.strip().lower()
        if site and site.location_area:
            name = site.location_area.name
            if name:
                return name.strip().lower()
    except Exception:
        pass

    return ''


def rank_candidates(
    demand,
    candidates_qs,
    params: dict,
    save_results: bool = True,
    user=None,
) -> list:
    """
    Score and rank all candidates in `candidates_qs` for `demand`.

    Returns a list of result dicts, sorted by score descending:
      {candidate, score, match_status, score_breakdown,
       matched_skills, missing_skills, extra_candidate_skills, reasons, warnings}

    If save_results=True, upserts CandidateMatchResult rows and attaches
    match_result ids to the returned rows.
    """
    from apps.talent.models import CandidateSkill, CandidateExperience

    # Prefetch skills and experiences in one round-trip each
    candidates = list(
        candidates_qs.prefetch_related('skills', 'experiences')
    )
    if not candidates:
        return []

    required_skills = get_required_skills(demand, params)
    location_hint = get_location_hint(demand, params)
    min_exp = _safe_decimal(params.get('min_experience', ''))
    max_exp = _safe_decimal(params.get('max_experience', ''))
    job_role = demand.job_role

    results = []
    for candidate in candidates:
        skills_list = list(candidate.skills.all())
        exp_list = list(candidate.experiences.all())

        candidate_skill_names = {
            (s.normalized_skill_name or s.skill_name or '').strip().lower()
            for s in skills_list
            if (s.normalized_skill_name or s.skill_name or '').strip()
        }

        role = score_role(candidate, job_role, exp_list)
        skill_sc, matched, missing, extra = score_skills(
            candidate_skill_names, required_skills,
        )
        experience = score_experience(candidate, exp_list, min_exp, max_exp)
        location = score_location(candidate, location_hint)
        availability = score_availability(candidate)
        quality = score_data_quality(candidate, len(candidate_skill_names), len(exp_list))

        total = compute_total_score(role, skill_sc, experience, location, availability, quality)

        reasons = build_reasons(candidate, job_role, matched, missing, min_exp, experience, role)
        warnings = build_warnings(candidate)

        results.append({
            'candidate': candidate,
            'score': total,
            'match_status': match_status_label(total),
            'score_breakdown': {
                'role': round(role, 2),
                'skills': round(skill_sc, 2),
                'experience': round(experience, 2),
                'location': round(location, 2),
                'availability': round(availability, 2),
                'data_quality': round(quality, 2),
            },
            'matched_skills': matched,
            'missing_skills': missing,
            'extra_candidate_skills': extra,
            'reasons': reasons,
            'warnings': warnings,
        })

    results.sort(key=lambda r: r['score'], reverse=True)

    if save_results and results:
        saved_ids = _save_match_results(demand, results, user)
        for result in results:
            result['match_result'] = saved_ids.get(result['candidate'].pk)

    return results


def match_result_snapshot(match_result) -> dict:
    """Return the auditable scorecard payload used when shortlisting."""
    if match_result is None:
        return {}
    score = match_result.final_score
    if score is None:
        score = match_result.match_score
    breakdown = {
        'role': match_result.role_score,
        'skills': match_result.skill_score,
        'experience': match_result.experience_score,
        'location': match_result.location_score,
        'availability': match_result.availability_score,
        'industry': match_result.industry_score,
        'education': match_result.education_score,
        'salary': match_result.salary_score,
        'semantic': match_result.semantic_score,
    }
    return {
        'match_result': match_result.pk,
        'score': float(score) if score is not None else None,
        'match_source': match_result.match_source,
        'score_breakdown': {
            key: float(value) for key, value in breakdown.items()
            if value is not None
        },
        'matched_skills': match_result.matched_skills or [],
        'missing_skills': match_result.missing_skills or [],
        'reasons': match_result.match_reason or [],
        'warnings': match_result.warnings or [],
        'details': match_result.match_details or {},
    }


# ─── Internals ────────────────────────────────────────────────────────────────

def _safe_decimal(value):
    if not value:
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def _save_match_results(demand, results, user):
    from apps.hiring.models import CandidateMatchResult

    org = demand.mrf.org
    saved_ids = {}

    for result in results:
        candidate = result['candidate']
        bd = result['score_breakdown']

        defaults = {
            'org': org,
            'final_score': Decimal(str(result['score'])),
            'match_score': Decimal(str(result['score'])),
            'role_score': Decimal(str(bd['role'])),
            'skill_score': Decimal(str(bd['skills'])),
            'experience_score': Decimal(str(bd['experience'])),
            'location_score': Decimal(str(bd['location'])),
            'availability_score': Decimal(str(bd['availability'])),
            'matched_skills': result['matched_skills'],
            'missing_skills': result['missing_skills'],
            'match_reason': result['reasons'],
            'warnings': result['warnings'],
            'match_details': bd,
            'match_source': 'rules',
            'is_auto_match': True,
            'created_by': user,
        }

        existing = (
            CandidateMatchResult.objects
            .filter(candidate=candidate, mrf_line_item=demand)
            .order_by('-created_at')
            .first()
        )
        if existing:
            for k, v in defaults.items():
                setattr(existing, k, v)
            existing.save(update_fields=list(defaults.keys()))
            saved_ids[candidate.pk] = existing.pk
        else:
            created = CandidateMatchResult.objects.create(
                candidate=candidate,
                mrf_line_item=demand,
                **defaults,
            )
            saved_ids[candidate.pk] = created.pk

    return saved_ids

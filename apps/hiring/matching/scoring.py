"""
apps/hiring/matching/scoring.py

Deterministic 100-point scoring for candidate ↔ MRF line item matching.
No AI calls. Uses only persisted Candidate / Skill / Experience data.

Weights:
  role_score        25 %
  skill_score       30 %
  experience_score  20 %
  location_score    10 %
  availability_score 10 %
  data_quality_score  5 %
"""

from decimal import Decimal, InvalidOperation


# ─── Role ─────────────────────────────────────────────────────────────────────

def score_role(candidate, job_role, experiences) -> float:
    """
    100 if candidate.current_role contains job_role.name (case-insensitive).
    70  if any experience job_title matches.
    30  no match (baseline — role signal may simply be absent).
    50  if job_role is None (neutral).
    """
    if job_role is None:
        return 50.0

    role_name = job_role.name.strip().lower()
    if not role_name:
        return 50.0

    current = (candidate.current_role or '').lower()
    if role_name in current or current in role_name:
        return 100.0

    for exp in experiences:
        title = (exp.job_title or '').lower()
        norm_title = (exp.normalized_title or '').lower()
        if role_name in title or role_name in norm_title:
            return 70.0

    return 30.0


# ─── Skills ───────────────────────────────────────────────────────────────────

def score_skills(
    candidate_normalized_skill_names: set,
    required_skills: list,
) -> tuple:
    """
    Returns (score, matched_skills, missing_skills, extra_skills).

    If no required skills → neutral score 70, all matched empty.
    Score = (matched / total_required) * 100.
    """
    if not required_skills:
        return 70.0, [], [], list(candidate_normalized_skill_names)

    required_set = set(s.lower() for s in required_skills)
    matched = [s for s in required_set if s in candidate_normalized_skill_names]
    missing = [s for s in required_set if s not in candidate_normalized_skill_names]
    extra = list(candidate_normalized_skill_names - required_set)

    score = (len(matched) / len(required_set)) * 100.0
    return round(score, 2), sorted(matched), sorted(missing), sorted(extra)


# ─── Experience ───────────────────────────────────────────────────────────────

def score_experience(candidate, experiences, min_exp=None, max_exp=None) -> float:
    """
    Score 0-100 based on years of experience.

    If min_exp specified:
      exp >= min_exp → 100
      exp < min_exp  → proportional, floor 10

    If no requirement:
      None  → 30
      0-5   → linear 30-100
      5+    → 100
    """
    exp_years = _get_experience_years(candidate, experiences)

    if exp_years is None:
        return 30.0

    exp_f = float(exp_years)

    if min_exp is not None:
        min_f = float(min_exp)
        if min_f <= 0:
            return 100.0
        if exp_f >= min_f:
            score = 100.0
        else:
            score = max(10.0, (exp_f / min_f) * 100.0)
        if max_exp is not None and exp_f > float(max_exp):
            score = min(score, 80.0)
        return round(score, 2)

    # No requirement — linear 30-100 over 0-5 years
    score = min(100.0, 30.0 + (exp_f / 5.0) * 70.0)
    return round(max(30.0, score), 2)


def _get_experience_years(candidate, experiences):
    """Return Decimal or float experience years; None if completely unknown."""
    if candidate.total_experience_years is not None:
        try:
            return float(candidate.total_experience_years)
        except (TypeError, ValueError, InvalidOperation):
            pass

    # Compute from experience records
    total_months = 0
    for exp in experiences:
        if exp.duration_months:
            try:
                total_months += int(exp.duration_months)
            except (TypeError, ValueError):
                pass
    if total_months:
        return total_months / 12.0
    return None


# ─── Location ─────────────────────────────────────────────────────────────────

def score_location(candidate, location_hint: str) -> float:
    """
    100 exact match, 70 contains match, 50 no hint, 30 no match.
    Checks both current_location and preferred_location.
    """
    if not location_hint:
        return 50.0

    hint = location_hint.strip().lower()
    candidate_locations = [
        (candidate.current_location or '').strip().lower(),
        (candidate.preferred_location or '').strip().lower(),
    ]

    best = 30.0
    for loc in candidate_locations:
        if not loc:
            continue
        if loc == hint or hint == loc:
            return 100.0
        if hint in loc or loc in hint:
            best = max(best, 70.0)

    return best


# ─── Availability ─────────────────────────────────────────────────────────────

def score_availability(candidate) -> float:
    """Score based on availability_status and notice_period_days."""
    status = (candidate.availability_status or 'unknown').lower()
    notice = candidate.notice_period_days

    if status == 'available_now':
        return 100.0
    if status == 'available_from_date':
        return 70.0
    if status == 'notice_period':
        if notice is None:
            return 50.0
        if notice <= 30:
            return 80.0
        if notice <= 60:
            return 60.0
        if notice <= 90:
            return 40.0
        return 20.0
    if status == 'currently_deployed':
        return 30.0
    if status == 'not_available':
        return 0.0
    return 40.0  # unknown


# ─── Data quality ─────────────────────────────────────────────────────────────

def score_data_quality(candidate, skill_count: int, experience_count: int) -> float:
    """
    Bonus for having complete profile data.
    Has email         → 20
    Has experience yrs → 30
    Has ≥1 skill      → 30
    Has ≥1 exp record  → 20
    """
    pts = 0
    if candidate.email:
        pts += 20
    if candidate.total_experience_years is not None:
        pts += 30
    if skill_count >= 1:
        pts += 30
    if experience_count >= 1:
        pts += 20
    return float(pts)


# ─── Composite ────────────────────────────────────────────────────────────────

def compute_total_score(
    role: float,
    skills: float,
    experience: float,
    location: float,
    availability: float,
    quality: float,
) -> float:
    total = (
        role * 0.25 +
        skills * 0.30 +
        experience * 0.20 +
        location * 0.10 +
        availability * 0.10 +
        quality * 0.05
    )
    return round(total, 2)

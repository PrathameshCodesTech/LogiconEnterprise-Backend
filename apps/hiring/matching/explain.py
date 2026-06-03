"""
apps/hiring/matching/explain.py

Human-readable reasons and warnings for a candidate match result.
"""


def match_status_label(score: float) -> str:
    if score >= 80:
        return "Strong Match"
    if score >= 60:
        return "Good Match"
    if score >= 40:
        return "Possible Match"
    return "Weak Match"


def build_reasons(
    candidate,
    job_role,
    matched_skills: list,
    missing_skills: list,
    min_exp,
    experience_score: float,
    role_score: float,
) -> list:
    reasons = []

    if job_role:
        role_name = job_role.name
        if role_score >= 90:
            reasons.append(f"Role matches {role_name}.")
        elif role_score >= 60:
            reasons.append(f"Past experience matches {role_name}.")
        else:
            current = candidate.current_role or 'not specified'
            reasons.append(f"Current role ({current}) does not match {role_name}.")

    total_req = len(matched_skills) + len(missing_skills)
    if total_req > 0:
        reasons.append(
            f"Matched {len(matched_skills)} of {total_req} required skill(s): "
            f"{', '.join(matched_skills) or 'none'}."
        )
    elif matched_skills:
        reasons.append(f"Has skills: {', '.join(matched_skills[:5])}.")

    exp = candidate.total_experience_years
    if exp is not None:
        reasons.append(f"Candidate has {exp} years of experience.")
        if min_exp is not None and float(exp) < float(min_exp):
            reasons.append(
                f"Below minimum experience requirement of {min_exp} years."
            )
    else:
        reasons.append("Experience data not available.")

    return reasons


def build_warnings(candidate) -> list:
    warnings = []
    if not candidate.email:
        warnings.append("No email address on file.")
    if candidate.total_experience_years is None:
        warnings.append("Experience data missing — score may be lower.")
    if candidate.notice_period_days and candidate.notice_period_days > 60:
        warnings.append(f"Long notice period: {candidate.notice_period_days} days.")
    if candidate.is_duplicate:
        warnings.append("Candidate is flagged as a possible duplicate.")
    return warnings

"""
apps/talent/resume_parser/normalization.py

Normalize parsed resume JSON: phone, skills, name split.
"""

import re


def normalize_parsed_json(parsed_json: dict) -> dict:
    """
    Return a new dict with normalized values suitable for persistence.
    Adds: first_name, middle_name, last_name, phone_normalized, normalized skill names.
    """
    n = dict(parsed_json)

    # Name splitting
    full_name = (n.get('full_name') or '').strip()
    first, middle, last = _split_name(full_name)
    n['first_name'] = first
    n['middle_name'] = middle
    n['last_name'] = last

    # Phone normalization
    phone = (n.get('phone') or '').strip()
    n['phone_normalized'] = _safe_normalize_phone(phone) if phone else ''

    # Skills — add normalized_name, filter out nameless entries
    raw_skills = n.get('skills') or []
    if not isinstance(raw_skills, list):
        raw_skills = []
    clean_skills = []
    for s in raw_skills:
        if not isinstance(s, dict):
            continue
        name = (s.get('name') or '').strip()
        if not name:
            continue
        clean_skills.append({
            **s,
            'name': name,
            'normalized_name': name.lower(),
        })
    n['skills'] = clean_skills

    # total_experience_years → float
    exp = n.get('total_experience_years')
    if exp is not None:
        try:
            n['total_experience_years'] = float(exp)
        except (TypeError, ValueError):
            n['total_experience_years'] = None

    return n


def _split_name(full_name: str) -> tuple:
    """Split into (first, middle, last) conservatively."""
    parts = full_name.split()
    if not parts:
        return '', '', ''
    if len(parts) == 1:
        return parts[0], '', ''
    if len(parts) == 2:
        return parts[0], '', parts[1]
    return parts[0], ' '.join(parts[1:-1]), parts[-1]


def _safe_normalize_phone(phone: str) -> str:
    """
    Try apps.talent.services.normalize_phone; fall back to digits-only.
    Returns empty string if result is invalid.
    """
    try:
        from apps.talent.services import normalize_phone
        return normalize_phone(phone)
    except Exception:
        digits = re.sub(r'\D', '', phone)
        return digits if len(digits) == 10 else ''

"""
apps/talent/resume_parser/validation.py

Validate parsed resume JSON for required identifiers and field types.
"""

import re


def validate_parsed_json(parsed_json: dict) -> tuple:
    """
    Validate parsed resume data.
    Returns (errors_dict, missing_fields_list).

    errors_dict keys:
      'no_identifier'  — critical: no name, email, or phone found (pipeline stops)
      'email'          — invalid email format (non-fatal)
      'skills'         — not a list (auto-corrected)
      'experience'     — not a list (auto-corrected)
      'education'      — not a list (auto-corrected)
    """
    errors = {}
    missing = []

    full_name = (parsed_json.get('full_name') or '').strip()
    email = (parsed_json.get('email') or '').strip()
    phone = (parsed_json.get('phone') or '').strip()

    if not full_name:
        missing.append('full_name')
    if not email:
        missing.append('email')
    if not phone:
        missing.append('phone')

    if not full_name and not email and not phone:
        errors['no_identifier'] = (
            'No name, email, or phone number found in resume; cannot identify candidate.'
        )

    if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        errors['email'] = f"Invalid email format: {email!r}"

    for list_field in ('skills', 'experience', 'education'):
        val = parsed_json.get(list_field)
        if val is not None and not isinstance(val, list):
            errors[list_field] = f"'{list_field}' must be a list; got {type(val).__name__}"
            parsed_json[list_field] = []

    return errors, missing

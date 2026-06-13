"""
Deterministic resume parser.

This intentionally avoids AI calls. It extracts conservative identifiers and
workforce-role signals so HR can tag/search resumes without relying on model
availability or hallucinated profile data.
"""

import re
from decimal import Decimal, InvalidOperation


EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
PHONE_RE = re.compile(r'(?:\+?91[\s-]?)?[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{4}')
EXP_RE = re.compile(
    r'(?P<num>\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)',
    re.IGNORECASE,
)
YEAR_RE = re.compile(r'(19|20)\d{2}')
MONTH_YEAR_RE = re.compile(
    r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(19|20)\d{2}\b',
    re.IGNORECASE,
)

EXPERIENCE_HEADINGS = {
    'experience', 'work experience', 'employment history', 'work history',
    'professional experience', 'career history',
}
EDUCATION_HEADINGS = {'education', 'qualification', 'qualifications', 'academic'}
SECTION_STOP_HEADINGS = {
    'skills', 'technical skills', 'personal details', 'certifications',
    'certificate', 'summary', 'profile', 'objective', 'declaration',
}

EDUCATION_KEYWORDS = [
    'ssc', 'hsc', 'iti', 'diploma', 'bachelor', 'graduate', 'graduation',
    'b.tech', 'b.e.', 'bsc', 'b.sc', 'ba', 'b.a.', 'm.tech', 'm.e.',
    'msc', 'm.sc', 'mba', 'degree',
]

ROLE_KEYWORDS = {
    'electrician': ['electrician', 'electrical', 'wiring', 'panel', 'lt panel', 'ht panel'],
    'plumber': ['plumber', 'plumbing', 'pipe fitting', 'pipeline'],
    'mst': ['mst', 'multi skilled technician', 'multi-skilled technician'],
    'hvac': ['hvac', 'air conditioning', 'chiller', 'ahu', 'vrf'],
    'carpenter': ['carpenter', 'carpentry', 'woodwork'],
    'painter': ['painter', 'painting', 'paint work'],
    'mason': ['mason', 'masonry', 'civil work', 'brick work'],
    'helper': ['helper', 'assistant helper', 'general helper'],
    'htp operator': ['htp operator', 'htp', 'heat treatment plant'],
    'wtp operator': ['wtp operator', 'wtp', 'water treatment plant'],
    'stp operator': ['stp operator', 'stp', 'sewage treatment plant'],
    'supervisor': ['supervisor', 'site supervisor', 'technical supervisor', 'team lead'],
    'housekeeping': ['housekeeping', 'hk', 'janitor', 'cleaner', 'sweeper'],
    'security': ['security', 'guard', 'watchman', 'patrolling'],
}

LOCATION_KEYWORDS = [
    'Mumbai', 'Pune', 'Navi Mumbai', 'Thane', 'Chakan', 'Nagpur',
    'Nashik', 'Bengaluru', 'Hyderabad', 'Delhi', 'Chennai',
]


def parse_resume_text(text: str) -> dict:
    """Return parser JSON compatible with normalization/persistence."""
    cleaned = _collapse(text)
    email = _first_match(EMAIL_RE, cleaned)
    phone = _first_match(PHONE_RE, cleaned)
    full_name = _guess_name(cleaned)
    role_name = _detect_role(cleaned)
    skills = _detect_skills(cleaned)
    exp_years = _detect_experience(cleaned)
    location = _detect_location(cleaned)
    experience = _detect_experience_entries(cleaned, role_name, exp_years)
    education = _detect_education_entries(cleaned)
    current_company = experience[0].get('company_name', '') if experience else ''

    confidence = _confidence(
        has_name=bool(full_name),
        has_email=bool(email),
        has_phone=bool(phone),
        skill_count=len(skills),
        has_experience=exp_years is not None,
    )

    return {
        'full_name': full_name,
        'email': email,
        'phone': phone,
        'summary': _summary(full_name, role_name, exp_years, location),
        'career_level': _career_level(exp_years),
        'primary_domain': role_name,
        'total_experience_years': exp_years,
        'current_company': current_company,
        'current_role': role_name,
        'current_location': location,
        'skills': skills,
        'experience': experience,
        'education': education,
        'confidence': confidence,
    }


def _collapse(text: str) -> str:
    return re.sub(r'[ \t]+', ' ', (text or '').replace('\r', '\n')).strip()


def _first_match(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(0).strip() if match else ''


def _guess_name(text: str) -> str:
    for raw_line in text.splitlines()[:12]:
        line = raw_line.strip()
        if not line or len(line) > 80:
            continue
        lower = line.lower()
        if EMAIL_RE.search(line) or PHONE_RE.search(line):
            continue
        if any(token in lower for token in ('resume', 'curriculum', 'cv', 'profile')):
            continue
        if re.search(r'\d', line):
            continue
        words = [w for w in re.split(r'\s+', line) if w]
        if 1 <= len(words) <= 5 and all(re.match(r"^[A-Za-z.'-]+$", w) for w in words):
            return ' '.join(w.capitalize() for w in words)
    return ''


def _detect_role(text: str) -> str:
    lower = text.lower()
    best_key = ''
    best_hits = 0
    for role, keywords in ROLE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > best_hits:
            best_key = role
            best_hits = hits
    return best_key.title() if best_key else ''


def _detect_skills(text: str) -> list:
    lower = text.lower()
    names = []
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            names.append(role.title())
    if 'safety' in lower:
        names.append('Safety Compliance')
    if 'maintenance' in lower:
        names.append('Maintenance')
    return [{'name': name, 'proficiency': '', 'years_experience': None} for name in sorted(set(names))]


def _detect_experience(text: str):
    values = []
    for match in EXP_RE.finditer(text):
        try:
            values.append(Decimal(match.group('num')))
        except (InvalidOperation, TypeError):
            pass
    if not values:
        return None
    return float(max(values))


def _detect_experience_entries(text: str, role_name: str, exp_years) -> list:
    lines = _section_lines(text, EXPERIENCE_HEADINGS)
    if not lines:
        lines = [
            line.strip() for line in text.splitlines()
            if _looks_like_experience_line(line)
        ]

    entries = []
    for line in lines[:12]:
        entry = _parse_experience_line(line, role_name)
        if entry:
            entries.append(entry)

    if entries:
        return _dedupe_entries(entries, ('job_title', 'company_name', 'start_date'))

    # Fallback: create one coarse entry only when the resume clearly gives role
    # and total experience. This avoids inventing employer history.
    if role_name and exp_years is not None:
        return [{
            'job_title': role_name,
            'company_name': '',
            'start_date': None,
            'end_date': None,
            'is_current': False,
            'duration_months': int(float(exp_years) * 12),
            'responsibilities': [],
        }]
    return []


def _parse_experience_line(line: str, role_name: str) -> dict | None:
    cleaned = line.strip(' -|\t')
    if len(cleaned) < 6:
        return None

    lower = cleaned.lower()
    if any(keyword in lower for keyword in EDUCATION_KEYWORDS):
        return None

    start_date, end_date, is_current = _dates_from_line(cleaned)
    without_dates = re.sub(
        r'\(?\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(?:19|20)\d{2}\b\s*(?:-|to|–|—)?\s*(?:present|current|till date|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(?:19|20)\d{2}|(?:19|20)\d{2})?\)?',
        '',
        cleaned,
        flags=re.IGNORECASE,
    )
    without_dates = re.sub(
        r'\(?\b(?:19|20)\d{2}\b\s*(?:-|to|–|—)?\s*(?:present|current|till date|(?:19|20)\d{2})?\)?',
        '',
        without_dates,
        flags=re.IGNORECASE,
    ).strip(' -|,')

    job_title = ''
    company_name = ''
    at_match = re.search(r'(.+?)\s+(?:at|@)\s+(.+)', without_dates, re.IGNORECASE)
    if at_match:
        job_title = at_match.group(1).strip(' -|,')
        company_name = at_match.group(2).strip(' -|,')
    else:
        parts = [p.strip() for p in re.split(r'\s+[-|–—]\s+', without_dates) if p.strip()]
        if len(parts) >= 2:
            role_idx = _role_part_index(parts, role_name)
            if role_idx is None:
                role_idx = 0
            job_title = parts[role_idx]
            company_name = parts[1 - role_idx] if len(parts) == 2 else ' - '.join(
                p for i, p in enumerate(parts) if i != role_idx
            )
        elif role_name and role_name.lower() in lower:
            job_title = role_name
            company_name = ''

    if not job_title and not company_name:
        return None

    return {
        'job_title': _clean_label(job_title)[:255],
        'company_name': _clean_label(company_name)[:255],
        'start_date': start_date,
        'end_date': None if is_current else end_date,
        'is_current': is_current,
        'duration_months': _duration_months(start_date, end_date, is_current),
        'responsibilities': [],
    }


def _detect_education_entries(text: str) -> list:
    lines = _section_lines(text, EDUCATION_HEADINGS)
    if not lines:
        lines = [
            line.strip() for line in text.splitlines()
            if any(keyword in line.lower() for keyword in EDUCATION_KEYWORDS)
        ]

    entries = []
    for line in lines[:12]:
        entry = _parse_education_line(line)
        if entry:
            entries.append(entry)
    return _dedupe_entries(entries, ('degree', 'institute', 'end_year'))


def _parse_education_line(line: str) -> dict | None:
    cleaned = line.strip(' -|\t')
    lower = cleaned.lower()
    if not any(keyword in lower for keyword in EDUCATION_KEYWORDS):
        return None

    years = [int(match.group(0)) for match in YEAR_RE.finditer(cleaned)]
    degree = _detect_degree(cleaned)
    yearless = YEAR_RE.sub('', cleaned).strip(' -|,')
    institute = ''
    specialization = ''

    if degree:
        remainder = re.sub(re.escape(degree), '', yearless, flags=re.IGNORECASE).strip(' -|,')
        institute = re.sub(r'\b(?:from|at|in)\b', '', remainder, flags=re.IGNORECASE).strip(' -|,')
    else:
        degree = yearless

    return {
        'degree': _clean_label(degree)[:255],
        'specialization': _clean_label(specialization)[:255],
        'institute': _clean_label(institute)[:255],
        'start_year': min(years) if len(years) > 1 else None,
        'end_year': max(years) if years else None,
    }


def _section_lines(text: str, headings: set[str]) -> list:
    lines = [line.strip() for line in text.splitlines()]
    capture = False
    output = []
    stop_headings = SECTION_STOP_HEADINGS | EXPERIENCE_HEADINGS | EDUCATION_HEADINGS
    for line in lines:
        if not line:
            continue
        normalized = re.sub(r'[^a-z ]+', '', line.lower()).strip()
        if normalized in headings:
            capture = True
            continue
        if capture and normalized in stop_headings:
            break
        if capture:
            output.append(line)
    return output


def _looks_like_experience_line(line: str) -> bool:
    lower = line.lower()
    return bool(
        (' at ' in lower or ' @ ' in lower or re.search(r'\s[-|–—]\s', line))
        and (YEAR_RE.search(line) or MONTH_YEAR_RE.search(line))
    )


def _dates_from_line(line: str) -> tuple:
    lower = line.lower()
    is_current = any(token in lower for token in ('present', 'current', 'till date'))
    date_tokens = []
    for match in MONTH_YEAR_RE.finditer(line):
        date_tokens.append(_month_year_to_iso(match.group(0)))
    if not date_tokens:
        date_tokens = [match.group(0) for match in YEAR_RE.finditer(line)]
    start_date = date_tokens[0] if date_tokens else None
    end_date = date_tokens[1] if len(date_tokens) > 1 else None
    return start_date, end_date, is_current


def _month_year_to_iso(value: str) -> str:
    months = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
        'sep': '09', 'sept': '09', 'oct': '10', 'nov': '11', 'dec': '12',
    }
    parts = value.lower().split()
    month = months.get(parts[0][:4], months.get(parts[0][:3], '01'))
    year = parts[-1]
    return f'{year}-{month}'


def _duration_months(start_date, end_date, is_current: bool):
    if not start_date:
        return None
    try:
        start_year = int(str(start_date)[:4])
        end_year = int(str(end_date)[:4]) if end_date else None
        if is_current or end_year is None:
            return None
        return max(0, (end_year - start_year) * 12)
    except (TypeError, ValueError):
        return None


def _role_part_index(parts: list[str], role_name: str):
    if not role_name:
        return None
    role_lower = role_name.lower()
    for index, part in enumerate(parts):
        if role_lower in part.lower():
            return index
    for index, part in enumerate(parts):
        if _detect_role(part).lower() == role_lower:
            return index
    return None


def _detect_degree(line: str) -> str:
    lower = line.lower()
    ordered = [
        'B.Tech', 'B.E.', 'M.Tech', 'M.E.', 'B.Sc', 'M.Sc',
        'MBA', 'Diploma', 'ITI', 'HSC', 'SSC', 'Bachelor', 'Graduate',
    ]
    for degree in ordered:
        if degree.lower() in lower:
            return degree
    return ''


def _clean_label(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').replace(':', ' ')).strip(' -|,')


def _dedupe_entries(entries: list[dict], keys: tuple[str, ...]) -> list:
    seen = set()
    output = []
    for entry in entries:
        marker = tuple((entry.get(key) or '') for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(entry)
    return output


def _detect_location(text: str) -> str:
    lower = text.lower()
    for location in LOCATION_KEYWORDS:
        if location.lower() in lower:
            return location
    return ''


def _career_level(exp_years) -> str:
    if exp_years is None:
        return ''
    if exp_years < 2:
        return 'junior'
    if exp_years < 7:
        return 'mid'
    return 'senior'


def _summary(name: str, role: str, exp_years, location: str) -> str:
    parts = []
    if name:
        parts.append(name)
    if role:
        parts.append(role)
    if exp_years is not None:
        parts.append(f'{exp_years:g} years experience')
    if location:
        parts.append(location)
    return ' | '.join(parts)


def _confidence(*, has_name: bool, has_email: bool, has_phone: bool, skill_count: int, has_experience: bool) -> float:
    score = 0.2
    if has_name:
        score += 0.2
    if has_email:
        score += 0.15
    if has_phone:
        score += 0.25
    if skill_count:
        score += min(0.15, skill_count * 0.03)
    if has_experience:
        score += 0.05
    return round(min(score, 0.95), 2)

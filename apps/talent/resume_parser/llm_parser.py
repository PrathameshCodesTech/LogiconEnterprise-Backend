"""
apps/talent/resume_parser/llm_parser.py

Parse resume text into structured JSON using OpenAI.
Raises ManualReviewRequired when no API key is configured or the API call fails.
"""

import json
import os

from .exceptions import ManualReviewRequired

_OPENAI_MODEL = 'gpt-4o-mini'
_MAX_INPUT_CHARS = 15_000

_SYSTEM_PROMPT = """\
You are a professional resume parser for a security-staffing company.
Extract structured candidate information from the resume text and return ONLY a valid JSON object.

Required fields:
  full_name        (string) — candidate's full name; use empty string if not found
  email            (string or null)
  phone            (string or null) — as printed, including country code if present
  summary          (string or null) — professional summary in 2-3 sentences
  career_level     (string) — one of: junior, mid, senior, lead, executive, unknown
  primary_domain   (string or null) — primary industry/domain (e.g. "Security", "IT")
  total_experience_years (number or null)
  current_company  (string or null)
  current_role     (string or null)
  current_location (string or null)
  skills           (array of {name, years_experience, proficiency})
    proficiency: one of beginner, intermediate, advanced, expert — or omit
  experience       (array of {job_title, company_name, start_date, end_date, is_current,
                              duration_months, responsibilities})
    dates in YYYY-MM or YYYY format; responsibilities as array of strings
  education        (array of {degree, specialization, institute, start_year, end_year})
  confidence       (number 0.0–1.0) — your overall extraction confidence

Return only the JSON object with no additional text."""


def parse_resume_text(text: str) -> dict:
    """
    Parse resume text using OpenAI chat completions.
    Raises ManualReviewRequired if:
      - OPENAI_API_KEY is not set
      - the openai package is not installed
      - the API call fails for any reason
      - the response is not valid JSON
    """
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        raise ManualReviewRequired(
            "OPENAI_API_KEY is not configured; resume requires manual review."
        )

    try:
        import openai
    except ImportError:
        raise ManualReviewRequired("openai package not installed.")

    truncated = text[:_MAX_INPUT_CHARS]

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=[
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': f'Parse the following resume:\n\n{truncated}'},
            ],
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=2048,
        )
    except Exception as exc:
        raise ManualReviewRequired(f"OpenAI API call failed: {exc}")

    content = (response.choices[0].message.content or '').strip()
    if not content:
        raise ManualReviewRequired("OpenAI returned an empty response.")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ManualReviewRequired(f"OpenAI returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        raise ManualReviewRequired("OpenAI returned unexpected data structure.")

    return parsed

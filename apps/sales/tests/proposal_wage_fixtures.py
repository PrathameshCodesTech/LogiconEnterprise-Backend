"""Shared wage master fixtures for sales proposal generation tests."""

from datetime import date
from decimal import Decimal

from apps.jobs.models import JobRole
from apps.wages.models import LocationArea, WageCategory, MinimumWageRate


def ensure_wage_category(code='unskilled', name='Unskilled'):
    return WageCategory.objects.get_or_create(
        code=code,
        defaults={'name': name, 'description': ''},
    )[0]


def ensure_location_area_mumbai():
    state, _ = LocationArea.objects.get_or_create(
        code='mh-test',
        parent=None,
        defaults={
            'name': 'Maharashtra',
            'area_type': 'state',
            'state_name': 'Maharashtra',
            'is_active': True,
        },
    )
    city, _ = LocationArea.objects.get_or_create(
        code='mumbai-test',
        parent=state,
        defaults={
            'name': 'Mumbai',
            'area_type': 'city',
            'state_name': 'Maharashtra',
            'is_active': True,
        },
    )
    return city


def ensure_minimum_wage(org, location, wage_category, job_role, monthly_wage=12000):
    return MinimumWageRate.objects.get_or_create(
        org=org,
        location=location,
        wage_category=wage_category,
        role=job_role,
        effective_from=date(2025, 1, 1),
        defaults={
            'monthly_wage': Decimal(monthly_wage),
            'daily_wage': Decimal(monthly_wage) / 26,
            'is_active': True,
            'source_note': 'test fixture',
        },
    )[0]


def wire_site_and_requirement_for_wages(site, role_requirement, location, wage_category):
    site.location_area = location
    site.save(update_fields=['location_area', 'updated_at'])
    role_requirement.wage_category = wage_category
    role_requirement.save(update_fields=['wage_category', 'updated_at'])

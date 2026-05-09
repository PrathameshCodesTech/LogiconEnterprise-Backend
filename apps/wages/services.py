"""
apps/wages/services.py

Business logic for wage lookup.
"""

from django.db.models import Q


def get_applicable_minimum_wage(state, city, wage_category, on_date, role=None):
    """
    Returns the most recent active MinimumWageRate for the given parameters.

    Lookup priority:
    1. Role + exact state/city match
    2. Role + state-level match
    3. Generic exact state/city match
    4. Generic state-level match

    Args:
        state (str): State name
        city (str): City name (can be empty/None)
        wage_category: WageCategory instance or pk
        on_date (date): The date to check applicability for
        role: Optional JobRole instance or pk for role-specific override

    Returns:
        MinimumWageRate instance or None
    """
    from .models import MinimumWageRate

    base_qs = MinimumWageRate.objects.filter(
        state=state,
        wage_category=wage_category,
        is_active=True,
        effective_from__lte=on_date,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)
    )

    city_filter = Q(city=city) if city else None
    state_filter = Q(city__isnull=True) | Q(city='')
    priority_filters = []

    if role is not None:
        if city_filter is not None:
            priority_filters.append(Q(role=role) & city_filter)
        priority_filters.append(Q(role=role) & state_filter)

    if city_filter is not None:
        priority_filters.append(Q(role__isnull=True) & city_filter)
    priority_filters.append(Q(role__isnull=True) & state_filter)

    for query_filter in priority_filters:
        result = base_qs.filter(query_filter).order_by('-effective_from').first()
        if result:
            return result

    return None

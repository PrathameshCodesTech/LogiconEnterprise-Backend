"""
apps/wages/services.py

Wage lookup service. Finds the most applicable MinimumWageRate for a
given org/location/state-city/role/wage category/date combination.
"""

from django.db.models import Q


def get_applicable_minimum_wage(
    wage_category,
    on_date,
    *,
    state=None,
    city=None,
    location=None,
    site=None,
    role=None,
    org=None,
):
    """
    Return the most recent active MinimumWageRate matching the given parameters.

    Lookup priority:
    1. exact location + role
    2. each ancestor location + role, nearest first
    3. exact location + no role
    4. each ancestor location + no role
    5. legacy city + state + role
    6. legacy state + role
    7. legacy city + state + no role
    8. legacy state + no role
    """
    from .models import MinimumWageRate

    if site is not None and state is None:
        state = getattr(site, 'state', None) or None
        city = getattr(site, 'city', None) or None

    base_qs = MinimumWageRate.objects.filter(
        wage_category=wage_category,
        is_active=True,
        effective_from__lte=on_date,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)
    )

    org_filters = []
    if org is not None:
        org_filters.append(Q(org=org))
    org_filters.append(Q(org__isnull=True))

    location_chain = _build_location_chain(location=location, site=site)
    priority_filters = []

    if location_chain:
        for loc_id in location_chain:
            if role is not None:
                priority_filters.append(('location', loc_id, role))
        for loc_id in location_chain:
            priority_filters.append(('location', loc_id, None))

    if state:
        city_val = city if city else None
        if role is not None:
            if city_val:
                priority_filters.append(('city', city_val, role))
            priority_filters.append(('state', state, role))
        if city_val:
            priority_filters.append(('city', city_val, None))
        priority_filters.append(('state', state, None))

    for org_q in org_filters:
        for kind, val, role_val in priority_filters:
            if kind == 'location':
                q = Q(location_id=val)
            elif kind == 'city':
                q = Q(location__isnull=True, state=state, city=val)
            else:
                q = Q(location__isnull=True, state=val) & (Q(city__isnull=True) | Q(city=''))

            if role_val is not None:
                q &= Q(role=role_val)
            else:
                q &= Q(role__isnull=True)

            result = base_qs.filter(org_q).filter(q).order_by('-effective_from').first()
            if result:
                return result

    return None


def _build_location_chain(*, location=None, site=None):
    """Return exact LocationArea id plus ancestor ids, nearest first."""
    from .models import LocationArea

    loc = None
    if location is not None:
        loc = location if isinstance(location, LocationArea) else LocationArea.objects.get(pk=location)
    elif site is not None:
        # Prefer the explicit FK if set; fall back to text-based resolution.
        loc = getattr(site, 'location_area', None) or _resolve_location_from_site(site)

    if loc is None:
        return []

    return [loc.pk, *loc.ancestor_ids()]


def _resolve_location_from_site(site):
    """
    Bridge SiteProfile city/state text to LocationArea.

    SiteProfile does not yet store a LocationArea FK. Resolving by text keeps
    location-based wage masters usable for Site Role Requirement auto-fill.
    """
    from .models import LocationArea

    city = (getattr(site, 'city', None) or '').strip()
    state = (getattr(site, 'state', None) or '').strip()

    if city:
        qs = LocationArea.objects.filter(is_active=True, area_type='city').filter(
            Q(name__iexact=city) | Q(code__iexact=city)
        )
        if state:
            qs = qs.filter(Q(state_name__iexact=state) | Q(parent__state_name__iexact=state))
        loc = qs.order_by('name').first()
        if loc:
            return loc

    if not state:
        return None

    return LocationArea.objects.filter(
        is_active=True,
        area_type='state',
    ).filter(
        Q(name__iexact=state) | Q(code__iexact=state) | Q(state_name__iexact=state)
    ).order_by('name').first()

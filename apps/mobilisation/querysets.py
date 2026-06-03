"""
apps/mobilisation/querysets.py

Queryset scope filter for MobilisationSetupRequest.
"""

from django.db.models import Q

from apps.access.scope import get_accessible_scope_paths


def _scope_q(field, paths):
    if not paths:
        return Q(pk__in=[])
    q = Q()
    for p in paths:
        q |= Q(**{field: p}) | Q(**{f'{field}__startswith': p + '/'})
    return q


def filter_mobilisation_requests_for_user(queryset, user):
    """
    Filter MobilisationSetupRequest queryset via client.scope_node path.
    Requests with no client are org-scoped: visible to any user in the same org
    who has at least one scope assignment.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    org_id = getattr(user, 'org_id', None)
    client_q = _scope_q('client__scope_node__path', paths)
    no_client_q = Q(client__isnull=True, org_id=org_id) if org_id else Q(pk__in=[])
    return queryset.filter(client_q | no_client_q).distinct()

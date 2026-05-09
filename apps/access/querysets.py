"""
apps/access/querysets.py

Queryset scope filters.

Each function takes (queryset, user) and returns a filtered queryset
containing only records within the user's accessible scope.

Rules:
  - Superuser: queryset returned unchanged.
  - Unassigned user (no role assignments): queryset.none() returned.
  - Assigned user: filter by scope path prefix matching.

Path prefix matching:
  Assigned path "logicon/client-a" grants access to:
    - "logicon/client-a"              (exact)
    - "logicon/client-a/site-1"       (startswith + '/')
    - "logicon/client-a/site-1/dept"  (deeper descendants)

All filters use .distinct() where joins may produce duplicate rows.
"""

from django.db.models import Q

from apps.access.scope import get_accessible_scope_paths


def _scope_q(field: str, paths: set) -> Q:
    """
    Build a Q object for prefix-matching scope paths against a model field.

    field: dotted path to the scope path field, e.g. 'scope_node__path'
    paths: set of assigned scope paths (not '*')
    """
    if not paths:
        return Q(pk__in=[])
    q = Q()
    for p in paths:
        q |= Q(**{field: p}) | Q(**{f'{field}__startswith': p + '/'})
    return q


def filter_scope_nodes_for_user(queryset, user):
    """Filter ScopeNode queryset to nodes within user's accessible scope."""
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    return queryset.filter(_scope_q('path', paths)).distinct()


def filter_clients_for_user(queryset, user):
    """
    Filter Client queryset.
    Client.scope_node.path must be within accessible paths.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    return queryset.filter(_scope_q('scope_node__path', paths)).distinct()


def filter_sites_for_user(queryset, user):
    """
    Filter SiteProfile queryset.
    Site.scope_node.path must be within accessible paths.
    Also grants access if the parent client scope node is accessible.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('scope_node__path', paths)
    client_q = _scope_q('client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_site_role_requirements_for_user(queryset, user):
    """
    Filter SiteRoleRequirement queryset via site.scope_node path.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_mrfs_for_user(queryset, user):
    """
    Filter ManpowerRequest queryset via site.scope_node path.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_mrf_line_items_for_user(queryset, user):
    """
    Filter MRFLineItem queryset via mrf.site.scope_node path.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('mrf__site__scope_node__path', paths)
    client_q = _scope_q('mrf__site__client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_hiring_applications_for_user(queryset, user):
    """
    Filter HiringApplication queryset via site.scope_node path.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_site_deployments_for_user(queryset, user):
    """
    Filter SiteDeployment queryset via site.scope_node path.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    return queryset.filter(site_q | client_q).distinct()


def filter_campaigns_for_user(queryset, user):
    """
    Filter QRCampaign queryset.
    Campaigns with a site: filter via site scope path.
    Campaigns without a site: accessible if user has any scope in the campaign's org.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    org_id = getattr(user, 'org_id', None)
    no_site_q = Q(site__isnull=True, org_id=org_id) if org_id else Q(pk__in=[])
    return queryset.filter(site_q | client_q | no_site_q).distinct()


def filter_campaign_job_roles_for_user(queryset, user):
    """Filter CampaignJobRole queryset via campaign's site scope path."""
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('campaign__site__scope_node__path', paths)
    client_q = _scope_q('campaign__site__client__scope_node__path', paths)
    org_id = getattr(user, 'org_id', None)
    no_site_q = Q(campaign__site__isnull=True, campaign__org_id=org_id) if org_id else Q(pk__in=[])
    return queryset.filter(site_q | client_q | no_site_q).distinct()


def filter_form_fields_for_user(queryset, user):
    """Filter FormField queryset via campaign's site scope path."""
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('campaign__site__scope_node__path', paths)
    client_q = _scope_q('campaign__site__client__scope_node__path', paths)
    org_id = getattr(user, 'org_id', None)
    no_site_q = Q(campaign__site__isnull=True, campaign__org_id=org_id) if org_id else Q(pk__in=[])
    return queryset.filter(site_q | client_q | no_site_q).distinct()


def filter_submissions_for_user(queryset, user):
    """Filter IntakeSubmission queryset via campaign's site scope path."""
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    site_q = _scope_q('campaign__site__scope_node__path', paths)
    client_q = _scope_q('campaign__site__client__scope_node__path', paths)
    org_id = getattr(user, 'org_id', None)
    no_site_q = Q(campaign__site__isnull=True, campaign__org_id=org_id) if org_id else Q(pk__in=[])
    return queryset.filter(site_q | client_q | no_site_q).distinct()


def filter_users_for_user(queryset, user):
    """
    Filter User queryset to users in the same org as the actor.
    Users are org-scoped, not hierarchy-scoped. Only roles that include
    user.read (admin, hr_admin) will ever reach this filter.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    return queryset.filter(org_id=org_id)


def filter_employees_for_user(queryset, user):
    """
    Filter Employee queryset.
    An employee is accessible if they are deployed to an accessible site.
    Uses SiteDeployment as the join bridge.
    """
    if user.is_superuser:
        return queryset
    paths = get_accessible_scope_paths(user)
    if not paths:
        return queryset.none()
    from apps.deployment.models import SiteDeployment
    site_q = _scope_q('site__scope_node__path', paths)
    client_q = _scope_q('site__client__scope_node__path', paths)
    accessible_ids = (
        SiteDeployment.objects
        .filter(site_q | client_q)
        .values_list('employee_id', flat=True)
        .distinct()
    )
    return queryset.filter(pk__in=accessible_ids)

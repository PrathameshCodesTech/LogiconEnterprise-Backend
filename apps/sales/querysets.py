"""
apps/sales/querysets.py

Org-scoped queryset filters for the sales app.
All sales resources are org-scoped; they do not use the scope-node hierarchy.
"""

from django.db.models import Q

from apps.access.capabilities import is_sales_persona_user


def _owned_lead_q(user, prefix='') -> Q:
    """Build a lead ownership predicate for sales-persona row scoping."""
    field_prefix = f'{prefix}__' if prefix else ''
    return (
        Q(**{f'{field_prefix}sales_person': user}) |
        Q(**{f'{field_prefix}created_by': user})
    )


def _restrict_sales_persona(queryset, user, lead_prefix):
    if is_sales_persona_user(user):
        return queryset.filter(_owned_lead_q(user, lead_prefix))
    return queryset


def filter_leads_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(org_id=org_id)
    return _restrict_sales_persona(scoped, user, '')


def filter_lead_sites_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_site_surveys_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_sales_role_requirements_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_proposal_versions_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_proposal_budget_lines_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(proposal_version__lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'proposal_version__lead')


def filter_proposal_breakup_lines_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(proposal_version__lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'proposal_version__lead')


def filter_client_responses_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_activities_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


def filter_documents_for_user(queryset, user):
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'lead')


# ─── Phase H ──────────────────────────────────────────────────────────────────

def filter_survey_children_for_user(queryset, user):
    """Org-scope any SiteSurvey child row via survey.lead.org."""
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    scoped = queryset.filter(survey__lead__org_id=org_id)
    return _restrict_sales_persona(scoped, user, 'survey__lead')


def filter_proposal_component_rules_for_user(queryset, user):
    """
    Org-scope component rules. Users see their org's rules AND any global
    (org__isnull=True) rules.
    """
    from django.db.models import Q

    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.filter(org__isnull=True)
    return queryset.filter(Q(org_id=org_id) | Q(org__isnull=True))


def filter_survey_role_mappings_for_user(queryset, user):
    """Org-scope survey role mappings."""
    if user.is_superuser:
        return queryset
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return queryset.none()
    return queryset.filter(org_id=org_id)

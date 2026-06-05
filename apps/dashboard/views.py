"""
apps/dashboard/views.py

GET /api/dashboard/summary/

Returns a single stable-shape response with all sections gated by
capability + scope. All section keys are always present; missing access
yields zeros / empty lists, never absent keys.

Phase Dashboard-Backend-B adds:
- charts and drilldowns sub-objects in each section
- monthly_trend (6-month window, TruncMonth, gaps filled with 0)
- by_site / by_department / by_stage / by_job_role group-by breakdowns
- recent_activity extended with url, target_type, target_id, subtitle
"""

from datetime import date as _date

from django.db.models import Count, Q, Sum, OuterRef, Subquery
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.querysets import (
    filter_candidates_for_user,
    filter_clients_for_user,
    filter_hiring_applications_for_user,
    filter_mrf_line_items_for_user,
    filter_mrfs_for_user,
    filter_mobilisation_requests_for_user,
    filter_onboarding_requests_for_user,
    filter_resumes_for_user,
    filter_sites_for_user,
)
from apps.access.scope import user_has_any_capability, user_has_capability

DASHBOARD_URL = '/api/dashboard/summary/'


# ─── Monthly trend helpers ────────────────────────────────────────────────────

def _months_back(n=6):
    """Return list of (year, month) tuples for the past n months, oldest first."""
    now = timezone.now()
    year, month = now.year, now.month
    months = []
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    months.reverse()
    return months


def _build_monthly_trend(queryset, date_field='created_at', n_months=6):
    """
    Aggregate an already-scoped queryset by calendar month for the past
    n_months (including current month). Gaps are filled with count=0.
    """
    months = _months_back(n_months)
    start_year, start_month = months[0]
    cutoff = _date(start_year, start_month, 1)

    trend_qs = (
        queryset
        .filter(**{f'{date_field}__date__gte': cutoff})
        .annotate(_month=TruncMonth(date_field))
        .values('_month')
        .annotate(count=Count('id'))
        .order_by('_month')
    )
    counts_map = {}
    for row in trend_qs:
        if row['_month']:
            counts_map[(row['_month'].year, row['_month'].month)] = row['count']

    return [
        {
            'period': f'{y:04d}-{m:02d}',
            'label': _date(y, m, 1).strftime('%b %Y'),
            'count': counts_map.get((y, m), 0),
        }
        for y, m in months
    ]


# ─── Section helpers ──────────────────────────────────────────────────────────

def _audience(user):
    if user.is_superuser:
        return 'internal'
    ut = getattr(user, 'user_type', None)
    if ut == 'client':
        return 'client'
    if ut == 'field':
        return 'field'
    if ut == 'internal':
        return 'internal'
    return 'unknown'


def _user_section(user):
    return {
        'id': user.pk,
        'username': user.username,
        'email': user.email or '',
        'user_type': getattr(user, 'user_type', None) or '',
    }


def _scope_section(user):
    from apps.sites.models import Client, SiteProfile
    client_count = filter_clients_for_user(Client.objects.all(), user).count()
    site_count = filter_sites_for_user(SiteProfile.objects.all(), user).count()
    return {
        'org_id': getattr(user, 'org_id', None),
        'client_count': client_count,
        'site_count': site_count,
    }


def _my_work_section(user):
    from apps.workflow.models import WorkflowInstance, WorkflowStepInstance
    from apps.mrf.models import ManpowerRequest
    from apps.mobilisation.models import MobilisationSetupRequest

    if user.is_superuser:
        scoped_wf_ids = WorkflowInstance.objects.filter(status='active').values('id')
    else:
        accessible_mrf_ids = filter_mrfs_for_user(
            ManpowerRequest.objects.only('id'), user,
        ).values_list('id', flat=True)
        accessible_ob_ids = filter_mobilisation_requests_for_user(
            MobilisationSetupRequest.objects.only('id'), user,
        ).values_list('id', flat=True)
        scoped_wf_ids = WorkflowInstance.objects.filter(
            status='active',
        ).filter(
            Q(mrf_id__in=accessible_mrf_ids) |
            Q(client_onboarding_request_id__in=accessible_ob_ids)
        ).values('id')

    task_qs = (
        WorkflowStepInstance.objects
        .filter(
            status='active',
            workflow__status='active',
            workflow_id__in=scoped_wf_ids,
            assigned_user=user,
        )
        .select_related(
            'workflow',
            'workflow__mrf__site',
            'workflow__client_onboarding_request__client',
        )
        .order_by('-activated_at')
    )

    active_task_count = task_qs.count()
    latest_tasks = []
    for step in task_qs[:5]:
        wf = step.workflow
        if wf.mrf_id:
            mrf = wf.mrf
            site = mrf.site if mrf else None
            target_type = 'mrf'
            target_title = (
                f'MRF #{mrf.pk} - {site.name}' if site else f'MRF #{mrf.pk}'
            )
        else:
            ob = wf.client_onboarding_request
            client = ob.client if ob else None
            target_type = 'mobilisation'
            target_title = (
                f'Mobilisation #{ob.pk} - {client.name}'
                if client else f'Mobilisation #{ob.pk}'
            )
        latest_tasks.append({
            'step_id': step.pk,
            'workflow_id': wf.pk,
            'target_type': target_type,
            'target_title': target_title,
            'step_name': step.step_name,
            'activated_at': step.activated_at,
        })

    return {'active_task_count': active_task_count, 'latest_tasks': latest_tasks}


def _client_overview_section(user):
    from apps.sites.models import Client, SiteProfile
    from apps.access.capabilities import is_client_facing_user

    client_facing = is_client_facing_user(user)
    has_access = user_has_any_capability(user, ['client.read', 'site.read', 'department.read'])
    empty = {
        'client_count': 0, 'site_count': 0, 'department_count': 0, 'clients': [],
        'charts': {'sites_by_client': [], 'departments_by_client': []},
    }
    if not has_access:
        return empty

    client_qs = filter_clients_for_user(Client.objects.all(), user)
    site_qs = filter_sites_for_user(SiteProfile.objects.all(), user)

    accessible_client_ids = client_qs.values_list('id', flat=True)
    accessible_site_ids = site_qs.values_list('id', flat=True)
    if client_facing:
        dept_count = 0
        departments_by_client = []
    else:
        from apps.core.models import Department

        dept_count = Department.objects.filter(
            Q(client_id__in=accessible_client_ids) | Q(site_id__in=accessible_site_ids)
        ).distinct().count()

        # departments_by_client: departments directly linked to accessible clients
        dept_by_client_rows = list(
            Department.objects
            .filter(client_id__in=accessible_client_ids)
            .values('client_id', 'client__name')
            .annotate(count=Count('id'))
            .order_by('-count')[:8]
        )
        departments_by_client = [
            {
                'key': f"client:{r['client_id']}",
                'label': r['client__name'] or f"Client #{r['client_id']}",
                'count': r['count'],
            }
            for r in dept_by_client_rows
        ]

    clients_with_sites = list(
        client_qs
        .annotate(computed_site_count=Count('sites', filter=Q(sites__is_active=True)))
        .order_by('name')[:10]
    )

    # sites_by_client chart: count of active sites per client, top 8
    site_by_client_rows = list(
        site_qs
        .filter(client__isnull=False)
        .values('client_id', 'client__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    sites_by_client = [
        {
            'key': f"client:{r['client_id']}",
            'label': r['client__name'] or f"Client #{r['client_id']}",
            'count': r['count'],
            'url': f"/sites?client={r['client_id']}",
        }
        for r in site_by_client_rows
    ]

    return {
        'client_count': client_qs.count(),
        'site_count': site_qs.count(),
        'department_count': dept_count,
        'clients': [
            {
                'id': c.pk,
                'name': c.name,
                'code': c.code,
                'site_count': c.computed_site_count,
            }
            for c in clients_with_sites
        ],
        'charts': {
            'sites_by_client': sites_by_client,
            'departments_by_client': departments_by_client,
        },
    }


def _onboarding_section(user):
    from apps.mobilisation.models import MobilisationSetupRequest

    _drilldowns = {
        'all': '/mobilisation',
        'draft': '/mobilisation?status=draft',
        'in_review': '/mobilisation?status=in_review',
        'approved': '/mobilisation?status=approved',
        'rejected': '/mobilisation?status=rejected',
        'finalization_failed': '/mobilisation?finalization_status=failed',
    }
    empty = {
        'total': 0, 'draft': 0, 'in_review': 0, 'approved': 0,
        'rejected': 0, 'finalized': 0, 'finalization_failed': 0, 'recent': [],
        'charts': {'by_status': [], 'by_finalization': [], 'monthly_trend': []},
        'drilldowns': _drilldowns,
    }
    if not (user_has_capability(user, 'mobilisation.read') or
            user_has_capability(user, 'client_onboarding.read')):
        return empty

    ob_qs = filter_mobilisation_requests_for_user(
        MobilisationSetupRequest.objects.all(), user,
    )
    counts = ob_qs.aggregate(
        total=Count('id'),
        draft=Count('id', filter=Q(status='draft')),
        in_review=Count('id', filter=Q(status='in_review')),
        approved=Count('id', filter=Q(status='approved')),
        rejected=Count('id', filter=Q(status='rejected')),
        finalized=Count('id', filter=Q(finalization_status='finalized')),
        finalization_failed=Count('id', filter=Q(finalization_status='failed')),
        not_finalized=Count('id', filter=Q(finalization_status='not_finalized')),
    )
    recent = list(
        ob_qs.select_related('client', 'requested_by').order_by('-created_at')[:5]
    )

    by_status = [
        {'key': 'draft', 'label': 'Draft', 'count': counts['draft'], 'url': '/mobilisation?status=draft'},
        {'key': 'in_review', 'label': 'In review', 'count': counts['in_review'], 'url': '/mobilisation?status=in_review'},
        {'key': 'approved', 'label': 'Approved', 'count': counts['approved'], 'url': '/mobilisation?status=approved'},
        {'key': 'rejected', 'label': 'Rejected', 'count': counts['rejected'], 'url': '/mobilisation?status=rejected'},
    ]
    by_finalization = [
        {'key': 'not_finalized', 'label': 'Not finalized', 'count': counts['not_finalized']},
        {'key': 'finalized', 'label': 'Finalized', 'count': counts['finalized']},
        {
            'key': 'failed', 'label': 'Failed',
            'count': counts['finalization_failed'],
            'url': '/mobilisation?finalization_status=failed',
        },
    ]
    monthly_trend = _build_monthly_trend(ob_qs, 'created_at')

    return {
        'total': counts['total'],
        'draft': counts['draft'],
        'in_review': counts['in_review'],
        'approved': counts['approved'],
        'rejected': counts['rejected'],
        'finalized': counts['finalized'],
        'finalization_failed': counts['finalization_failed'],
        'recent': [
            {
                'id': ob.pk,
                'mobilisation_type': ob.mobilisation_type,
                'client_name': ob.client.name if ob.client else '',
                'status': ob.status,
                'finalization_status': ob.finalization_status,
                'created_at': ob.created_at,
            }
            for ob in recent
        ],
        'charts': {
            'by_status': by_status,
            'by_finalization': by_finalization,
            'monthly_trend': monthly_trend,
        },
        'drilldowns': _drilldowns,
    }


def _mrf_section(user):
    from apps.mrf.models import ManpowerRequest
    from apps.workflow.models import WorkflowInstance

    _drilldowns = {
        'all': '/mrf',
        'draft': '/mrf?status=draft',
        'in_review': '/mrf?status=in_review',
        'approved': '/mrf?status=approved',
        'rejected': '/mrf?status=rejected',
        'request_changes': '/mrf?status=request_changes',
    }
    empty = {
        'total': 0, 'draft': 0, 'in_review': 0, 'approved': 0,
        'rejected': 0, 'request_changes': 0, 'recent': [],
        'charts': {'by_status': [], 'by_site': [], 'by_department': [], 'monthly_trend': []},
        'drilldowns': _drilldowns,
    }
    if not user_has_capability(user, 'mrf.read'):
        return empty

    mrf_qs = filter_mrfs_for_user(ManpowerRequest.objects.all(), user)
    counts = mrf_qs.aggregate(
        total=Count('id'),
        draft=Count('id', filter=Q(status='draft')),
        in_review=Count('id', filter=Q(status__in=[
            'submitted', 'hr_review', 'finance_review', 'admin_review', 'client_review',
        ])),
        approved=Count('id', filter=Q(status='approved')),
        rejected=Count('id', filter=Q(status='rejected')),
    )

    # by_site: top 8 sites by MRF count
    site_rows = list(
        mrf_qs
        .filter(site__isnull=False)
        .values('site_id', 'site__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    by_site = [
        {
            'key': f"site:{r['site_id']}",
            'label': r['site__name'] or f"Site #{r['site_id']}",
            'count': r['count'],
            'url': f"/mrf?site={r['site_id']}",
        }
        for r in site_rows
    ]

    # by_department: top 8 requesting departments by MRF count
    dept_rows = list(
        mrf_qs
        .filter(requesting_department__isnull=False)
        .values('requesting_department_id', 'requesting_department__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    by_department = [
        {
            'key': f"department:{r['requesting_department_id']}",
            'label': r['requesting_department__name'] or f"Dept #{r['requesting_department_id']}",
            'count': r['count'],
            'url': f"/mrf?department={r['requesting_department_id']}",
        }
        for r in dept_rows
    ]

    monthly_trend = _build_monthly_trend(mrf_qs, 'created_at')

    by_status = [
        {'key': 'draft', 'label': 'Draft', 'count': counts['draft'], 'url': '/mrf?status=draft'},
        {'key': 'in_review', 'label': 'In review', 'count': counts['in_review'], 'url': '/mrf?status=in_review'},
        {'key': 'approved', 'label': 'Approved', 'count': counts['approved'], 'url': '/mrf?status=approved'},
        {'key': 'rejected', 'label': 'Rejected', 'count': counts['rejected'], 'url': '/mrf?status=rejected'},
        {'key': 'request_changes', 'label': 'Request changes', 'count': 0, 'url': '/mrf?status=request_changes'},
    ]

    # Annotate recent MRFs with active workflow status (Subquery avoids N+1)
    active_wf_status = Subquery(
        WorkflowInstance.objects
        .filter(mrf=OuterRef('pk'), status='active')
        .values('status')[:1]
    )
    recent_mrfs = list(
        mrf_qs
        .annotate(workflow_status=active_wf_status)
        .select_related('site')
        .order_by('-created_at')[:5]
    )

    return {
        'total': counts['total'],
        'draft': counts['draft'],
        'in_review': counts['in_review'],
        'approved': counts['approved'],
        'rejected': counts['rejected'],
        'request_changes': 0,
        'recent': [
            {
                'id': m.pk,
                'request_number': m.request_number or '',
                'site_name': m.site.name if m.site else None,
                'status': m.status,
                'workflow_status': m.workflow_status,
                'created_at': m.created_at,
            }
            for m in recent_mrfs
        ],
        'charts': {
            'by_status': by_status,
            'by_site': by_site,
            'by_department': by_department,
            'monthly_trend': monthly_trend,
        },
        'drilldowns': _drilldowns,
    }


def _budget_section(user):
    from apps.budgets.models import BudgetPlan, BudgetReservation
    from apps.access.querysets import filter_budget_plans_for_user

    _drilldowns = {
        'all': '/budgets',
        'billable': '/budgets?budget_nature=billable',
        'non_billable': '/budgets?budget_nature=non_billable',
    }
    empty = {
        'plan_count': 0, 'total_amount': '0', 'reserved_amount': '0',
        'committed_amount': '0', 'available_amount': '0',
        'by_nature': {'billable': '0', 'non_billable': '0'},
        'charts': {'utilization': [], 'by_nature': [], 'by_scope': [], 'top_plans': []},
        'drilldowns': _drilldowns,
    }
    if not user_has_capability(user, 'budget.read'):
        return empty

    if user.is_superuser:
        bp_qs = BudgetPlan.objects.filter(is_active=True)
    else:
        org_id = getattr(user, 'org_id', None)
        if not org_id:
            return empty
        bp_qs = filter_budget_plans_for_user(
            BudgetPlan.objects.filter(org_id=org_id, is_active=True),
            user,
        )

    agg = bp_qs.aggregate(
        plan_count=Count('id'),
        total_amount=Sum('amount'),
        billable=Sum('amount', filter=Q(budget_nature='billable')),
        non_billable=Sum('amount', filter=Q(budget_nature='non_billable')),
        # by_scope: client-only, site-scoped, department-scoped
        client_scope_amount=Sum('amount', filter=Q(
            client__isnull=False, site__isnull=True, department__isnull=True,
        )),
        site_scope_amount=Sum('amount', filter=Q(site__isnull=False)),
        dept_scope_amount=Sum('amount', filter=Q(department__isnull=False)),
    )
    plan_ids = bp_qs.values_list('id', flat=True)
    res_agg = BudgetReservation.objects.filter(budget_plan_id__in=plan_ids).aggregate(
        reserved=Sum('amount', filter=Q(status='reserved')),
        committed=Sum('amount', filter=Q(status='committed')),
    )

    total = agg['total_amount'] or 0
    reserved = res_agg['reserved'] or 0
    committed = res_agg['committed'] or 0
    available = total - reserved - committed
    billable = agg['billable'] or 0
    non_billable = agg['non_billable'] or 0
    client_scope = agg['client_scope_amount'] or 0
    site_scope = agg['site_scope_amount'] or 0
    dept_scope = agg['dept_scope_amount'] or 0

    # top plans by amount (max 5)
    top_plan_rows = list(bp_qs.order_by('-amount')[:5])
    top_plans = [
        {
            'key': f'budget:{p.pk}',
            'label': p.name,
            'amount': str(p.amount),
            'url': '/budgets',
        }
        for p in top_plan_rows
    ]

    return {
        'plan_count': agg['plan_count'] or 0,
        'total_amount': str(total),
        'reserved_amount': str(reserved),
        'committed_amount': str(committed),
        'available_amount': str(available),
        'by_nature': {
            'billable': str(billable),
            'non_billable': str(non_billable),
        },
        'charts': {
            'utilization': [
                {'key': 'committed', 'label': 'Committed', 'amount': str(committed), 'url': '/budgets?reservation_status=committed'},
                {'key': 'reserved', 'label': 'Reserved', 'amount': str(reserved), 'url': '/budgets?reservation_status=reserved'},
                {'key': 'available', 'label': 'Available', 'amount': str(available)},
            ],
            'by_nature': [
                {'key': 'billable', 'label': 'Billable', 'amount': str(billable), 'url': '/budgets?budget_nature=billable'},
                {'key': 'non_billable', 'label': 'Non-billable', 'amount': str(non_billable), 'url': '/budgets?budget_nature=non_billable'},
            ],
            'by_scope': [
                {'key': 'client', 'label': 'Client', 'amount': str(client_scope), 'url': '/budgets?scope_level=client'},
                {'key': 'site', 'label': 'Site', 'amount': str(site_scope), 'url': '/budgets?scope_level=site'},
                {'key': 'department', 'label': 'Department', 'amount': str(dept_scope), 'url': '/budgets?scope_level=department'},
            ],
            'top_plans': top_plans,
        },
        'drilldowns': _drilldowns,
    }


def _hiring_section(user):
    from apps.hiring.models import HiringApplication
    from apps.mrf.models import MRFLineItem

    _drilldowns = {
        'all': '/hiring-applications',
    }
    empty = {
        'application_count': 0, 'open_count': 0, 'selected_count': 0,
        'joined_count': 0, 'rejected_count': 0, 'demand_count': 0,
        'latest_applications': [],
        'charts': {'by_status': [], 'by_stage': [], 'by_job_role': []},
        'drilldowns': _drilldowns,
    }
    if not user_has_capability(user, 'hiring_application.read'):
        return empty

    ha_qs = filter_hiring_applications_for_user(HiringApplication.objects.all(), user)
    terminal = {'selected', 'rejected', 'offer_released', 'offer_accepted',
                'offer_declined', 'deployed', 'cancelled'}
    counts = ha_qs.aggregate(
        application_count=Count('id'),
        open_count=Count('id', filter=~Q(status__in=terminal)),
        selected_count=Count('id', filter=Q(status='selected')),
        joined_count=Count('id', filter=Q(status='deployed')),
        rejected_count=Count('id', filter=Q(status='rejected')),
    )

    demand_qs = filter_mrf_line_items_for_user(
        MRFLineItem.objects.filter(mrf__status='approved'), user,
    )
    demand_count = demand_qs.count()

    # by_stage: top 8 pipeline stages by application count
    stage_rows = list(
        ha_qs
        .filter(current_stage__isnull=False)
        .values('current_stage_id', 'current_stage__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    by_stage = [
        {
            'key': f"stage:{r['current_stage_id']}",
            'label': r['current_stage__name'] or f"Stage #{r['current_stage_id']}",
            'count': r['count'],
            'url': f"/hiring-applications?stage={r['current_stage_id']}",
        }
        for r in stage_rows
    ]

    # by_job_role: top 8 job roles by application count
    role_rows = list(
        ha_qs
        .filter(job_role__isnull=False)
        .values('job_role_id', 'job_role__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    by_job_role = [
        {
            'key': f"job_role:{r['job_role_id']}",
            'label': r['job_role__name'] or f"Role #{r['job_role_id']}",
            'count': r['count'],
            'url': f"/hiring-applications?job_role={r['job_role_id']}",
        }
        for r in role_rows
    ]

    by_status = [
        {'key': 'open', 'label': 'Open', 'count': counts['open_count']},
        {'key': 'selected', 'label': 'Selected', 'count': counts['selected_count']},
        {'key': 'joined', 'label': 'Joined', 'count': counts['joined_count']},
        {'key': 'rejected', 'label': 'Rejected', 'count': counts['rejected_count']},
    ]

    recent = list(
        ha_qs.select_related('candidate', 'site', 'job_role').order_by('-created_at')[:5]
    )
    return {
        'application_count': counts['application_count'],
        'open_count': counts['open_count'],
        'selected_count': counts['selected_count'],
        'joined_count': counts['joined_count'],
        'rejected_count': counts['rejected_count'],
        'demand_count': demand_count,
        'latest_applications': [
            {
                'id': a.pk,
                'candidate_name': f'{a.candidate.first_name} {a.candidate.last_name}'.strip(),
                'site_name': a.site.name if a.site else None,
                'job_role_name': a.job_role.name if a.job_role else None,
                'status': a.status,
                'created_at': a.created_at,
            }
            for a in recent
        ],
        'charts': {
            'by_status': by_status,
            'by_stage': by_stage,
            'by_job_role': by_job_role,
        },
        'drilldowns': _drilldowns,
    }


def _talent_section(user):
    from apps.talent.models import Candidate, Resume, CandidateSkill

    _drilldowns = {
        'all': '/candidates',
        'uploaded': '/candidates?resume_status=uploaded',
        'manual_review': '/candidates?resume_status=manual_review',
    }
    empty = {
        'candidate_count': 0, 'active_candidate_count': 0,
        'resume_count': 0, 'manual_review_count': 0, 'uploaded_count': 0,
        'charts': {'by_resume_status': [], 'by_availability': [], 'top_skills': []},
        'drilldowns': _drilldowns,
    }
    # Guard: don't expose candidate pool to client users even if they somehow have the capability
    user_type = getattr(user, 'user_type', None)
    if user_type == 'client':
        return empty
    if not user_has_any_capability(user, ['candidate.read', 'resume.read']):
        return empty

    candidate_qs = filter_candidates_for_user(Candidate.objects.all(), user)
    resume_qs = filter_resumes_for_user(Resume.objects.all(), user)

    c_counts = candidate_qs.aggregate(
        candidate_count=Count('id'),
        active_candidate_count=Count('id', filter=Q(lifecycle_status='active')),
        avail_count=Count('id', filter=Q(
            availability_status__in=['available_now', 'available_from_date'],
        )),
        deployed_count=Count('id', filter=Q(availability_status='currently_deployed')),
    )
    r_counts = resume_qs.aggregate(
        resume_count=Count('id'),
        manual_review_count=Count('id', filter=Q(status='manual_review')),
        uploaded_count=Count('id', filter=Q(status='uploaded')),
    )

    # by_resume_status: all statuses that have at least one resume
    resume_status_rows = list(
        resume_qs
        .values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    by_resume_status = [
        {
            'key': r['status'],
            'label': r['status'].replace('_', ' ').title(),
            'count': r['count'],
        }
        for r in resume_status_rows
    ]

    # by_availability: coarse buckets
    by_availability = [
        {'key': 'available', 'label': 'Available', 'count': c_counts['avail_count']},
        {'key': 'deployed', 'label': 'Deployed', 'count': c_counts['deployed_count']},
    ]

    # top_skills: most common normalized skills across scoped candidates
    skill_rows = list(
        CandidateSkill.objects
        .filter(candidate__in=candidate_qs)
        .exclude(normalized_skill_name='')
        .values('normalized_skill_name')
        .annotate(count=Count('candidate_id', distinct=True))
        .order_by('-count')[:10]
    )
    top_skills = [
        {
            'key': f"skill:{r['normalized_skill_name']}",
            'label': r['normalized_skill_name'],
            'count': r['count'],
            'url': f"/candidates?skill={r['normalized_skill_name']}",
        }
        for r in skill_rows
    ]

    return {
        'candidate_count': c_counts['candidate_count'],
        'active_candidate_count': c_counts['active_candidate_count'],
        'resume_count': r_counts['resume_count'],
        'manual_review_count': r_counts['manual_review_count'],
        'uploaded_count': r_counts['uploaded_count'],
        'charts': {
            'by_resume_status': by_resume_status,
            'by_availability': by_availability,
            'top_skills': top_skills,
        },
        'drilldowns': _drilldowns,
    }


def _recent_activity(user, mrf_section_data, ob_section_data):
    """
    Merge recent MRFs + recent onboarding requests; sort by created_at; cap at 10.
    Reuses already-fetched data from mrf/onboarding sections to avoid extra queries.
    Extended in Phase B with url, target_type, target_id, subtitle.
    """
    activity = []

    for item in mrf_section_data.get('recent', []):
        site_name = item.get('site_name') or ''
        activity.append({
            'type': 'mrf',
            'target_type': 'mrf',
            'target_id': item['id'],
            'id': item['id'],
            'title': f"MRF #{item['id']}" + (f" - {site_name}" if site_name else ''),
            'subtitle': site_name,
            'status': item['status'],
            'created_at': item['created_at'],
            'url': f"/mrf/{item['id']}",
        })

    for item in ob_section_data.get('recent', []):
        mob_type = item.get('mobilisation_type', '').replace('_', ' ').title()
        activity.append({
            'type': 'mobilisation',
            'target_type': 'mobilisation',
            'target_id': item['id'],
            'id': item['id'],
            'title': item.get('client_name') or f"Mobilisation #{item['id']}",
            'subtitle': mob_type,
            'status': item['status'],
            'created_at': item['created_at'],
            'url': f"/mobilisation/{item['id']}",
        })

    activity.sort(key=lambda x: x['created_at'] or '', reverse=True)
    return activity[:10]


class DashboardSummaryView(APIView):
    """
    GET /api/dashboard/summary/

    Returns scope-aware dashboard summary. Permission: IsAuthenticated only.
    All section visibility is governed by capabilities + scope filters.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        mrf_data = _mrf_section(user)
        ob_data = _onboarding_section(user)

        return Response({
            'audience': _audience(user),
            'user': _user_section(user),
            'scope': _scope_section(user),
            'sections': {
                'my_work': _my_work_section(user),
                'client_overview': _client_overview_section(user),
                'onboarding': ob_data,
                'mrf': mrf_data,
                'budget': _budget_section(user),
                'hiring': _hiring_section(user),
                'talent': _talent_section(user),
                'recent_activity': _recent_activity(user, mrf_data, ob_data),
            },
        })

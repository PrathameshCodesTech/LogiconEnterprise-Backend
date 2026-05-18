"""
apps/budgets/tests/test_budget_auto_resolve.py

Phase MRF-Budget-AutoResolve-A — backend tests.

Scenarios (A01–A24):

  Resolver logic:
  A01  Exact department match returned when available
  A02  Site-level fallback used when no department budget
  A03  Client-level fallback used when no site budget
  A04  Exact department beats site-level fallback
  A05  Multiple exact same-priority plans raise BudgetReservationError
  A06  Multiple site-level plans raise BudgetReservationError
  A07  Multiple client-level plans raise BudgetReservationError
  A08  No match raises BudgetReservationError
  A09  Non-billable MRF raises BudgetReservationError
  A10  Inactive plan is not considered
  A11  Draft (non-active) status plan is not considered
  A12  Wrong budget_type is not considered

  reserve_budget_for_mrf auto-link:
  A13  Billable MRF with no budget_plan auto-resolves and creates reservation
  A14  Auto-resolve attaches budget_plan onto mrf and saves it
  A15  Explicit budget_plan on MRF still wins (resolver not called)
  A16  Non-billable MRF with no budget_plan returns None (no reservation)
  A17  Auto-resolve fails → BudgetReservationError propagates (workflow start 400)
  A18  Insufficient budget after auto-resolve → BudgetReservationError

  billing_rate_snapshot auto-fill:
  A19  Creating line item with SRR auto-fills billing_rate_snapshot from SRR

  MRF serializer resolved budget context:
  A20  MRF detail includes resolved_budget_plan_id and scope for auto-resolved
  A21  MRF detail resolved_budget_scope='explicit' when budget_plan manually set
  A22  MRF detail resolved_budget_scope='none' when non-billable with no budget

  Workflow task drawer:
  A23  My-tasks detail includes resolved budget context in target.mrf

  Readiness with auto-resolution:
  A24  Readiness endpoint includes budget plan info from auto-resolver
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.exceptions import BudgetReservationError
from apps.budgets.models import BudgetPlan, BudgetReservation
from apps.budgets.services import reserve_budget_for_mrf, resolve_budget_plan_for_mrf
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.workflow.models import (
    StepAssignmentConfig, WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping,
)
from apps.workflow.services import start_mrf_workflow


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass',
        is_superuser=is_superuser, is_staff=is_superuser,
    )
    if org:
        u.org = org
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


def _budget(org, client, name, code, site=None, department=None,
            amount='900000.00', budget_type='headcount', status='active', is_active=True):
    return BudgetPlan.objects.create(
        org=org, name=name, code=code,
        budget_nature='billable',
        budget_type=budget_type,
        client=client, site=site, department=department,
        period_start=datetime.date(2026, 1, 1),
        amount=Decimal(amount),
        currency='INR',
        status=status,
        is_active=is_active,
    )


def _mrf(org, site, actor, billing_type='billable', required_department=None,
         budget_plan=None, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=actor,
        mrf_type='new_hiring', status=status,
        billing_type=billing_type,
        required_department=required_department,
        budget_plan=budget_plan,
    )


def _srr(site, job_role, billing_rate=None, approved_headcount=30):
    return SiteRoleRequirement.objects.create(
        site=site, job_role=job_role,
        approved_headcount=approved_headcount,
        billing_type='billable',
        billing_rate=billing_rate,
        effective_from=datetime.date.today(),
        is_active=True,
    )


def _li(mrf, job_role, srr=None, headcount=10, billing_rate_snapshot=None):
    return MRFLineItem.objects.create(
        mrf=mrf, job_role=job_role,
        site_role_requirement=srr,
        headcount=headcount,
        billing_rate_snapshot=billing_rate_snapshot,
    )


def _wf_setup(org, approver):
    tpl = WorkflowTemplate.objects.create(
        org=org, name='auto-res-tpl', code='auto-res-tpl',
        trigger_type='mrf', version=1, is_active=True,
    )
    step = WorkflowStepTemplate.objects.create(
        template=tpl, order=1, code='hr_review', name='HR Review',
        assignment_mode='named_user', actor_type='internal',
        on_approve_next='END', on_reject_target='',
        requires_comment_on_reject=True,
        requires_comment_on_request_changes=True,
    )
    WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='mrf', template=tpl, is_active=True,
    )
    StepAssignmentConfig.objects.create(
        org=org, trigger_type='mrf', step_code='hr_review',
        assignment_mode='named_user', named_user=approver, is_active=True,
    )
    return tpl, step


# ─── Base fixture ─────────────────────────────────────────────────────────────

class AutoResolveBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='AR Test Org', code='ar-org')

        cls.n_company = _node(cls.org, 'ar-org', 'company', None, 0, 'ar-org')
        cls.n_client = _node(cls.org, 'ar-cli', 'client', cls.n_company, 1, 'ar-org/ar-cli')
        cls.n_site = _node(cls.org, 'ar-site', 'site', cls.n_client, 2, 'ar-org/ar-cli/ar-site')
        cls.n_site2 = _node(cls.org, 'ar-site2', 'site', cls.n_client, 2, 'ar-org/ar-cli/ar-site2')

        cls.site_client = Client.objects.create(
            org=cls.org, name='AR Client', code='ar-cli', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.site_client, name='AR Site', code='ar-site',
            scope_node=cls.n_site,
        )
        cls.site2 = SiteProfile.objects.create(
            org=cls.org, client=cls.site_client, name='AR Site2', code='ar-site2',
            scope_node=cls.n_site2,
        )

        cls.dept_a = Department.objects.create(
            org=cls.org, client=cls.site_client, name='Housekeeping', code='ar-hk', is_active=True,
        )
        cls.dept_b = Department.objects.create(
            org=cls.org, client=cls.site_client, name='Security', code='ar-sec', is_active=True,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='ar-guard', skill_category='unskilled',
        )

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr = _role(cls.org, 'hr_admin')
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr)

        cls.admin = _user('ar_admin', org=cls.org)
        cls.approver = _user('ar_approver', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)
        _assign(cls.approver, cls.role_hr, cls.n_company)

        # Shared workflow setup
        cls.wf_tpl, cls.wf_step = _wf_setup(cls.org, cls.approver)

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c


# ─── A01–A12: Resolver logic ─────────────────────────────────────────────────

class TestResolverLogic(AutoResolveBase):

    def test_a01_exact_department_match_returned(self):
        """Exact: client+site+department budget is returned."""
        bp = _budget(self.org, self.site_client, 'Dept Budget', 'ar-db-a01',
                     site=self.site, department=self.dept_a)
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_a)
        result = resolve_budget_plan_for_mrf(mrf)
        self.assertEqual(result.pk, bp.pk)

    def test_a02_site_level_fallback_when_no_dept_budget(self):
        """Site fallback: site budget (no dept) used when no dept budget exists."""
        bp = _budget(self.org, self.site_client, 'Site Budget A02', 'ar-sb-a02', site=self.site)
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_a)
        result = resolve_budget_plan_for_mrf(mrf)
        self.assertEqual(result.pk, bp.pk)

    def test_a03_client_level_fallback_when_no_site_budget(self):
        """Client fallback: client budget (no site/dept) used when no site budget."""
        bp = _budget(self.org, self.site_client, 'Client Budget A03', 'ar-cb-a03')
        mrf = _mrf(self.org, self.site, self.admin)
        result = resolve_budget_plan_for_mrf(mrf)
        self.assertEqual(result.pk, bp.pk)

    def test_a04_exact_department_beats_site_fallback(self):
        """Exact department budget takes priority over site-level fallback."""
        site_bp = _budget(self.org, self.site_client, 'Site BP A04', 'ar-sp-a04', site=self.site)
        dept_bp = _budget(self.org, self.site_client, 'Dept BP A04', 'ar-dp-a04',
                          site=self.site, department=self.dept_a)
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_a)
        result = resolve_budget_plan_for_mrf(mrf)
        self.assertEqual(result.pk, dept_bp.pk)

    def test_a05_multiple_exact_plans_raise_error(self):
        """Two exact same-priority plans → BudgetReservationError."""
        _budget(self.org, self.site_client, 'Exact1 A05', 'ar-e1-a05',
                site=self.site, department=self.dept_a)
        _budget(self.org, self.site_client, 'Exact2 A05', 'ar-e2-a05',
                site=self.site, department=self.dept_a)
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_a)
        with self.assertRaises(BudgetReservationError) as ctx:
            resolve_budget_plan_for_mrf(mrf)
        self.assertIn('Multiple', str(ctx.exception))

    def test_a06_multiple_site_level_plans_raise_error(self):
        """Two site-level plans with no dept → BudgetReservationError."""
        _budget(self.org, self.site_client, 'Site1 A06', 'ar-s1-a06', site=self.site)
        _budget(self.org, self.site_client, 'Site2 A06', 'ar-s2-a06', site=self.site)
        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(BudgetReservationError) as ctx:
            resolve_budget_plan_for_mrf(mrf)
        self.assertIn('Multiple', str(ctx.exception))

    def test_a07_multiple_client_level_plans_raise_error(self):
        """Two client-level plans → BudgetReservationError."""
        _budget(self.org, self.site_client, 'CLI1 A07', 'ar-c1-a07')
        _budget(self.org, self.site_client, 'CLI2 A07', 'ar-c2-a07')
        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(BudgetReservationError) as ctx:
            resolve_budget_plan_for_mrf(mrf)
        self.assertIn('Multiple', str(ctx.exception))

    def test_a08_no_match_raises_error(self):
        """No plan for this site/client → BudgetReservationError."""
        # No budgets created for this MRF's client/site
        mrf = _mrf(self.org, self.site2, self.admin)
        with self.assertRaises(BudgetReservationError) as ctx:
            resolve_budget_plan_for_mrf(mrf)
        self.assertIn('No matching', str(ctx.exception))

    def test_a09_non_billable_mrf_raises_error(self):
        """resolver raises for non-billable MRF."""
        mrf = _mrf(self.org, self.site, self.admin, billing_type='non_billable')
        with self.assertRaises(BudgetReservationError):
            resolve_budget_plan_for_mrf(mrf)

    def test_a10_inactive_plan_not_considered(self):
        """inactive budget plan is excluded from resolution."""
        _budget(self.org, self.site_client, 'Inactive A10', 'ar-ia-a10',
                site=self.site, is_active=False)
        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(BudgetReservationError):
            resolve_budget_plan_for_mrf(mrf)

    def test_a11_draft_status_not_considered(self):
        """Budget with status='draft' is excluded (resolver requires status='active')."""
        _budget(self.org, self.site_client, 'Draft A11', 'ar-dr-a11',
                site=self.site, status='draft')
        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(BudgetReservationError):
            resolve_budget_plan_for_mrf(mrf)

    def test_a12_wrong_budget_type_not_considered(self):
        """Budget with budget_type != 'headcount' is excluded."""
        _budget(self.org, self.site_client, 'Manpower A12', 'ar-mp-a12',
                site=self.site, budget_type='manpower')
        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(BudgetReservationError):
            resolve_budget_plan_for_mrf(mrf)


# ─── A13–A18: reserve_budget_for_mrf auto-link ────────────────────────────────

class TestReserveBudgetAutoLink(AutoResolveBase):

    def test_a13_billable_mrf_auto_resolves_and_creates_reservation(self):
        """Billable MRF with no budget_plan gets auto-resolved and reservation created."""
        bp = _budget(self.org, self.site_client, 'Auto BP A13', 'ar-ab-a13',
                     site=self.site, amount='500000.00')
        mrf = _mrf(self.org, self.site, self.admin)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('24000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=5, billing_rate_snapshot=Decimal('24000'))

        reservation = reserve_budget_for_mrf(mrf, actor=self.admin)

        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.status, 'reserved')
        self.assertEqual(reservation.budget_plan_id, bp.pk)

    def test_a14_auto_resolve_attaches_budget_plan_to_mrf(self):
        """After auto-resolve, mrf.budget_plan is saved to DB."""
        bp = _budget(self.org, self.site_client, 'Attach BP A14', 'ar-at-a14',
                     site=self.site, amount='500000.00')
        mrf = _mrf(self.org, self.site, self.admin)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('10000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=2, billing_rate_snapshot=Decimal('10000'))

        self.assertIsNone(mrf.budget_plan_id)
        reserve_budget_for_mrf(mrf, actor=self.admin)
        mrf.refresh_from_db()
        self.assertEqual(mrf.budget_plan_id, bp.pk)

    def test_a15_explicit_budget_plan_wins_no_resolver_called(self):
        """When mrf.budget_plan_id is set, resolver is not called (explicit wins)."""
        explicit_bp = _budget(self.org, self.site_client, 'Explicit A15', 'ar-ex-a15',
                              site=self.site, amount='200000.00')
        # A different plan that would match but should NOT be used
        _budget(self.org, self.site_client, 'Other A15', 'ar-ot-a15', amount='100000.00')

        mrf = _mrf(self.org, self.site, self.admin, budget_plan=explicit_bp)
        _li(mrf, self.job_role, headcount=1, billing_rate_snapshot=Decimal('5000'))

        reservation = reserve_budget_for_mrf(mrf, actor=self.admin)
        self.assertEqual(reservation.budget_plan_id, explicit_bp.pk)

    def test_a16_non_billable_no_budget_plan_returns_none(self):
        """Non-billable MRF with no budget_plan returns None (no reservation)."""
        mrf = _mrf(self.org, self.site, self.admin, billing_type='non_billable')
        result = reserve_budget_for_mrf(mrf, actor=self.admin)
        self.assertIsNone(result)

    def test_a17_auto_resolve_fail_propagates_as_budget_reservation_error(self):
        """No matching plan → BudgetReservationError → workflow start returns 400."""
        # No budget plan exists for this site
        mrf = _mrf(self.org, self.site2, self.admin)
        srr = _srr(self.site2, self.job_role, billing_rate=Decimal('5000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=1, billing_rate_snapshot=Decimal('5000'))

        with self.assertRaises(BudgetReservationError):
            reserve_budget_for_mrf(mrf, actor=self.admin)

    def test_a18_insufficient_budget_after_auto_resolve_raises_error(self):
        """Auto-resolved plan with insufficient funds → BudgetReservationError."""
        # Budget of 100 but request would be 240000
        bp = _budget(self.org, self.site_client, 'Tiny A18', 'ar-ty-a18',
                     site=self.site, amount='100.00')
        mrf = _mrf(self.org, self.site, self.admin)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('24000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=10, billing_rate_snapshot=Decimal('24000'))

        with self.assertRaises(BudgetReservationError) as ctx:
            reserve_budget_for_mrf(mrf, actor=self.admin)
        self.assertIn('Insufficient', str(ctx.exception))


# ─── A19: billing_rate_snapshot auto-fill via API ────────────────────────────

class TestBillingRateSnapshotAutoFill(AutoResolveBase):

    def test_a19_line_item_create_auto_fills_billing_rate_snapshot_from_srr(self):
        """Creating a line item with an SRR auto-fills billing_rate_snapshot."""
        mrf = _mrf(self.org, self.site, self.admin)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('24000.00'), approved_headcount=30)

        resp = self._api(self.admin).post('/api/mrf/line-items/', {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 3,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertEqual(li.billing_rate_snapshot, Decimal('24000.00'))


# ─── A20–A22: MRF serializer resolved budget context ─────────────────────────

class TestMRFSerializerResolvedBudget(AutoResolveBase):

    def test_a20_mrf_detail_includes_resolved_budget_scope_auto_resolved(self):
        """MRF detail shows resolved_budget_plan_id and scope when auto-resolved."""
        bp = _budget(self.org, self.site_client, 'Dept BP A20', 'ar-db20',
                     site=self.site, department=self.dept_a, amount='500000.00')
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_a)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('5000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=2, billing_rate_snapshot=Decimal('5000'))

        resp = self._api(self.admin).get(f'/api/mrf/requests/{mrf.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['resolved_budget_plan_id'], bp.pk)
        self.assertEqual(resp.data['resolved_budget_scope'], 'department')
        self.assertIsNotNone(resp.data['resolved_budget_total_amount'])
        self.assertIsNotNone(resp.data['requested_budget_amount'])

    def test_a21_mrf_detail_resolved_scope_explicit_when_manually_set(self):
        """When mrf.budget_plan is explicitly set, resolved_budget_scope='explicit'."""
        bp = _budget(self.org, self.site_client, 'Explicit A21', 'ar-ex21',
                     site=self.site, amount='100000.00')
        mrf = _mrf(self.org, self.site, self.admin, budget_plan=bp)
        _li(mrf, self.job_role, headcount=1, billing_rate_snapshot=Decimal('1000'))

        resp = self._api(self.admin).get(f'/api/mrf/requests/{mrf.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['resolved_budget_scope'], 'explicit')
        self.assertEqual(resp.data['resolved_budget_plan_id'], bp.pk)

    def test_a22_non_billable_mrf_resolved_scope_none(self):
        """Non-billable MRF with no budget_plan returns resolved_budget_scope='none'."""
        mrf = _mrf(self.org, self.site, self.admin, billing_type='non_billable')
        _li(mrf, self.job_role, headcount=1)

        resp = self._api(self.admin).get(f'/api/mrf/requests/{mrf.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['resolved_budget_scope'], 'none')
        self.assertIsNone(resp.data['resolved_budget_plan_id'])


# ─── A23: Workflow task drawer budget context ─────────────────────────────────

class TestWorkflowDrawerBudgetContext(AutoResolveBase):

    def test_a23_my_tasks_detail_includes_resolved_budget_context(self):
        """My-tasks drawer includes resolved budget context in target.mrf."""
        bp = _budget(self.org, self.site_client, 'Drawer BP A23', 'ar-dr23',
                     site=self.site, amount='800000.00')
        mrf = _mrf(self.org, self.site, self.admin)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('24000'), approved_headcount=30)
        li = _li(mrf, self.job_role, srr=srr, headcount=5, billing_rate_snapshot=Decimal('24000'))
        mrf.budget_plan = bp
        mrf.save(update_fields=['budget_plan'])

        instance = start_mrf_workflow(mrf, actor=self.admin)
        step = instance.steps.filter(status='active').first()

        resp = self._api(self.approver).get(f'/api/workflow/my-tasks/{step.pk}/')
        self.assertEqual(resp.status_code, 200)
        mrf_data = resp.data['target']['mrf']
        self.assertIn('resolved_budget_plan_id', mrf_data)
        self.assertIn('resolved_budget_scope', mrf_data)
        self.assertIn('resolved_budget_total_amount', mrf_data)
        self.assertIn('requested_budget_amount', mrf_data)
        self.assertIn('budget_after_request_available_amount', mrf_data)
        self.assertEqual(mrf_data['resolved_budget_plan_id'], bp.pk)
        self.assertEqual(mrf_data['resolved_budget_scope'], 'explicit')


# ─── A24: Readiness endpoint with auto-resolution ────────────────────────────

class TestReadinessBudgetContext(AutoResolveBase):

    def test_a24_readiness_includes_budget_plan_from_auto_resolver(self):
        """Readiness endpoint shows resolved budget context including plan name and scope."""
        bp = _budget(self.org, self.site_client, 'Ready BP A24', 'ar-rb24',
                     site=self.site, department=self.dept_b, amount='500000.00')
        mrf = _mrf(self.org, self.site, self.admin, required_department=self.dept_b)
        srr = _srr(self.site, self.job_role, billing_rate=Decimal('24000'), approved_headcount=30)
        _li(mrf, self.job_role, srr=srr, headcount=5, billing_rate_snapshot=Decimal('24000'))

        resp = self._api(self.admin).get(f'/api/mrf/requests/{mrf.pk}/readiness/')
        self.assertEqual(resp.status_code, 200, resp.data)

        budget = resp.data.get('budget')
        self.assertIsNotNone(budget)
        self.assertEqual(budget['plan_id'], bp.pk)
        self.assertEqual(budget['plan_name'], bp.name)
        self.assertEqual(budget['scope'], 'department')
        self.assertIsNotNone(budget['total_amount'])
        self.assertIsNotNone(budget['available_amount'])
        self.assertIsNotNone(budget['requested_amount'])
        self.assertIsNotNone(budget['available_after_request'])
        self.assertTrue(budget['ok'])
        self.assertTrue(resp.data['ok'])

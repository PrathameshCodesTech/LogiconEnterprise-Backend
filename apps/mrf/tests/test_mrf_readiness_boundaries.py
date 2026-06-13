"""
apps/mrf/tests/test_mrf_readiness_boundaries.py

Phase MRF-Readiness-Boundaries-A — 20 tests.

Covers:
  R01-R08  headcount service (get_billable_headcount_usage, billable line item rules)
  R09-R15  budget boundary rules (nature, department scope, sufficiency, totals)
  R16-R18  GET /api/mrf/requests/{id}/readiness/ endpoint
  R19-R20  workflow start gate + reservation hardening
"""

from decimal import Decimal
from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.exceptions import BudgetReservationError
from apps.budgets.models import BudgetPlan, BudgetReservation
from apps.budgets.services import get_budget_plan_totals, reserve_budget_for_mrf
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.mrf.services import check_mrf_readiness, get_billable_headcount_usage
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


# ─── URL helpers ──────────────────────────────────────────────────────────────

def _readiness_url(mrf_id):
    return f'/api/mrf/requests/{mrf_id}/readiness/'


def _start_url(mrf_id):
    return f'/api/workflow/mrf/{mrf_id}/start/'


# ─── Base class ───────────────────────────────────────────────────────────────

class ReadinessBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='RD Test Org', code='rd-test')

        cls.n_co = ScopeNode.objects.create(
            org=cls.org, code='rd-test', name='rd-test', node_type='company',
            parent=None, depth=0, path='rd-test', is_active=True,
        )
        cls.n_cl = ScopeNode.objects.create(
            org=cls.org, code='rd-client', name='rd-client', node_type='client',
            parent=cls.n_co, depth=1, path='rd-test/rd-client', is_active=True,
        )
        cls.n_site = ScopeNode.objects.create(
            org=cls.org, code='rd-site', name='rd-site', node_type='site',
            parent=cls.n_cl, depth=2, path='rd-test/rd-client/rd-site', is_active=True,
        )

        cls.client_obj = Client.objects.create(
            org=cls.org, name='RD Client', code='rd-client', scope_node=cls.n_cl,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client_obj, scope_node=cls.n_site,
            name='RD Site', code='rd-site',
        )
        cls.site2 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_obj,
            name='RD Site 2', code='rd-site2',
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard RD', code='rd-guard', skill_category='unskilled',
        )

        cls.srr = SiteRoleRequirement.objects.create(
            site=cls.site, job_role=cls.job_role,
            approved_headcount=10, billing_type='billable',
            effective_from=date.today(), is_active=True,
        )
        cls.srr2 = SiteRoleRequirement.objects.create(
            site=cls.site2, job_role=cls.job_role,
            approved_headcount=10, billing_type='billable',
            effective_from=date.today(), is_active=True,
        )

        cls.dept = Department.objects.create(
            org=cls.org, name='RD Operations', code='rd-ops',
        )

        cls.role_hr_admin = AccessRole.objects.get_or_create(
            org=cls.org, code='hr_admin', defaults={'name': 'HR Admin'},
        )[0]
        cls.role_no_access = AccessRole.objects.get_or_create(
            org=cls.org, code='field_supervisor', defaults={'name': 'Field Supervisor'},
        )[0]
        bootstrap_role_permissions(cls.role_hr_admin)
        bootstrap_role_permissions(cls.role_no_access)

        cls.hr_admin = User.objects.create_user('rd_hradmin', password='pass')
        cls.hr_admin.org = cls.org
        cls.hr_admin.save()
        UserRoleAssignment.objects.create(
            user=cls.hr_admin, role=cls.role_hr_admin, scope_node=cls.n_co,
        )

        cls.no_mrf_user = User.objects.create_user('rd_no_mrf', password='pass')
        cls.no_mrf_user.org = cls.org
        cls.no_mrf_user.save()
        UserRoleAssignment.objects.create(
            user=cls.no_mrf_user, role=cls.role_no_access, scope_node=cls.n_co,
        )

    def setUp(self):
        self.api = APIClient()

    def _mrf(self, billing_type='billable', **kwargs):
        defaults = {
            'org': self.org,
            'site': self.site,
            'requested_by': self.hr_admin,
            'mrf_type': 'new_hiring',
            'status': 'draft',
            'billing_type': billing_type,
        }
        defaults.update(kwargs)
        return ManpowerRequest.objects.create(**defaults)

    def _li(self, mrf, srr=None, headcount=2, **kwargs):
        return MRFLineItem.objects.create(
            mrf=mrf, job_role=self.job_role,
            site_role_requirement=srr, headcount=headcount, **kwargs,
        )

    def _bp(self, nature='billable', amount=1000, code=None, **kwargs):
        if code is None:
            code = f'bp-{nature}-{id(self)}'
        defaults = {
            'org': self.org,
            'name': f'Budget {code}',
            'code': code,
            'budget_nature': nature,
            'budget_type': 'general',
            'period_start': date.today(),
            'amount': Decimal(str(amount)),
            'status': 'active',
            'is_active': True,
        }
        defaults.update(kwargs)
        return BudgetPlan.objects.create(**defaults)


# ─── R01-R08: Headcount service tests ─────────────────────────────────────────

class TestHeadcountService(ReadinessBase):

    def test_R01_no_line_items_fails(self):
        mrf = self._mrf()
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('no line items', r['errors'][0].lower())

    def test_R02_billable_missing_srr_fails(self):
        mrf = self._mrf()
        self._li(mrf, srr=None, headcount=2)
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('SiteRoleRequirement' in e for e in r['errors']))

    def test_R03_billable_srr_wrong_site_fails(self):
        mrf = self._mrf()
        self._li(mrf, srr=self.srr2, headcount=2)
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('different site' in e for e in r['errors']))

    def test_R04_billable_headcount_exceeded_fails(self):
        # Active MRF consumes 9 of 10 approved headcount
        other_mrf = self._mrf(status='submitted')
        self._li(other_mrf, srr=self.srr, headcount=9)

        # New MRF requests 2 but only 1 is left
        mrf = self._mrf()
        self._li(mrf, srr=self.srr, headcount=2)

        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('headcount' in e.lower() for e in r['errors']))

    def test_R05_billable_ok_passes(self):
        mrf = self._mrf()
        self._li(mrf, srr=self.srr, headcount=3)
        r = check_mrf_readiness(mrf)
        self.assertTrue(r['ok'], r['errors'])
        self.assertEqual(r['errors'], [])
        li_info = list(r['billable_headcount'].values())[0]
        self.assertEqual(li_info['requested'], 3)
        self.assertEqual(li_info['already_allocated'], 0)
        self.assertEqual(li_info['approved_headcount'], 10)
        self.assertEqual(li_info['available'], 10)

    def test_R06_headcount_usage_counts_active_statuses(self):
        for status_val in ('submitted', 'hr_review', 'approved'):
            active_mrf = self._mrf(status=status_val)
            self._li(active_mrf, srr=self.srr, headcount=1)

        usage = get_billable_headcount_usage(self.site, self.job_role)
        self.assertEqual(usage, 3)

    def test_R07_headcount_usage_ignores_draft_rejected_cancelled(self):
        for status_val in ('draft', 'rejected', 'cancelled'):
            inactive_mrf = self._mrf(status=status_val)
            self._li(inactive_mrf, srr=self.srr, headcount=5)

        usage = get_billable_headcount_usage(self.site, self.job_role)
        self.assertEqual(usage, 0)

    def test_R08_headcount_usage_excludes_mrf(self):
        mrf = self._mrf(status='submitted')
        self._li(mrf, srr=self.srr, headcount=4)

        self.assertEqual(get_billable_headcount_usage(self.site, self.job_role), 4)
        self.assertEqual(get_billable_headcount_usage(self.site, self.job_role, exclude_mrf=mrf), 0)


# ─── R09-R15: Budget boundary tests ───────────────────────────────────────────

class TestBudgetBoundaries(ReadinessBase):

    def test_R09_billable_mrf_with_non_billable_budget_fails(self):
        bp = self._bp('non_billable', code='bp-r09')
        mrf = self._mrf(billing_type='billable', budget_plan=bp)
        self._li(mrf, srr=self.srr, headcount=2)
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('billable budget' in e.lower() for e in r['errors']))

    def test_R10_non_billable_mrf_with_billable_budget_fails(self):
        bp = self._bp('billable', code='bp-r10')
        mrf = self._mrf(billing_type='non_billable', budget_plan=bp)
        self._li(mrf, headcount=2)
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('non-billable budget' in e.lower() for e in r['errors']))

    def test_R11_non_billable_dept_budget_mismatch_fails(self):
        dept2 = Department.objects.create(org=self.org, name='Other Dept', code='other-dept-r11')
        bp = self._bp('non_billable', code='bp-r11', department=dept2, budget_type='hiring')
        mrf = self._mrf(
            billing_type='non_billable', budget_plan=bp,
            requesting_department=self.dept, required_department=self.dept,
        )
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('department' in e.lower() for e in r['errors']))

    def test_R12_non_billable_dept_budget_match_passes(self):
        bp = self._bp(
            'non_billable',
            amount=10000,
            code='bp-r12',
            department=self.dept,
            budget_type='hiring',
        )
        mrf = self._mrf(
            billing_type='non_billable', budget_plan=bp,
            requesting_department=self.dept, required_department=self.dept,
        )
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))
        r = check_mrf_readiness(mrf)
        self.assertTrue(r['ok'], r['errors'])

    def test_R12a_non_billable_non_hiring_budget_fails(self):
        bp = self._bp(
            'non_billable',
            amount=10000,
            code='bp-r12a',
            department=self.dept,
            budget_type='general',
        )
        mrf = self._mrf(
            billing_type='non_billable', budget_plan=bp,
            requesting_department=self.dept, required_department=self.dept,
        )
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('internal hiring budget' in e.lower() for e in r['errors']))

    def test_R12b_non_billable_missing_required_department_fails(self):
        mrf = self._mrf(billing_type='non_billable', requesting_department=self.dept)
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))

        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('required department' in e.lower() for e in r['errors']))

    def test_R12c_non_billable_auto_resolves_required_department_budget(self):
        bp = self._bp(
            'non_billable',
            amount=10000,
            code='bp-r12c',
            department=self.dept,
            budget_type='hiring',
        )
        mrf = self._mrf(
            billing_type='non_billable',
            requesting_department=self.dept,
            required_department=self.dept,
        )
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))

        r = check_mrf_readiness(mrf)
        self.assertTrue(r['ok'], r['errors'])
        self.assertEqual(r['budget']['plan_id'], bp.pk)
        self.assertEqual(r['budget']['scope'], 'department')

    def test_R12d_non_billable_insufficient_department_budget_fails(self):
        self._bp(
            'non_billable',
            amount=100,
            code='bp-r12d',
            department=self.dept,
            budget_type='hiring',
        )
        mrf = self._mrf(
            billing_type='non_billable',
            requesting_department=self.dept,
            required_department=self.dept,
        )
        self._li(mrf, headcount=2, budget_max=Decimal('500.00'))

        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('insufficient budget' in e.lower() for e in r['errors']))

    def test_R13_insufficient_budget_fails(self):
        bp = self._bp('billable', amount=100, code='bp-r13')
        mrf = self._mrf(billing_type='billable', budget_plan=bp)
        self._li(mrf, srr=self.srr, headcount=2, budget_max=Decimal('200.00'))
        r = check_mrf_readiness(mrf)
        self.assertFalse(r['ok'])
        self.assertTrue(any('insufficient budget' in e.lower() for e in r['errors']))
        self.assertIsNotNone(r['budget'])
        self.assertFalse(r['budget']['sufficient'])

    def test_R14_sufficient_budget_passes(self):
        bp = self._bp('billable', amount=1000, code='bp-r14')
        mrf = self._mrf(billing_type='billable', budget_plan=bp)
        self._li(mrf, srr=self.srr, headcount=2, budget_max=Decimal('200.00'))
        r = check_mrf_readiness(mrf)
        self.assertTrue(r['ok'], r['errors'])
        self.assertIsNotNone(r['budget'])
        self.assertTrue(r['budget']['sufficient'])

    def test_R15_get_budget_plan_totals_correct(self):
        bp = self._bp('billable', amount=1000, code='bp-r15')
        mrf1 = self._mrf()
        mrf2 = self._mrf()
        mrf3 = self._mrf()
        BudgetReservation.objects.create(
            org=self.org, budget_plan=bp, mrf=mrf1,
            amount=Decimal('300.00'), status='reserved',
        )
        BudgetReservation.objects.create(
            org=self.org, budget_plan=bp, mrf=mrf2,
            amount=Decimal('200.00'), status='committed',
        )
        BudgetReservation.objects.create(
            org=self.org, budget_plan=bp, mrf=mrf3,
            amount=Decimal('50.00'), status='released',
        )
        totals = get_budget_plan_totals(bp)
        self.assertEqual(totals['total_amount'], Decimal('1000.00'))
        self.assertEqual(totals['reserved_amount'], Decimal('300.00'))
        self.assertEqual(totals['committed_amount'], Decimal('200.00'))
        # available = 1000 - 300 - 200 = 500 (released does not reduce available)
        self.assertEqual(totals['available_amount'], Decimal('500.00'))


# ─── R16-R18: Readiness endpoint tests ────────────────────────────────────────

class TestReadinessEndpoint(ReadinessBase):

    def test_R16_readiness_endpoint_ok_for_ready_mrf(self):
        mrf = self._mrf()
        self._li(mrf, srr=self.srr, headcount=3)
        self.api.force_authenticate(user=self.hr_admin)
        resp = self.api.get(_readiness_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['ok'])
        self.assertEqual(resp.data['errors'], [])
        self.assertIn('billable_headcount', resp.data)
        self.assertIn('budget', resp.data)

    def test_R17_readiness_endpoint_shows_errors_for_not_ready_mrf(self):
        mrf = self._mrf()  # no line items
        self.api.force_authenticate(user=self.hr_admin)
        resp = self.api.get(_readiness_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertGreater(len(resp.data['errors']), 0)
        self.assertIn('warnings', resp.data)

    def test_R18_readiness_endpoint_requires_mrf_read(self):
        mrf = self._mrf()
        self._li(mrf, srr=self.srr, headcount=3)
        self.api.force_authenticate(user=self.no_mrf_user)
        resp = self.api.get(_readiness_url(mrf.pk))
        self.assertEqual(resp.status_code, 403)


# ─── R19-R20: Workflow gate + reservation hardening ───────────────────────────

class TestWorkflowGate(ReadinessBase):

    def test_R19_workflow_start_blocked_when_not_ready(self):
        # MRF with no line items — readiness check will fail
        mrf = self._mrf()
        self.api.force_authenticate(user=self.hr_admin)
        resp = self.api.post(_start_url(mrf.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('errors', resp.data)
        self.assertGreater(len(resp.data['errors']), 0)
        self.assertIn('detail', resp.data)

    def test_R20_reservation_hardening_raises_on_insufficient_budget(self):
        bp = self._bp('billable', amount=100, code='bp-r20')
        # Another MRF has committed 80 → available = 20
        other_mrf = self._mrf(billing_type='billable', budget_plan=bp)
        BudgetReservation.objects.create(
            org=self.org, budget_plan=bp, mrf=other_mrf,
            amount=Decimal('80.00'), status='committed',
        )
        # This MRF requires 50 (> 20 available)
        mrf = self._mrf(billing_type='billable', budget_plan=bp)
        self._li(mrf, srr=self.srr, headcount=2, budget_min=Decimal('50.00'))

        with self.assertRaises(BudgetReservationError):
            reserve_budget_for_mrf(mrf, self.hr_admin)

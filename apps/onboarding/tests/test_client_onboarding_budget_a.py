"""
apps/onboarding/tests/test_client_onboarding_budget_a.py

Phase Client-Onboarding-Budget-A tests.

Scenarios:
  Proposed budget CRUD / validation:
  1.  Create proposed budget for new-client onboarding — 201
  2.  Duplicate active code within same request → 400
  3.  Same code on different request → both 201
  4.  Site-level budget without proposed_site → 400
  5.  Department-level budget without proposed_department → 400
  6.  proposed_site from a different request → 400
  7.  proposed_department from a different request → 400
  8.  Negative / zero amount → 400
  9.  period_end before period_start → 400
  10. proposed_budgets list present on onboarding detail endpoint

  Lifecycle / editability:
  11. Proposed budget CRUD blocked once workflow is active (in_review)

  Finalization — budget creation:
  12. Final approval creates real BudgetPlan rows
  13. Client-level proposed budget → BudgetPlan.client=created_client, site=None, dept=None
  14. Site-level proposed budget → BudgetPlan.site=created_site
  15. Department-level proposed budget → BudgetPlan.department=created_dept

  Finalization — negative / idempotency:
  16. Rejection creates no real BudgetPlan
  17. Finalization idempotency: second call does not duplicate budgets

  Regression:
  18. Existing new_site_expansion budget_plan field still works
"""

import datetime
import decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.budgets.models import BudgetPlan
from apps.onboarding.models import ClientOnboardingProposedBudget, ClientOnboardingRequest
from apps.onboarding.services import finalize_client_onboarding_request
from apps.onboarding.tests.test_client_onboarding_finalization import FinalizationTestBase
from apps.sites.models import Client


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _today():
    return datetime.date.today()


def _next_year():
    return _today().replace(year=_today().year + 1)


# ─── Base ─────────────────────────────────────────────────────────────────────

class BudgetTestBase(FinalizationTestBase):
    """Extends FinalizationTestBase with proposed-budget helpers."""

    def _proposed_budget_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-budgets/'

    def _proposed_budget_detail_url(self, req_pk, pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-budgets/{pk}/'

    def _budget_payload(self, **overrides):
        defaults = dict(
            name='Onboarding Budget',
            code='ob-budget-1',
            budget_nature='billable',
            budget_type='onboarding',
            scope_level='client',
            amount='500000.00',
            currency='INR',
            period_start=str(_today()),
            period_end=str(_next_year()),
        )
        defaults.update(overrides)
        return defaults

    def _proposed_budget(self, req, code='pb-1', scope_level='client',
                         proposed_site=None, proposed_department=None):
        return ClientOnboardingProposedBudget.objects.create(
            request=req,
            name=f'Budget {code}',
            code=code,
            budget_nature='billable',
            budget_type='onboarding',
            scope_level=scope_level,
            proposed_site=proposed_site,
            proposed_department=proposed_department,
            amount=decimal.Decimal('100000.00'),
            currency='INR',
            period_start=_today(),
            period_end=_next_year(),
            is_active=True,
        )


# ─── Scenarios 1–10: CRUD & validation ───────────────────────────────────────

class TestProposedBudgetCRUD(BudgetTestBase):

    def test_01_create_proposed_budget_for_new_client(self):
        """Create proposed budget for new-client onboarding → 201."""
        req = self._new_client_request()
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['code'], 'ob-budget-1')
        self.assertEqual(resp.data['scope_level'], 'client')
        self.assertIsNone(resp.data['proposed_site'])

    def test_02_duplicate_active_code_blocked(self):
        """Duplicate active budget code within same request → 400."""
        req = self._new_client_request()
        self._proposed_budget(req, code='dup-budget')
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(code='dup-budget'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', str(resp.data))

    def test_03_same_code_allowed_on_different_request(self):
        """Same code on two different onboarding requests is valid."""
        req1 = self._new_client_request(proposed_client_code='nc-bud-1', proposed_client_name='NC1')
        req2 = self._new_client_request(proposed_client_code='nc-bud-2', proposed_client_name='NC2')
        self._login(self.admin)

        resp1 = self.api.post(
            self._proposed_budget_url(req1.pk),
            self._budget_payload(code='shared-code'),
            format='json',
        )
        resp2 = self.api.post(
            self._proposed_budget_url(req2.pk),
            self._budget_payload(code='shared-code'),
            format='json',
        )
        self.assertEqual(resp1.status_code, 201, resp1.data)
        self.assertEqual(resp2.status_code, 201, resp2.data)

    def test_04_site_level_without_proposed_site_rejected(self):
        """scope_level=site without proposed_site → 400."""
        req = self._new_client_request()
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(scope_level='site'),  # no proposed_site
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_site', str(resp.data))

    def test_05_department_level_without_proposed_department_rejected(self):
        """scope_level=department without proposed_department → 400."""
        req = self._new_client_request()
        p_site = self._proposed_site(req)
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(
                scope_level='department',
                proposed_site=p_site.pk,
                # no proposed_department
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_department', str(resp.data))

    def test_06_wrong_request_proposed_site_rejected(self):
        """proposed_site from a different onboarding request → 400."""
        req1 = self._new_client_request(proposed_client_code='nc-r1', proposed_client_name='R1')
        req2 = self._new_client_request(proposed_client_code='nc-r2', proposed_client_name='R2')
        p_site_req2 = self._proposed_site(req2, code='other-site')
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req1.pk),
            self._budget_payload(
                scope_level='site',
                proposed_site=p_site_req2.pk,
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_site', str(resp.data))

    def test_07_wrong_request_proposed_department_rejected(self):
        """proposed_department from a different onboarding request → 400."""
        req1 = self._new_client_request(proposed_client_code='nc-dr1', proposed_client_name='DR1')
        req2 = self._new_client_request(proposed_client_code='nc-dr2', proposed_client_name='DR2')
        p_site_req1 = self._proposed_site(req1, code='dept-site-1')
        p_site_req2 = self._proposed_site(req2, code='dept-site-2')
        p_dept_req2 = self._proposed_dept(req2, p_site=p_site_req2, code='other-dept')
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req1.pk),
            self._budget_payload(
                scope_level='department',
                proposed_site=p_site_req1.pk,
                proposed_department=p_dept_req2.pk,
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_department', str(resp.data))

    def test_08_negative_and_zero_amount_rejected(self):
        """Amount ≤ 0 → 400."""
        req = self._new_client_request()
        self._login(self.admin)

        for bad_amount in ['0', '-100']:
            resp = self.api.post(
                self._proposed_budget_url(req.pk),
                self._budget_payload(amount=bad_amount, code=f'bad-amt-{bad_amount}'),
                format='json',
            )
            self.assertEqual(resp.status_code, 400, f"Expected 400 for amount={bad_amount}")
            self.assertIn('amount', str(resp.data))

    def test_09_invalid_period_rejected(self):
        """period_end before period_start → 400."""
        req = self._new_client_request()
        self._login(self.admin)

        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(
                period_start='2025-12-31',
                period_end='2025-01-01',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('period_end', str(resp.data))

    def test_10_proposed_budgets_in_detail_serializer(self):
        """proposed_budgets list is present on the onboarding detail endpoint."""
        req = self._new_client_request()
        self._proposed_budget(req, code='detail-bud-1')
        self._proposed_budget(req, code='detail-bud-2')
        self._login(self.admin)

        resp = self.api.get(f'/api/onboarding/client-requests/{req.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('proposed_budgets', resp.data)
        self.assertEqual(len(resp.data['proposed_budgets']), 2)
        codes = {b['code'] for b in resp.data['proposed_budgets']}
        self.assertEqual(codes, {'detail-bud-1', 'detail-bud-2'})


# ─── Scenario 11: Editability after workflow starts ───────────────────────────

class TestProposedBudgetEditability(BudgetTestBase):

    def test_11_crud_blocked_when_workflow_active(self):
        """Cannot add/edit proposed budget when request status is 'in_review'."""
        from apps.workflow.services import start_client_onboarding_workflow

        req = self._new_client_request()
        start_client_onboarding_workflow(req, actor=self.admin)
        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

        self._login(self.admin)
        resp = self.api.post(
            self._proposed_budget_url(req.pk),
            self._budget_payload(),
            format='json',
        )
        self.assertEqual(resp.status_code, 403)


# ─── Scenarios 12–15: Finalization creates real BudgetPlan rows ──────────────

class TestBudgetFinalization(BudgetTestBase):

    def test_12_final_approval_creates_real_budget_plans(self):
        """Full approval → one BudgetPlan per active proposed budget."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-12',
            proposed_client_name='BP Corp 12',
        )
        self._proposed_budget(req, code='budget-a')
        self._proposed_budget(req, code='budget-b')

        before = BudgetPlan.objects.filter(org=self.org).count()
        self._full_approve(req)
        after = BudgetPlan.objects.filter(org=self.org).count()

        self.assertEqual(after - before, 2)

    def test_13_client_level_budget_maps_to_created_client(self):
        """Client-level proposed budget → BudgetPlan.client=created_client, site=None."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-13',
            proposed_client_name='BP Corp 13',
        )
        self._proposed_budget(req, code='cl-bud', scope_level='client')

        self._full_approve(req)
        req.refresh_from_db()

        bp = BudgetPlan.objects.get(org=self.org, code='cl-bud')
        self.assertEqual(bp.client_id, req.created_client_id)
        self.assertIsNone(bp.site_id)
        self.assertIsNone(bp.department_id)

    def test_14_site_level_budget_maps_to_created_site(self):
        """Site-level proposed budget → BudgetPlan.site=real created site."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-14',
            proposed_client_name='BP Corp 14',
        )
        p_site = self._proposed_site(req, code='site-14')
        self._proposed_budget(req, code='site-bud', scope_level='site', proposed_site=p_site)

        self._full_approve(req)
        req.refresh_from_db()

        from apps.sites.models import SiteProfile
        real_site = SiteProfile.objects.get(org=self.org, code='site-14')
        bp = BudgetPlan.objects.get(org=self.org, code='site-bud')
        self.assertEqual(bp.site_id, real_site.pk)
        self.assertEqual(bp.client_id, req.created_client_id)
        self.assertIsNone(bp.department_id)

    def test_15_department_level_budget_maps_to_created_department(self):
        """Department-level proposed budget → BudgetPlan.department=real created department."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-15',
            proposed_client_name='BP Corp 15',
        )
        p_site = self._proposed_site(req, code='site-15')
        p_dept = self._proposed_dept(req, p_site=p_site, code='dept-15', scope_level='site')
        self._proposed_budget(
            req, code='dept-bud', scope_level='department',
            proposed_site=p_site, proposed_department=p_dept,
        )

        self._full_approve(req)
        req.refresh_from_db()

        from apps.core.models import Department
        from apps.sites.models import SiteProfile
        real_site = SiteProfile.objects.get(org=self.org, code='site-15')
        real_dept = Department.objects.get(org=self.org, code='dept-15')
        bp = BudgetPlan.objects.get(org=self.org, code='dept-bud')
        self.assertEqual(bp.department_id, real_dept.pk)
        self.assertEqual(bp.site_id, real_site.pk)
        self.assertEqual(bp.client_id, req.created_client_id)


# ─── Scenario 16: Rejection creates no BudgetPlan ────────────────────────────

class TestBudgetRejection(BudgetTestBase):

    def test_16_rejection_creates_no_real_budget(self):
        """Rejected onboarding → no BudgetPlan created from proposed budgets."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-16',
            proposed_client_name='BP Corp 16',
        )
        self._proposed_budget(req, code='reject-bud')

        before = BudgetPlan.objects.filter(org=self.org).count()
        self._reject_final(req)
        after = BudgetPlan.objects.filter(org=self.org).count()

        self.assertEqual(after, before)
        req.refresh_from_db()
        self.assertEqual(req.status, 'rejected')


# ─── Scenario 17: Idempotency ─────────────────────────────────────────────────

class TestBudgetFinalizationIdempotency(BudgetTestBase):

    def test_17_idempotency_does_not_duplicate_budgets(self):
        """Calling finalize a second time after success does not create more BudgetPlans."""
        req = self._new_client_request(
            proposed_client_code='bp-corp-17',
            proposed_client_name='BP Corp 17',
        )
        self._proposed_budget(req, code='idem-bud')

        self._full_approve(req)
        req.refresh_from_db()
        self.assertEqual(req.finalization_status, 'finalized')

        count_after_first = BudgetPlan.objects.filter(org=self.org, code='idem-bud').count()
        self.assertEqual(count_after_first, 1)

        # Second call is a no-op
        finalize_client_onboarding_request(req, actor=self.admin)
        self.assertEqual(
            BudgetPlan.objects.filter(org=self.org, code='idem-bud').count(), 1,
        )


# ─── Scenario 18: new_site_expansion budget_plan field regression ─────────────

class TestSiteExpansionBudgetRegression(BudgetTestBase):

    def test_18_new_site_expansion_budget_plan_field_still_works(self):
        """
        ClientOnboardingRequest.budget_plan (FK to BudgetPlan) still works for
        new_site_expansion type; proposed_budgets are independent of this field.
        """
        from apps.budgets.models import BudgetPlan as BP

        real_budget = BP.objects.create(
            org=self.org,
            name='Expansion Budget',
            code='exp-bud-18',
            budget_nature='billable',
            budget_type='onboarding',
            client=self.client_a,
            period_start=_today(),
            amount=decimal.Decimal('250000.00'),
            status='active',
            is_active=True,
        )

        self._login(self.admin)
        resp = self.api.post('/api/onboarding/client-requests/', {
            'onboarding_type': 'new_site_expansion',
            'client': self.client_a.pk,
            'budget_plan': real_budget.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], real_budget.pk)

        # Retrieve confirms budget_plan display fields are present
        req_id = resp.data['id']
        detail = self.api.get(f'/api/onboarding/client-requests/{req_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['budget_plan_code'], 'exp-bud-18')
        self.assertEqual(detail.data['proposed_budgets'], [])

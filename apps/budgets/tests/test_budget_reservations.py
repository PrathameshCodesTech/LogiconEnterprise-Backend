"""
apps/budgets/tests/test_budget_reservations.py

Phase Budget-Reservation-A — backend MRF budget reservation tests.

Test cases:

  Model / service calculation (5):
  1.  calculate_mrf_reservation_amount uses budget_max.
  2.  Falls back to budget_min.
  3.  Falls back to headcount * billing_rate_snapshot.
  4.  Missing all amount sources raises BudgetReservationError.
  5.  No matching line items raises BudgetReservationError.

  Reserve on workflow start (5):
  6.  Draft MRF has no reservation.
  7.  Starting workflow creates reserved reservation.
  8.  Reservation amount matches line item sum.
  9.  Starting workflow rolls back if reservation calculation fails.
  10. Starting duplicate workflow does not create duplicate reservation.

  Commit / release on workflow outcome (5):
  11. Partial approval (HR only) keeps reservation reserved.
  12. Final approval (finance) changes reservation to committed.
  13. Final rejection changes reservation to released.
  14. Reject-back-to-HR keeps reservation reserved (workflow stays active).
  15. Request-changes-back-to-HR keeps reservation reserved.

  Budget totals on BudgetPlan serializer (4):
  16. reserved_amount reflects sum of reserved reservations.
  17. committed_amount reflects sum of committed reservations.
  18. available_amount = amount - reserved - committed.
  19. Released reservations do not reduce available_amount.

  Regression (3):
  20. MRF with no budget_plan starts workflow without error (no reservation created).
  21. Existing budget API tests still pass after changes.
  22. Existing workflow engine tests still pass after changes.
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
from apps.budgets.services import (
    calculate_mrf_reservation_amount,
    commit_mrf_budget_reservations,
    release_mrf_budget_reservations,
    reserve_budget_for_mrf,
)
from apps.core.models import Organization, ScopeNode
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowInstance, WorkflowStepTemplate, WorkflowTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
)
from apps.workflow.services import act_on_step, start_mrf_workflow


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


def _budget(org, name, code, client, amount='50000.00'):
    return BudgetPlan.objects.create(
        org=org, name=name, code=code,
        budget_nature='billable', budget_type='manpower',
        client=client,
        period_start=datetime.date(2026, 1, 1),
        amount=Decimal(amount),
        currency='INR',
        status='active',
        is_active=True,
    )


def _template(org, code, trigger_type='mrf'):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code, trigger_type=trigger_type,
        version=1, is_active=True,
    )


def _step(template, order, code, on_approve_next='END', on_reject_target='',
          on_request_changes_target=''):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        on_approve_next=on_approve_next,
        on_reject_target=on_reject_target,
        on_request_changes_target=on_request_changes_target,
        requires_comment_on_reject=True,
        requires_comment_on_request_changes=True,
    )


def _mapping(org, template):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='mrf', template=template, is_active=True,
    )


def _sac(org, step_code, named_user):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='mrf', step_code=step_code,
        assignment_mode='named_user', named_user=named_user, is_active=True,
    )


def _mrf(org, site, actor, budget_plan=None, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=actor,
        mrf_type='new_hiring', status=status,
        billing_type='billable',
        budget_plan=budget_plan,
    )


def _line_item(mrf, job_role, headcount=1, budget_max=None, budget_min=None,
               billing_rate_snapshot=None, budget_plan=None):
    return MRFLineItem.objects.create(
        mrf=mrf,
        job_role=job_role,
        headcount=headcount,
        budget_max=budget_max,
        budget_min=budget_min,
        billing_rate_snapshot=billing_rate_snapshot,
        budget_plan=budget_plan,
    )


# ─── Base class ───────────────────────────────────────────────────────────────

class BudgetReservationTestBase(TestCase):
    """
    Shared fixture for all budget-reservation tests.

    Layout:
      - org with client + site
      - 2-step workflow template (hr_review → finance_review → END)
      - org-level template mapping + SACs
      - admin user with all capabilities
      - two approver users (hr_approver, finance_approver)
      - a billable BudgetPlan (amount=50000)
      - a JobRole for line items
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='BR Test Org', code='br-org')

        cls.n_company = _node(cls.org, 'br-org', 'company', None, 0, 'br-org')
        cls.n_client = _node(cls.org, 'br-client', 'client', cls.n_company, 1, 'br-org/br-client')
        cls.n_site = _node(cls.org, 'br-site', 'site', cls.n_client, 2, 'br-org/br-client/br-site')

        cls.client_obj = Client.objects.create(
            org=cls.org, name='BR Client', code='br-client', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client_obj,
            name='BR Site', code='br-site', scope_node=cls.n_site,
        )

        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = _user('br_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.hr_approver = _user('br_hr', org=cls.org)
        cls.finance_approver = _user('br_finance', org=cls.org)
        cls.actor = _user('br_actor', org=cls.org)

        # 2-step workflow
        cls.template = _template(cls.org, 'br-mrf-tpl')
        cls.step1 = _step(cls.template, 1, 'hr_review', on_approve_next='finance_review')
        cls.step2 = _step(cls.template, 2, 'finance_review', on_approve_next='END')
        _mapping(cls.org, cls.template)
        _sac(cls.org, 'hr_review', cls.hr_approver)
        _sac(cls.org, 'finance_review', cls.finance_approver)

        cls.budget = _budget(cls.org, 'BR Budget', 'br-budget', cls.client_obj)
        cls.job_role = JobRole.objects.create(
            org=cls.org, name='BR Role', code='br-role',
            skill_category='unskilled', is_active=True,
        )

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def _start_with_reservation(self, budget_max='5000.00'):
        """Create an MRF with a line item and start its workflow. Returns (mrf, instance)."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=2, budget_max=Decimal(budget_max))
        instance = start_mrf_workflow(mrf, actor=self.actor)
        return mrf, instance

    def _act(self, instance, step_code, action, comment='ok'):
        step = instance.steps.filter(step_code=step_code, status='active').first()
        user = self.hr_approver if step_code == 'hr_review' else self.finance_approver
        act_on_step(step, actor=user, action=action, comment=comment)
        instance.refresh_from_db()
        return step


# ─── 1–5: Service calculation ─────────────────────────────────────────────────

class TestCalculateMRFReservationAmount(BudgetReservationTestBase):

    def test_01_uses_budget_max(self):
        """budget_max is the primary amount source."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('3000.00'))

        amount = calculate_mrf_reservation_amount(mrf, self.budget)
        self.assertEqual(amount, Decimal('3000.00'))

    def test_02_falls_back_to_budget_min(self):
        """When budget_max is absent, budget_min is used."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_min=Decimal('2500.00'))

        amount = calculate_mrf_reservation_amount(mrf, self.budget)
        self.assertEqual(amount, Decimal('2500.00'))

    def test_03_falls_back_to_rate_times_headcount(self):
        """When no budget_max/min, uses headcount * billing_rate_snapshot."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=3, billing_rate_snapshot=Decimal('800.00'))

        amount = calculate_mrf_reservation_amount(mrf, self.budget)
        self.assertEqual(amount, Decimal('2400.00'))

    def test_04_missing_all_sources_raises(self):
        """No amount source at all raises BudgetReservationError."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1)  # all amount fields None

        with self.assertRaises(BudgetReservationError) as ctx:
            calculate_mrf_reservation_amount(mrf, self.budget)
        self.assertIn('no budget amount', str(ctx.exception))

    def test_05_no_matching_line_items_raises(self):
        """MRF with no line items (or all on different budget_plan) raises."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        # No line items at all

        with self.assertRaises(BudgetReservationError) as ctx:
            calculate_mrf_reservation_amount(mrf, self.budget)
        self.assertIn('no budgeted line items', str(ctx.exception))


# ─── 6–10: Reserve on workflow start ─────────────────────────────────────────

class TestReserveOnWorkflowStart(BudgetReservationTestBase):

    def test_06_draft_mrf_has_no_reservation(self):
        """Before workflow start, no BudgetReservation exists for the MRF."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget, status='draft')
        self.assertFalse(BudgetReservation.objects.filter(mrf=mrf).exists())

    def test_07_starting_workflow_creates_reserved_reservation(self):
        """start_mrf_workflow creates a BudgetReservation with status='reserved'."""
        mrf, _instance = self._start_with_reservation()

        reservation = BudgetReservation.objects.filter(mrf=mrf).first()
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.status, 'reserved')
        self.assertEqual(reservation.budget_plan_id, self.budget.pk)
        self.assertEqual(reservation.org_id, self.org.pk)
        self.assertIsNotNone(reservation.reserved_at)
        self.assertEqual(reservation.reserved_by_id, self.actor.pk)

    def test_08_reservation_amount_matches_line_items(self):
        """Reservation amount equals sum of line item budget_max values."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('4000.00'))
        _line_item(mrf, self.job_role, headcount=2, budget_max=Decimal('6000.00'))
        start_mrf_workflow(mrf, actor=self.actor)

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.amount, Decimal('10000.00'))

    def test_09_workflow_start_rolls_back_if_reservation_fails(self):
        """
        If reservation calculation fails (line item with no amount source),
        start_mrf_workflow raises WorkflowConfigurationError and rolls back —
        no WorkflowInstance or status change is persisted.
        """
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1)  # no amount → BudgetReservationError

        with self.assertRaises(WorkflowConfigurationError):
            start_mrf_workflow(mrf, actor=self.actor)

        self.assertFalse(WorkflowInstance.objects.filter(mrf=mrf).exists())
        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'submitted')

    def test_10_idempotent_reserve_no_duplicate(self):
        """Calling reserve_budget_for_mrf again when already reserved returns existing record."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('3000.00'))
        start_mrf_workflow(mrf, actor=self.actor)

        count_before = BudgetReservation.objects.filter(mrf=mrf).count()
        reserve_budget_for_mrf(mrf, actor=self.actor)  # called again directly
        count_after = BudgetReservation.objects.filter(mrf=mrf).count()

        self.assertEqual(count_before, count_after)
        self.assertEqual(count_after, 1)


# ─── 11–15: Commit / release on workflow outcome ──────────────────────────────

class TestReservationLifecycle(BudgetReservationTestBase):

    def test_11_hr_approve_only_keeps_reservation_reserved(self):
        """After step 1 approves, workflow is still active — reservation stays reserved."""
        mrf, instance = self._start_with_reservation()

        self._act(instance, 'hr_review', 'approve')

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.status, 'reserved')

    def test_12_final_approval_commits_reservation(self):
        """After final step approves, reservation becomes committed."""
        mrf, instance = self._start_with_reservation()
        self._act(instance, 'hr_review', 'approve')
        self._act(instance, 'finance_review', 'approve')

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.status, 'committed')
        self.assertIsNotNone(reservation.committed_at)

    def test_13_final_rejection_releases_reservation(self):
        """When workflow is finally rejected (no reject-back target), reservation is released."""
        mrf, instance = self._start_with_reservation()
        # Reject step 1 — no on_reject_target, so workflow completes as rejected
        self._act(instance, 'hr_review', 'reject')

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.status, 'released')
        self.assertIsNotNone(reservation.released_at)
        self.assertEqual(reservation.note, 'Workflow rejected')

    def test_14_reject_back_keeps_reservation_reserved(self):
        """When reject sends workflow back (on_reject_target set), reservation stays reserved."""
        # Build a fresh template where step2 rejects back to step1
        tpl = _template(self.org, 'br-loop-tpl')
        _step(tpl, 1, 'step_a', on_approve_next='step_b')
        _step(tpl, 2, 'step_b', on_approve_next='END', on_reject_target='step_a')
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='mrf', template=tpl, site=self.site, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_a',
            site=self.site, assignment_mode='named_user',
            named_user=self.hr_approver, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_b',
            site=self.site, assignment_mode='named_user',
            named_user=self.finance_approver, is_active=True,
        )

        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('2000.00'))
        instance = start_mrf_workflow(mrf, actor=self.actor)

        # Approve step_a → step_b becomes active
        step_a = instance.steps.get(step_code='step_a')
        act_on_step(step_a, actor=self.hr_approver, action='approve', comment='ok')

        # Reject step_b with reject-back → step_a reactivated, workflow still active
        step_b = instance.steps.get(step_code='step_b')
        act_on_step(step_b, actor=self.finance_approver, action='reject', comment='needs revision')

        instance.refresh_from_db()
        self.assertEqual(instance.status, 'active')

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.status, 'reserved')

    def test_15_request_changes_keeps_reservation_reserved(self):
        """When request_changes sends workflow back, reservation stays reserved."""
        tpl = _template(self.org, 'br-rc-tpl')
        _step(tpl, 1, 'step_x', on_approve_next='step_y')
        _step(tpl, 2, 'step_y', on_approve_next='END', on_request_changes_target='step_x')
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='mrf', template=tpl, site=self.site, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_x',
            site=self.site, assignment_mode='named_user',
            named_user=self.hr_approver, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_y',
            site=self.site, assignment_mode='named_user',
            named_user=self.finance_approver, is_active=True,
        )

        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('2000.00'))
        instance = start_mrf_workflow(mrf, actor=self.actor)

        step_x = instance.steps.get(step_code='step_x')
        act_on_step(step_x, actor=self.hr_approver, action='approve', comment='ok')

        step_y = instance.steps.get(step_code='step_y')
        act_on_step(step_y, actor=self.finance_approver, action='request_changes',
                    comment='please revise')

        instance.refresh_from_db()
        self.assertEqual(instance.status, 'active')

        reservation = BudgetReservation.objects.get(mrf=mrf)
        self.assertEqual(reservation.status, 'reserved')


# ─── 16–19: Budget totals on BudgetPlan serializer ───────────────────────────

class TestBudgetPlanTotals(BudgetReservationTestBase):

    def _get_budget(self, budget_pk):
        resp = self._api(self.admin).get(f'/api/budgets/plans/{budget_pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_16_reserved_amount_reflects_reserved_reservations(self):
        """reserved_amount = sum of reservations with status='reserved'."""
        local_budget = _budget(self.org, 'BR Totals 16', 'br-t16', self.client_obj, '20000.00')
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=local_budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('3000.00'))
        start_mrf_workflow(mrf, actor=self.actor)

        data = self._get_budget(local_budget.pk)
        self.assertEqual(Decimal(data['reserved_amount']), Decimal('3000.00'))
        self.assertEqual(Decimal(data['committed_amount']), Decimal('0.00'))

    def test_17_committed_amount_reflects_committed_reservations(self):
        """committed_amount = sum of reservations with status='committed'."""
        local_budget = _budget(self.org, 'BR Totals 17', 'br-t17', self.client_obj, '20000.00')
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=local_budget)
        _line_item(mrf, self.job_role, headcount=1, budget_max=Decimal('4000.00'))
        instance = start_mrf_workflow(mrf, actor=self.actor)

        # Approve both steps → reservation committed
        self._act(instance, 'hr_review', 'approve')
        self._act(instance, 'finance_review', 'approve')

        data = self._get_budget(local_budget.pk)
        self.assertEqual(Decimal(data['committed_amount']), Decimal('4000.00'))
        self.assertEqual(Decimal(data['reserved_amount']), Decimal('0.00'))

    def test_18_available_amount_deducts_reserved_and_committed(self):
        """available_amount = plan.amount - reserved - committed."""
        local_budget = _budget(self.org, 'BR Totals 18', 'br-t18', self.client_obj, '10000.00')

        # Create one reserved reservation (not via workflow for simplicity)
        BudgetReservation.objects.create(
            org=self.org, budget_plan=local_budget,
            mrf=_mrf(self.org, self.site, self.actor),
            amount=Decimal('2000.00'), status='reserved',
        )
        # Create one committed reservation
        BudgetReservation.objects.create(
            org=self.org, budget_plan=local_budget,
            mrf=_mrf(self.org, self.site, self.actor),
            amount=Decimal('3000.00'), status='committed',
        )

        data = self._get_budget(local_budget.pk)
        self.assertEqual(Decimal(data['reserved_amount']), Decimal('2000.00'))
        self.assertEqual(Decimal(data['committed_amount']), Decimal('3000.00'))
        self.assertEqual(Decimal(data['available_amount']), Decimal('5000.00'))

    def test_19_released_reservations_do_not_reduce_available(self):
        """Released reservations are not subtracted from available_amount."""
        local_budget = _budget(self.org, 'BR Totals 19', 'br-t19', self.client_obj, '10000.00')
        BudgetReservation.objects.create(
            org=self.org, budget_plan=local_budget,
            mrf=_mrf(self.org, self.site, self.actor),
            amount=Decimal('5000.00'), status='released',
        )

        data = self._get_budget(local_budget.pk)
        self.assertEqual(Decimal(data['available_amount']), Decimal('10000.00'))
        self.assertEqual(Decimal(data['reserved_amount']), Decimal('0.00'))


# ─── 20–22: Regression ───────────────────────────────────────────────────────

class TestBudgetReservationRegression(BudgetReservationTestBase):

    def test_20_mrf_without_budget_plan_starts_without_reservation(self):
        """
        MRF with no budget_plan starts workflow successfully;
        no BudgetReservation is created.
        """
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=None)
        instance = start_mrf_workflow(mrf, actor=self.actor)

        self.assertIsNotNone(instance)
        self.assertFalse(BudgetReservation.objects.filter(mrf=mrf).exists())
        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'hr_review')

    def test_21_budget_plan_serializer_still_returns_all_existing_fields(self):
        """BudgetPlan API response still contains all original fields after adding totals."""
        resp = self._api(self.admin).get(f'/api/budgets/plans/{self.budget.pk}/')
        self.assertEqual(resp.status_code, 200)
        for field in ('id', 'name', 'code', 'budget_nature', 'amount', 'currency',
                      'status', 'is_active', 'period_start',
                      'reserved_amount', 'committed_amount', 'available_amount'):
            self.assertIn(field, resp.data, f'Missing field: {field}')

    def test_22_mrf_serializer_includes_reservation_fields(self):
        """MRF API response includes budget_reserved_amount, budget_committed_amount,
        budget_reservation_status; all null/zero before workflow starts."""
        mrf = _mrf(self.org, self.site, self.actor, budget_plan=self.budget)

        resp = self._api(self.admin).get(f'/api/mrf/requests/{mrf.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('budget_reserved_amount', resp.data)
        self.assertIn('budget_committed_amount', resp.data)
        self.assertIn('budget_reservation_status', resp.data)
        self.assertIsNone(resp.data['budget_reservation_status'])
        self.assertEqual(Decimal(resp.data['budget_reserved_amount']), Decimal('0.00'))
        self.assertEqual(Decimal(resp.data['budget_committed_amount']), Decimal('0.00'))

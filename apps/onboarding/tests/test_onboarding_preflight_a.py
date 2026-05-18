"""
apps/onboarding/tests/test_onboarding_preflight_a.py

Phase Client-Onboarding-Finalization-Preflight-A tests.

Scenarios:
  P01  Final approval blocked when proposed user email already exists.
  P02  Workflow remains active after preflight failure.
  P03  Final step remains active after preflight failure.
  P04  Onboarding request remains in_review after preflight failure.
  P05  finalization_status is not changed to 'failed' on preflight failure.
  P06  Final approval blocked when proposed client code already exists.
  P07  Final approval blocked when proposed site code already exists.
  P08  Final approval blocked when proposed budget code already exists.
  P09  Non-final approval step does NOT run preflight (no block on duplicate email).
  P10  Successful final approval with clean data: finalizes and creates real records.
  P11  MRF final approval is unaffected (no preflight run).
  P12  API shape: 400 response includes both 'detail' and 'errors' keys.
  P13  Preflight passes when proposed user already has created_user set.
  P14  API via act endpoint returns correct error shape.
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.onboarding.models import (
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
    ClientOnboardingRequest,
)
from apps.onboarding.services import validate_onboarding_finalization_preflight
from apps.sites.models import Client, SiteProfile
from apps.workflow.exceptions import OnboardingPreflightError
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowInstance,
    WorkflowStepInstance,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)
from apps.workflow.services import act_on_step, start_client_onboarding_workflow


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


def _ob_template(org, code='pf-co-tmpl'):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code,
        trigger_type='client_onboarding', version=1, is_active=True,
    )


def _ob_step(template, order, code, on_approve_next=''):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        on_approve_next=on_approve_next,
        requires_comment_on_reject=False,
        requires_comment_on_request_changes=False,
    )


def _ob_mapping(org, template):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='client_onboarding', template=template,
        client=None, site=None, is_active=True,
    )


def _ob_sac(org, step_code, named_user):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='client_onboarding', step_code=step_code,
        client=None, site=None,
        assignment_mode='named_user', named_user=named_user,
        is_active=True,
    )


def _job_role(org, code='pf-jr-1'):
    return JobRole.objects.create(
        org=org, name=code, code=code, skill_category='unskilled', is_active=True,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class PreflightTestBase(TestCase):
    """
    Org with a 2-step client_onboarding workflow.
    step1 (non-final) → step2 (final).
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='PF Org', code='pf-org')
        cls.n_org = _node(cls.org, 'pf-org', 'company', None, 0, 'pf-org')

        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = _user('pf_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_org)

        cls.approver = _user('pf_approver', org=cls.org)
        _assign(cls.approver, cls.role_admin, cls.n_org)

        cls.job_role = _job_role(cls.org)

        # 2-step template: step1 → step2 (final)
        cls.template = _ob_template(cls.org)
        cls.step1 = _ob_step(cls.template, 1, 'pf_step1')
        cls.step2 = _ob_step(cls.template, 2, 'pf_step2')
        cls.mapping = _ob_mapping(cls.org, cls.template)
        cls.sac1 = _ob_sac(cls.org, 'pf_step1', cls.approver)
        cls.sac2 = _ob_sac(cls.org, 'pf_step2', cls.approver)

    def setUp(self):
        self.api = APIClient()

    # ── URL helpers ──────────────────────────────────────────────────────────

    def _act_url(self, instance_id, step_id):
        return f'/api/workflow/instances/{instance_id}/steps/{step_id}/act/'

    # ── Object factories ─────────────────────────────────────────────────────

    def _new_client_request(self, code='pf-nc', name='PF New Corp', **kwargs):
        return ClientOnboardingRequest.objects.create(
            org=self.org,
            client=None,
            requested_by=self.admin,
            onboarding_type='new_client',
            status='draft',
            proposed_client_name=name,
            proposed_client_code=code,
            **kwargs,
        )

    def _proposed_site(self, req, code='pf-ps-1'):
        return ClientOnboardingProposedSite.objects.create(
            request=req, name=f'Site {code}', code=code,
            city='Mumbai', state='MH', is_active=True,
        )

    def _proposed_dept(self, req, p_site=None, code='pf-pd-1'):
        return ClientOnboardingProposedDepartment.objects.create(
            request=req, proposed_site=p_site,
            scope_level='site' if p_site else 'client',
            name=f'Dept {code}', code=code, is_active=True,
        )

    def _proposed_srr(self, req, p_site):
        return ClientOnboardingProposedSiteRoleRequirement.objects.create(
            request=req,
            proposed_site=p_site,
            job_role=self.job_role,
            approved_headcount=2,
            billing_type='billable',
            effective_from=datetime.date.today(),
            is_active=True,
        )

    def _proposed_budget(self, req, code='pf-bp-1', scope_level='client',
                         p_site=None, p_dept=None):
        return ClientOnboardingProposedBudget.objects.create(
            request=req,
            name=f'Budget {code}',
            code=code,
            budget_nature='billable',
            budget_type='onboarding',
            scope_level=scope_level,
            proposed_site=p_site,
            proposed_department=p_dept,
            amount='100000.00',
            currency='INR',
            period_start=datetime.date.today(),
            is_active=True,
        )

    def _proposed_user(self, req, email='pf.user@example.com', p_site=None,
                       scope_level='client'):
        return ClientOnboardingProposedUser.objects.create(
            request=req,
            full_name='PF User',
            email=email,
            access_role=self.role_admin,
            scope_level=scope_level,
            proposed_site=p_site,
            is_active=True,
        )

    def _start_and_get_steps(self, req):
        """Start the workflow and return (instance, step1_inst, step2_inst)."""
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        steps = list(instance.steps.order_by('step_order'))
        return instance, steps[0], steps[1]

    def _approve_step1(self, req):
        """Start workflow and approve the non-final step. Returns (instance, step2_inst)."""
        instance, s1, s2 = self._start_and_get_steps(req)
        act_on_step(s1, actor=self.approver, action='approve')
        s2.refresh_from_db()
        return instance, s2

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── P01–P05: State assertions after preflight failure ───────────────────────

class TestPreflightBlocksFinalApproval(PreflightTestBase):

    def _setup_duplicate_email(self):
        """Create a request with a proposed user whose email already exists."""
        User.objects.create_user(
            username='existing_pf_user',
            email='dup.pf@example.com',
            password='pass',
        )
        req = self._new_client_request(code='pf-dup-email')
        self._proposed_site(req, code='pf-de-site')
        self._proposed_user(req, email='dup.pf@example.com')
        return req

    def test_P01_duplicate_user_email_blocks_final_approval(self):
        """Final approval raises OnboardingPreflightError when proposed email already exists."""
        req = self._setup_duplicate_email()
        instance, s2 = self._approve_step1(req)

        with self.assertRaises(OnboardingPreflightError) as cm:
            act_on_step(s2, actor=self.approver, action='approve')

        self.assertIn('dup.pf@example.com', str(cm.exception.preflight_errors))

    def test_P02_workflow_remains_active_after_preflight_failure(self):
        """WorkflowInstance.status stays 'active' when preflight blocks final approval."""
        req = self._setup_duplicate_email()
        instance, s2 = self._approve_step1(req)

        try:
            act_on_step(s2, actor=self.approver, action='approve')
        except OnboardingPreflightError:
            pass

        instance.refresh_from_db()
        self.assertEqual(instance.status, 'active')

    def test_P03_final_step_remains_active_after_preflight_failure(self):
        """The final step stays 'active' (not 'approved') when preflight blocks it."""
        req = self._setup_duplicate_email()
        instance, s2 = self._approve_step1(req)

        try:
            act_on_step(s2, actor=self.approver, action='approve')
        except OnboardingPreflightError:
            pass

        s2.refresh_from_db()
        self.assertEqual(s2.status, 'active')
        self.assertEqual(s2.action_taken, '')

    def test_P04_onboarding_remains_in_review_after_preflight_failure(self):
        """ClientOnboardingRequest.status stays 'in_review' when preflight blocks approval."""
        req = self._setup_duplicate_email()
        instance, s2 = self._approve_step1(req)

        try:
            act_on_step(s2, actor=self.approver, action='approve')
        except OnboardingPreflightError:
            pass

        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

    def test_P05_finalization_status_not_failed_on_preflight_block(self):
        """finalization_status is NOT set to 'failed' on a preflight block."""
        req = self._setup_duplicate_email()
        instance, s2 = self._approve_step1(req)

        try:
            act_on_step(s2, actor=self.approver, action='approve')
        except OnboardingPreflightError:
            pass

        req.refresh_from_db()
        self.assertNotEqual(req.finalization_status, 'failed')
        self.assertEqual(req.finalization_status, 'not_finalized')


# ─── P06–P08: Other blocking checks ──────────────────────────────────────────

class TestOtherPreflightChecks(PreflightTestBase):

    def test_P06_duplicate_client_code_blocks_final_approval(self):
        """Final approval blocked when proposed_client_code matches an existing active Client."""
        Client.objects.create(
            org=self.org, name='Pre-existing', code='pf-clash-code', is_active=True,
        )
        req = self._new_client_request(code='pf-clash-code', name='Clash Corp')
        instance, s2 = self._approve_step1(req)

        with self.assertRaises(OnboardingPreflightError) as cm:
            act_on_step(s2, actor=self.approver, action='approve')

        errors = cm.exception.preflight_errors
        self.assertTrue(any('pf-clash-code' in e for e in errors))

    def test_P07_duplicate_site_code_blocks_final_approval(self):
        """Final approval blocked when a proposed site code already exists as an active SiteProfile."""
        existing_client = Client.objects.create(
            org=self.org, name='Existing', code='pf-ex-cl', is_active=True,
        )
        SiteProfile.objects.create(
            org=self.org, client=existing_client,
            name='Existing Site', code='pf-dup-site', is_active=True,
        )

        req = self._new_client_request(code='pf-site-clash')
        self._proposed_site(req, code='pf-dup-site')
        instance, s2 = self._approve_step1(req)

        with self.assertRaises(OnboardingPreflightError) as cm:
            act_on_step(s2, actor=self.approver, action='approve')

        errors = cm.exception.preflight_errors
        self.assertTrue(any('pf-dup-site' in e for e in errors))

    def test_P08_duplicate_budget_code_blocks_final_approval(self):
        """Final approval blocked when a proposed budget code already exists as an active BudgetPlan."""
        BudgetPlan.objects.create(
            org=self.org,
            name='Existing Budget',
            code='pf-dup-bp',
            budget_nature='billable',
            budget_type='onboarding',
            amount='50000',
            currency='INR',
            period_start=datetime.date.today(),
            status='active',
            is_active=True,
        )

        req = self._new_client_request(code='pf-bp-clash')
        self._proposed_budget(req, code='pf-dup-bp')
        instance, s2 = self._approve_step1(req)

        with self.assertRaises(OnboardingPreflightError) as cm:
            act_on_step(s2, actor=self.approver, action='approve')

        errors = cm.exception.preflight_errors
        self.assertTrue(any('pf-dup-bp' in e for e in errors))


# ─── P09: Non-final step does not run preflight ───────────────────────────────

class TestNonFinalStepSkipsPreflight(PreflightTestBase):

    def test_P09_non_final_approval_does_not_run_preflight(self):
        """
        Approving a non-final step succeeds even if the proposed email already exists.
        Preflight only runs on the final step.
        """
        User.objects.create_user(
            username='existing_pf_nonfinal',
            email='nonfinal.pf@example.com',
            password='pass',
        )
        req = self._new_client_request(code='pf-nonfinal')
        self._proposed_user(req, email='nonfinal.pf@example.com')

        instance, s1, s2 = self._start_and_get_steps(req)

        # Step 1 is NOT final — should approve without raising
        act_on_step(s1, actor=self.approver, action='approve')
        s1.refresh_from_db()
        self.assertEqual(s1.status, 'approved')


# ─── P10: Successful final approval ──────────────────────────────────────────

class TestSuccessfulFinalApproval(PreflightTestBase):

    def test_P10_clean_data_finalizes_and_creates_real_records(self):
        """With no conflicts, final approval completes finalization and creates real records."""
        req = self._new_client_request(
            code='pf-clean-corp',
            name='PF Clean Corp',
        )
        p_site = self._proposed_site(req, code='pf-clean-site')
        self._proposed_srr(req, p_site)

        instance, s2 = self._approve_step1(req)
        act_on_step(s2, actor=self.approver, action='approve')

        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.finalization_status, 'finalized')
        self.assertIsNotNone(req.created_client)
        self.assertEqual(req.created_client.code, 'pf-clean-corp')

        real_sites = SiteProfile.objects.filter(client=req.created_client)
        self.assertEqual(real_sites.count(), 1)
        self.assertEqual(real_sites.first().code, 'pf-clean-site')


# ─── P11: MRF approval is unaffected ─────────────────────────────────────────

class TestMRFUnaffected(PreflightTestBase):
    """MRF workflow final approval does not run onboarding preflight."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.mrf.models import ManpowerRequest

        cls.n_cl = _node(cls.org, 'pf-cl', 'client', cls.n_org, 1, 'pf-org/pf-cl')
        cls.mrf_client = Client.objects.create(
            org=cls.org, name='PF MRF Client', code='pf-cl',
            scope_node=cls.n_cl, is_active=True,
        )
        cls.n_site = _node(cls.org, 'pf-site', 'site', cls.n_cl, 2, 'pf-org/pf-cl/pf-site')
        cls.mrf_site = SiteProfile.objects.create(
            org=cls.org, client=cls.mrf_client,
            name='PF MRF Site', code='pf-site',
            scope_node=cls.n_site, is_active=True,
        )

        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='pf-mrf-tmpl', code='pf-mrf-tmpl',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.mrf_step = WorkflowStepTemplate.objects.create(
            template=cls.mrf_template, order=1, code='pf_mrf_step1',
            name='HR Review', assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False,
            requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.mrf_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='pf_mrf_step1',
            assignment_mode='named_user', named_user=cls.approver, is_active=True,
        )

    def test_P11_mrf_final_approval_unaffected(self):
        """Approving the final MRF step does not raise OnboardingPreflightError."""
        from apps.mrf.models import ManpowerRequest
        from apps.workflow.services import start_mrf_workflow

        # Create a real user with an email that a proposed onboarding user might share —
        # should not affect MRF at all.
        User.objects.create_user(
            username='pf_mrf_block_user',
            email='mrf.block@example.com',
            password='pass',
        )

        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.mrf_site,
            requested_by=self.admin,
            mrf_type='new_hiring', status='submitted', billing_type='billable',
        )
        instance = start_mrf_workflow(mrf, actor=self.admin)
        step = instance.steps.first()

        # Should complete without raising
        act_on_step(step, actor=self.approver, action='approve')
        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'approved')


# ─── P12–P13: API shape and edge cases ───────────────────────────────────────

class TestPreflightAPIShape(PreflightTestBase):

    def test_P12_api_returns_detail_and_errors_keys(self):
        """
        POST to act endpoint returns 400 with both 'detail' and 'errors' when preflight fails.
        """
        User.objects.create_user(
            username='pf_api_shape',
            email='api.shape@example.com',
            password='pass',
        )
        req = self._new_client_request(code='pf-api-shape')
        self._proposed_user(req, email='api.shape@example.com')
        instance, s2 = self._approve_step1(req)

        self._login(self.approver)
        resp = self.api.post(
            self._act_url(instance.pk, s2.pk),
            {'action': 'approve'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('detail', resp.data)
        self.assertIn('errors', resp.data)
        self.assertIsInstance(resp.data['errors'], list)
        self.assertTrue(len(resp.data['errors']) > 0)
        self.assertEqual(resp.data['detail'], 'Onboarding cannot be finalized.')

    def test_P13_already_finalized_proposed_user_not_treated_as_duplicate(self):
        """
        A proposed user with created_user set is skipped in email-uniqueness check.
        """
        existing_system_user = User.objects.create_user(
            username='pf_already_done',
            email='already.done@example.com',
            password='pass',
        )
        req = self._new_client_request(code='pf-already-done')
        p_site = self._proposed_site(req, code='pf-done-site')
        self._proposed_srr(req, p_site)

        # Proposed user with same email but already linked to a real user
        ClientOnboardingProposedUser.objects.create(
            request=req,
            full_name='Already Done User',
            email='already.done@example.com',
            access_role=self.role_admin,
            scope_level='client',
            is_active=True,
            created_user=existing_system_user,
        )

        errors = validate_onboarding_finalization_preflight(req)
        self.assertFalse(
            any('already.done@example.com' in e for e in errors),
            f"Should not flag already-linked user, but got: {errors}",
        )

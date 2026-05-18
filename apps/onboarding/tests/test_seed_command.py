"""
apps/onboarding/tests/test_seed_command.py

Tests for seed_client_onboarding_workflow_demo management command.

Scenarios:
  1.  Command runs without error with all user args provided.
  2.  Second run is fully idempotent — no duplicate rows created.
  3.  Department is attached to SAC when user's dept matches expected.
  4.  Department is skipped (warns) when user's dept does not match.
  5.  Demo request is created on first run.
  6.  Demo request is NOT duplicated on second run.
  7.  Config-check passes (ok=True) after seeding all configs.
  8.  Missing user produces an error log but does not raise.
  9.  Command works without any user args (skips SAC creation).
  10. Final admin step has no department (no admin dept in DB).
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.access.models import AccessRole, UserRoleAssignment
from apps.accounts.models import User
from apps.core.models import Department, Organization, ScopeNode
from apps.onboarding.models import ClientOnboardingRequest
from apps.sites.models import Client
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowInstance,
    WorkflowStepInstance,
    WorkflowTemplate,
    WorkflowTemplateMapping,
    WorkflowStepTemplate,
)
from apps.workflow.services import (
    resolve_step_assignment,
    resolve_workflow_template,
    start_client_onboarding_workflow,
)
from apps.workflow.exceptions import WorkflowConfigurationError


# ─── Test base ────────────────────────────────────────────────────────────────

class SeedCommandTestBase(TestCase):
    """
    Creates a minimal Logicon-style org with the expected departments and users.
    Users are given distinct '.st' suffix to avoid collision with production-data names.
    """

    ORG_CODE = 'seed-test'

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Seed Test Org', code=cls.ORG_CODE)

        # Scope nodes
        cls.n_company = ScopeNode.objects.create(
            org=cls.org, code='seed-test', name='Seed Test', node_type='company',
            parent=None, depth=0, path='seed-test', is_active=True,
        )
        cls.n_client = ScopeNode.objects.create(
            org=cls.org, code='seed-client', name='Seed Client', node_type='client',
            parent=cls.n_company, depth=1, path='seed-test/seed-client', is_active=True,
        )

        # Client (named demo_client to avoid collision with TestCase.client)
        cls.demo_client = Client.objects.create(
            org=cls.org, name='Seed ABC Client', code='SEED-ABC-INFRA',
            scope_node=cls.n_client,
        )

        # Departments (org-level, mirroring logicon structure)
        cls.dept_sales = Department.objects.create(org=cls.org, code='sales', name='Sales')
        cls.dept_ops = Department.objects.create(org=cls.org, code='operations', name='Operations')
        cls.dept_hr = Department.objects.create(org=cls.org, code='hr', name='Human Resources')
        cls.dept_finance = Department.objects.create(org=cls.org, code='finance', name='Finance')

        # Users with correct departments
        cls.u_sales = cls._make_user('rohan.sales.st', cls.dept_sales)
        cls.u_ops = cls._make_user('suresh.ops.st', cls.dept_ops)
        cls.u_hr = cls._make_user('jyoti.hr.st', cls.dept_hr)
        cls.u_finance = cls._make_user('bhakti.finance.st', cls.dept_finance)
        cls.u_admin = cls._make_user('admin.st', None)
        cls.u_admin.is_superuser = True
        cls.u_admin.save()

        # AccessRoles needed for role assignments in scope tests (not for SAC)
        for code in ('sales_executive', 'site_manager', 'hr_admin', 'finance', 'admin'):
            AccessRole.objects.get_or_create(org=cls.org, code=code, defaults={'name': code})

    @classmethod
    def _make_user(cls, username, dept):
        u = User.objects.create_user(username=username, password='pass')
        u.org = cls.org
        u.department = dept
        u.save()
        return u

    def _call_seed(self, **extra_kwargs):
        """Call seed command with standard user args and return captured stdout."""
        out = StringIO()
        kwargs = dict(
            org=self.ORG_CODE,
            sales_user='rohan.sales.st',
            operations_user='suresh.ops.st',
            hr_user='jyoti.hr.st',
            finance_user='bhakti.finance.st',
            admin_user='admin.st',
            stdout=out,
        )
        kwargs.update(extra_kwargs)
        call_command('seed_client_onboarding_workflow_demo', **kwargs)
        return out.getvalue()


# ─── Idempotency tests ────────────────────────────────────────────────────────

class TestSeedCommandIdempotency(SeedCommandTestBase):

    def test_first_run_creates_all_objects(self):
        """Scenario 1: command creates template, steps, mapping, SACs, demo request."""
        self._call_seed()

        self.assertEqual(
            WorkflowTemplate.objects.filter(org=self.org, code='client_onboarding_default').count(), 1,
        )
        template = WorkflowTemplate.objects.get(org=self.org, code='client_onboarding_default')
        self.assertEqual(template.steps.count(), 5)
        self.assertEqual(
            WorkflowTemplateMapping.objects.filter(
                org=self.org, trigger_type='client_onboarding', client__isnull=True,
                site__isnull=True, is_active=True,
            ).count(), 1,
        )
        self.assertEqual(
            StepAssignmentConfig.objects.filter(
                org=self.org, trigger_type='client_onboarding', is_active=True,
            ).count(), 5,
        )
        self.assertEqual(
            ClientOnboardingRequest.objects.filter(org=self.org).count(), 1,
        )

    def test_second_run_creates_no_duplicates(self):
        """Scenario 2: second run is fully idempotent."""
        self._call_seed()
        self._call_seed()

        self.assertEqual(
            WorkflowTemplate.objects.filter(org=self.org, code='client_onboarding_default').count(), 1,
        )
        template = WorkflowTemplate.objects.get(org=self.org, code='client_onboarding_default')
        self.assertEqual(template.steps.count(), 5)
        self.assertEqual(
            WorkflowTemplateMapping.objects.filter(
                org=self.org, trigger_type='client_onboarding', is_active=True,
            ).count(), 1,
        )
        self.assertEqual(
            StepAssignmentConfig.objects.filter(
                org=self.org, trigger_type='client_onboarding', is_active=True,
            ).count(), 5,
        )
        self.assertEqual(
            ClientOnboardingRequest.objects.filter(org=self.org).count(), 1,
        )

    def test_second_run_output_shows_exists(self):
        """Scenario 2b: second run output mentions EXISTS for pre-existing rows."""
        self._call_seed()
        out = self._call_seed()
        self.assertIn('EXISTS', out)

    def test_demo_request_not_duplicated(self):
        """Scenario 5+6: demo request created once; second run returns EXISTS."""
        self._call_seed()
        req1 = ClientOnboardingRequest.objects.get(org=self.org)
        self._call_seed()
        self.assertEqual(ClientOnboardingRequest.objects.filter(org=self.org).count(), 1)
        req2 = ClientOnboardingRequest.objects.get(org=self.org)
        self.assertEqual(req1.pk, req2.pk)


# ─── Department assignment tests ──────────────────────────────────────────────

class TestSeedDepartmentAssignment(SeedCommandTestBase):

    def setUp(self):
        self._call_seed()

    def test_sales_sac_has_sales_dept(self):
        """Scenario 3a: sales step SAC carries Sales department."""
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='sales_submit_review', is_active=True,
        )
        self.assertIsNotNone(sac.department)
        self.assertEqual(sac.department.code, 'sales')

    def test_operations_sac_has_operations_dept(self):
        """Scenario 3b: operations step SAC carries Operations department."""
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='operations_review', is_active=True,
        )
        self.assertIsNotNone(sac.department)
        self.assertEqual(sac.department.code, 'operations')

    def test_hr_sac_has_hr_dept(self):
        """Scenario 3c: hr step SAC carries Human Resources department."""
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='hr_review', is_active=True,
        )
        self.assertIsNotNone(sac.department)
        self.assertEqual(sac.department.code, 'hr')

    def test_finance_sac_has_finance_dept(self):
        """Scenario 3d: finance step SAC carries Finance department."""
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='finance_review', is_active=True,
        )
        self.assertIsNotNone(sac.department)
        self.assertEqual(sac.department.code, 'finance')

    def test_final_admin_sac_has_no_dept(self):
        """Scenario 10: final_admin_review SAC has no department (admin has no dept)."""
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='final_admin_review', is_active=True,
        )
        self.assertIsNone(sac.department)

    def test_dept_mismatch_skips_dept_and_warns(self):
        """Scenario 4: user with wrong dept gets no dept on SAC; command warns but doesn't crash."""
        # Create a user whose department is wrong for the sales step
        wrong_dept_user = self._make_user('wrong.dept.sales.st', self.dept_finance)

        out = StringIO()
        call_command(
            'seed_client_onboarding_workflow_demo',
            org=self.ORG_CODE,
            sales_user='wrong.dept.sales.st',
            operations_user='suresh.ops.st',
            hr_user='jyoti.hr.st',
            finance_user='bhakti.finance.st',
            admin_user='admin.st',
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn('skipping department assignment', output.lower())

        # The SAC should now have no dept (because user dept didn't match expected)
        sac = StepAssignmentConfig.objects.get(
            org=self.org, trigger_type='client_onboarding',
            step_code='sales_submit_review', is_active=True,
        )
        self.assertIsNone(sac.department)
        self.assertEqual(sac.named_user, wrong_dept_user)


# ─── Config-check passes after seed ──────────────────────────────────────────

class TestSeedConfigCheck(SeedCommandTestBase):

    def setUp(self):
        self._call_seed()

    def test_resolve_template_succeeds(self):
        """Scenario 7a: template resolves cleanly for the demo client."""
        template = resolve_workflow_template('client_onboarding', self.org, client=self.demo_client)
        self.assertEqual(template.code, 'client_onboarding_default')
        self.assertTrue(template.is_active)

    def test_all_steps_resolve_assignment(self):
        """Scenario 7b: all 5 step assignments resolve without error."""
        template = resolve_workflow_template('client_onboarding', self.org, client=self.demo_client)
        for step in template.steps.order_by('order'):
            config = resolve_step_assignment(
                trigger_type='client_onboarding',
                org=self.org,
                step_code=step.code,
                client=self.demo_client,
            )
            self.assertIsNotNone(config.named_user)
            self.assertTrue(config.named_user.is_active)

    def test_workflow_can_start_after_seed(self):
        """Scenario 7c: workflow starts successfully on demo request after seeding."""
        req = ClientOnboardingRequest.objects.get(org=self.org)
        instance = start_client_onboarding_workflow(req, actor=self.u_sales)
        self.assertIsNotNone(instance.pk)
        self.assertEqual(instance.status, 'active')

        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

        steps = list(instance.steps.order_by('step_order'))
        self.assertEqual(len(steps), 5)
        self.assertEqual(steps[0].status, 'active')
        for s in steps[1:]:
            self.assertEqual(s.status, 'pending')


# ─── No user args test ────────────────────────────────────────────────────────

class TestSeedNoUserArgs(SeedCommandTestBase):

    def test_no_user_args_creates_template_and_mapping_only(self):
        """Scenario 9: running without user args creates template + steps + mapping but no SACs."""
        out = StringIO()
        call_command(
            'seed_client_onboarding_workflow_demo',
            org=self.ORG_CODE,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn('WorkflowTemplate', output)
        self.assertIn('WorkflowTemplateMapping', output)

        self.assertEqual(
            WorkflowTemplate.objects.filter(org=self.org, code='client_onboarding_default').count(), 1,
        )
        self.assertEqual(
            StepAssignmentConfig.objects.filter(
                org=self.org, trigger_type='client_onboarding',
            ).count(), 0,
        )


# ─── Missing user arg test ────────────────────────────────────────────────────

class TestSeedMissingUser(SeedCommandTestBase):

    def test_nonexistent_user_skips_with_error_log(self):
        """Scenario 8: nonexistent username logs error and continues; no crash."""
        out = StringIO()
        call_command(
            'seed_client_onboarding_workflow_demo',
            org=self.ORG_CODE,
            sales_user='does.not.exist.st',
            operations_user='suresh.ops.st',
            hr_user='jyoti.hr.st',
            finance_user='bhakti.finance.st',
            admin_user='admin.st',
            stdout=out,
        )
        # Nonexistent user is skipped; other steps still created
        self.assertEqual(
            StepAssignmentConfig.objects.filter(
                org=self.org, trigger_type='client_onboarding', is_active=True,
            ).count(), 4,  # sales step skipped
        )

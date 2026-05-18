"""
apps/dashboard/tests/test_dashboard_summary.py

Phase Dashboard-Backend-A — 14 tests:

 01. Unauthenticated GET → 401
 02. Superuser/internal admin sees internal audience and broad counts
 03. Client user scoped to Nova sees client audience
 04. Internal admin sees both clients/sites in client_overview
 05. Client user sees only their client in client_overview
 06. Client user does not receive hiring/talent data
 07. User with mrf.read sees MRF section populated
 08. User without mrf.read gets empty (zero) MRF section
 09. Onboarding counts respect scope
 10. my_work count only includes active steps assigned to the user
 11. Step assigned to another user not counted in my_work
 12. Budget section only populated with budget.read
 13. Hiring section only populated with hiring_application.read
 14. recent_activity respects scope (only scoped MRF/onboarding in activity)
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest
from apps.onboarding.models import ClientOnboardingRequest
from apps.sites.models import Client, SiteProfile
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowInstance,
    WorkflowStepInstance,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)

SUMMARY_URL = '/api/dashboard/summary/'


class DashboardBase(TestCase):
    """
    Shared fixtures:
      org           — single organization
      nova_client   — Client scoped at n_cl
      nova_site     — SiteProfile scoped at n_site
      role_admin    — 'admin' role with all capabilities
      role_client   — 'client_admin' role with client capabilities
      role_field    — 'field_supervisor' role (no mrf.read, no budget.read, etc.)
      admin_user    — internal user, admin role @ company scope
      client_user   — client user, client_admin role @ nova_client scope
      field_user    — field user, field_supervisor role @ nova_site scope
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Dash Org', code='dash-org')

        cls.n_co = ScopeNode.objects.create(
            org=cls.org, code='dash-org', name='Dash Org',
            node_type='company', depth=0, path='dash-org', is_active=True,
        )
        cls.n_cl = ScopeNode.objects.create(
            org=cls.org, code='nova-fac', name='Nova Facilities',
            node_type='client', parent=cls.n_co, depth=1,
            path='dash-org/nova-fac', is_active=True,
        )
        cls.n_site = ScopeNode.objects.create(
            org=cls.org, code='nova-site-1', name='Nova Site 1',
            node_type='site', parent=cls.n_cl, depth=2,
            path='dash-org/nova-fac/nova-site-1', is_active=True,
        )

        cls.nova_client = Client.objects.create(
            org=cls.org, name='Nova Facilities', code='nova-fac',
            scope_node=cls.n_cl, is_active=True,
        )
        cls.nova_site = SiteProfile.objects.create(
            org=cls.org, client=cls.nova_client, name='Nova Site 1',
            code='nova-site-1', scope_node=cls.n_site, is_active=True,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='dash-guard', skill_category='unskilled',
        )

        # Roles
        cls.role_admin = AccessRole.objects.get_or_create(
            org=cls.org, code='admin', defaults={'name': 'Admin'},
        )[0]
        bootstrap_role_permissions(cls.role_admin)

        cls.role_client = AccessRole.objects.get_or_create(
            org=cls.org, code='client_admin', defaults={'name': 'Client Admin'},
        )[0]
        bootstrap_role_permissions(cls.role_client)

        cls.role_field = AccessRole.objects.get_or_create(
            org=cls.org, code='field_supervisor', defaults={'name': 'Field Supervisor'},
        )[0]
        bootstrap_role_permissions(cls.role_field)

        # Users
        cls.admin_user = User.objects.create_user(username='dash_admin', password='pass')
        cls.admin_user.org = cls.org
        cls.admin_user.user_type = 'internal'
        cls.admin_user.save()
        UserRoleAssignment.objects.create(
            user=cls.admin_user, role=cls.role_admin, scope_node=cls.n_co,
        )

        cls.client_user = User.objects.create_user(username='dash_client', password='pass')
        cls.client_user.org = cls.org
        cls.client_user.user_type = 'client'
        cls.client_user.save()
        UserRoleAssignment.objects.create(
            user=cls.client_user, role=cls.role_client, scope_node=cls.n_cl,
        )

        cls.field_user = User.objects.create_user(username='dash_field', password='pass')
        cls.field_user.org = cls.org
        cls.field_user.user_type = 'field'
        cls.field_user.save()
        UserRoleAssignment.objects.create(
            user=cls.field_user, role=cls.role_field, scope_node=cls.n_site,
        )

        # Second client (another org to test scope isolation)
        cls.other_org = Organization.objects.create(name='Other Org', code='other-org')
        cls.other_n_cl = ScopeNode.objects.create(
            org=cls.other_org, code='other-cl', name='Other Client',
            node_type='client', depth=0, path='other-cl', is_active=True,
        )
        cls.other_client = Client.objects.create(
            org=cls.other_org, name='Other Client', code='other-cl',
            scope_node=cls.other_n_cl, is_active=True,
        )

        # MRF workflow setup for admin_user
        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='dash-mrf-tmpl', code='dash-mrf-tmpl',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.mrf_step_tmpl = WorkflowStepTemplate.objects.create(
            template=cls.mrf_template, order=1, code='dash_review', name='Review',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.mrf_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='dash_review',
            assignment_mode='named_user', named_user=cls.admin_user, is_active=True,
        )

    def _api(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _make_mrf(self, *, status='submitted'):
        return ManpowerRequest.objects.create(
            org=self.org, site=self.nova_site, requested_by=self.admin_user,
            mrf_type='new_hiring', billing_type='billable', status=status,
        )

    def _make_workflow_step(self, mrf, *, assigned_user=None, step_status='active'):
        """Create WorkflowInstance + active WorkflowStepInstance manually."""
        wf = WorkflowInstance.objects.create(
            org=self.org, mrf=mrf, template=self.mrf_template,
            template_version=1, status='active',
            initiated_by=self.admin_user,
        )
        step = WorkflowStepInstance.objects.create(
            workflow=wf,
            step_template=self.mrf_step_tmpl,
            step_order=1,
            step_code='dash_review',
            step_name='Review',
            assignment_mode='named_user',
            actor_type='internal',
            status=step_status,
            assigned_user=assigned_user or self.admin_user,
        )
        return wf, step


# ─── Tests 01–06: Auth, audience, scope ──────────────────────────────────────

class TestDashboardAuth(DashboardBase):

    def test_01_unauthenticated_returns_401(self):
        """GET without auth token → 401."""
        resp = self._api().get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 401)

    def test_02_superuser_sees_internal_audience(self):
        """Superuser gets audience='internal' and the response has all section keys."""
        su = User.objects.create_superuser(username='dash_su', password='pass')
        resp = self._api(su).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['audience'], 'internal')
        sections = resp.data['sections']
        for key in ('my_work', 'client_overview', 'onboarding', 'mrf',
                    'budget', 'hiring', 'talent', 'recent_activity'):
            self.assertIn(key, sections, f"Missing section key: {key}")

    def test_03_client_user_sees_client_audience(self):
        """Client user gets audience='client'."""
        resp = self._api(self.client_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['audience'], 'client')

    def test_04_internal_admin_sees_both_clients(self):
        """Internal admin scoped to company node sees all org clients."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        overview = resp.data['sections']['client_overview']
        # nova_client is in org; other_client is in a different org — not visible
        self.assertGreaterEqual(overview['client_count'], 1)
        codes = [c['code'] for c in overview['clients']]
        self.assertIn('nova-fac', codes)
        self.assertNotIn('other-cl', codes)

    def test_05_client_user_scoped_to_own_client(self):
        """Client user scoped to nova_client scope node only sees nova-fac in clients list."""
        resp = self._api(self.client_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        overview = resp.data['sections']['client_overview']
        codes = [c['code'] for c in overview['clients']]
        self.assertIn('nova-fac', codes)
        self.assertNotIn('other-cl', codes)
        self.assertEqual(overview['client_count'], 1)

    def test_06_client_user_gets_zero_hiring_and_talent(self):
        """Client user (no hiring_application.read / candidate.read) gets zeros."""
        resp = self._api(self.client_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        hiring = resp.data['sections']['hiring']
        talent = resp.data['sections']['talent']
        self.assertEqual(hiring['application_count'], 0)
        self.assertEqual(talent['candidate_count'], 0)
        self.assertEqual(talent['resume_count'], 0)


# ─── Tests 07–08: MRF section ─────────────────────────────────────────────────

class TestMRFSection(DashboardBase):

    def test_07_mrf_section_populated_with_mrf_read(self):
        """Admin user (mrf.read) sees non-zero MRF total after an MRF is created."""
        self._make_mrf(status='hr_review')
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        mrf = resp.data['sections']['mrf']
        self.assertGreater(mrf['total'], 0)
        self.assertGreater(mrf['in_review'], 0)

    def test_08_mrf_section_empty_without_mrf_read(self):
        """Field supervisor (no mrf.read) gets all-zero MRF section."""
        self._make_mrf(status='approved')
        resp = self._api(self.field_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        mrf = resp.data['sections']['mrf']
        self.assertEqual(mrf['total'], 0)
        self.assertEqual(mrf['approved'], 0)
        self.assertEqual(mrf['recent'], [])


# ─── Tests 09: Onboarding section ─────────────────────────────────────────────

class TestOnboardingSection(DashboardBase):

    def test_09_onboarding_counts_respect_scope(self):
        """Onboarding requests for this org are counted; other orgs are not."""
        ClientOnboardingRequest.objects.create(
            org=self.org, requested_by=self.admin_user,
            onboarding_type='new_client',
            proposed_client_name='Scoped Corp', proposed_client_code='scoped-corp',
            status='in_review',
        )
        # Onboarding in other_org — should NOT be visible to admin_user
        other_admin = User.objects.create_user(username='dash_other_admin', password='pass')
        other_admin.org = self.other_org
        other_admin.save()
        ClientOnboardingRequest.objects.create(
            org=self.other_org, requested_by=other_admin,
            onboarding_type='new_client',
            proposed_client_name='Other Corp', proposed_client_code='other-corp',
            status='approved',
        )

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        ob = resp.data['sections']['onboarding']
        self.assertGreaterEqual(ob['in_review'], 1)
        # The "other-corp" approved request must NOT be counted
        self.assertEqual(ob['approved'], 0)


# ─── Tests 10–11: my_work section ─────────────────────────────────────────────

class TestMyWorkSection(DashboardBase):

    def test_10_my_work_counts_active_assigned_steps(self):
        """Only active WorkflowStepInstances assigned to the user are counted."""
        mrf = self._make_mrf(status='hr_review')
        self._make_workflow_step(mrf, assigned_user=self.admin_user)

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        my_work = resp.data['sections']['my_work']
        self.assertGreaterEqual(my_work['active_task_count'], 1)
        self.assertTrue(
            any(t['target_type'] == 'mrf' for t in my_work['latest_tasks']),
        )

    def test_11_step_assigned_to_other_user_not_counted(self):
        """Steps assigned to client_user are not counted in admin_user's my_work."""
        mrf = self._make_mrf(status='hr_review')
        self._make_workflow_step(mrf, assigned_user=self.client_user)

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        my_work = resp.data['sections']['my_work']
        # admin_user should have 0 tasks (this step is assigned to client_user)
        self.assertEqual(my_work['active_task_count'], 0)


# ─── Tests 12–13: Budget and Hiring sections ──────────────────────────────────

class TestBudgetSection(DashboardBase):

    def test_12_budget_section_populated_with_budget_read(self):
        """Admin (budget.read) gets non-zero plan_count after a BudgetPlan is created."""
        import datetime
        BudgetPlan.objects.create(
            org=self.org, name='Test Budget', code='dash-budget-1',
            budget_nature='billable', budget_type='manpower',
            client=self.nova_client,
            period_start=datetime.date(2026, 1, 1),
            amount='100000.00', currency='INR', status='active', is_active=True,
        )
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        budget = resp.data['sections']['budget']
        self.assertGreaterEqual(budget['plan_count'], 1)
        self.assertNotEqual(budget['total_amount'], '0')

    def test_12b_budget_section_empty_without_budget_read(self):
        """Field supervisor (no budget.read) gets all-zero budget section."""
        resp = self._api(self.field_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        budget = resp.data['sections']['budget']
        self.assertEqual(budget['plan_count'], 0)
        self.assertEqual(budget['total_amount'], '0')


class TestHiringSection(DashboardBase):

    def test_13_hiring_section_empty_without_hiring_read(self):
        """Field supervisor (no hiring_application.read) gets all-zero hiring section."""
        resp = self._api(self.field_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        hiring = resp.data['sections']['hiring']
        self.assertEqual(hiring['application_count'], 0)
        self.assertEqual(hiring['demand_count'], 0)


# ─── Test 14: recent_activity ─────────────────────────────────────────────────

class TestRecentActivity(DashboardBase):

    def test_14_recent_activity_respects_scope(self):
        """recent_activity only contains items from the user's scope; max 10."""
        # Create an MRF in scope
        self._make_mrf(status='submitted')
        # Create an onboarding request in scope
        ClientOnboardingRequest.objects.create(
            org=self.org, requested_by=self.admin_user,
            onboarding_type='new_client',
            proposed_client_name='Activity Corp', proposed_client_code='act-corp',
            status='draft',
        )

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        activity = resp.data['sections']['recent_activity']
        self.assertIsInstance(activity, list)
        self.assertLessEqual(len(activity), 10)
        # All activity items must have type, id, title, status, created_at
        for item in activity:
            for key in ('type', 'id', 'title', 'status', 'created_at'):
                self.assertIn(key, item, f"Missing key '{key}' in activity item")
        # Check that only 'mrf' and 'onboarding' types appear
        types_seen = {item['type'] for item in activity}
        self.assertTrue(types_seen.issubset({'mrf', 'onboarding'}))

"""
apps/onboarding/tests/test_onboarding.py

Phase Client-Onboarding-A tests.

Scenarios:
  Model / CRUD:
  1.  Create onboarding request with valid data.
  2.  CRUD API: list requires client_onboarding.read.
  3.  CRUD API: create requires client_onboarding.create, sets requested_by from token.
  4.  CRUD API: update requires client_onboarding.update.
  5.  CRUD API: delete requires client_onboarding.delete.
  6.  Cross-org access blocked.

  Scope filtering:
  7.  User scoped to client-a cannot list client-b onboarding requests.
  8.  User scoped to client-a can list client-a onboarding requests.

  Workflow start:
  9.  Start workflow creates WorkflowInstance + step snapshots.
  10. Start workflow moves status to in_review.
  11. Workflow start is explicit — creating request does NOT auto-start workflow.
  12. Duplicate start returns 400.

  Config check:
  13. Config-check returns ok=true when template + all SACs configured.
  14. Config-check returns ok=false when no template mapping.
  15. Config-check returns ok=false when SAC missing for a step.

  Workflow outcomes:
  16. Final approval updates onboarding request status to approved.
  17. Rejection updates onboarding request status to rejected.

  Reassign + department safety:
  18. Reassign within department succeeds.
  19. Reassign across departments raises 400.

  MRF non-regression:
  20. Existing MRF workflow start still works after schema change.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode, Department
from apps.onboarding.models import ClientOnboardingRequest
from apps.sites.models import Client, SiteProfile
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig, WorkflowInstance,
)
from apps.workflow.services import (
    start_client_onboarding_workflow,
    start_mrf_workflow,
    act_on_step,
)
from apps.mrf.models import ManpowerRequest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, department=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass',
                                 is_superuser=is_superuser, is_staff=is_superuser)
    if org:
        u.org = org
    if department:
        u.department = department
    if org or department:
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


def _dept(org, name, code):
    return Department.objects.create(org=org, name=name, code=code)


def _onboarding(org, client, requested_by, onboarding_type='new_client'):
    return ClientOnboardingRequest.objects.create(
        org=org, client=client, requested_by=requested_by,
        onboarding_type=onboarding_type, status='draft',
    )


def _mrf_obj(org, site, requested_by):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status='submitted', billing_type='billable',
    )


def _ob_template(org, code='co-default'):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code,
        trigger_type='client_onboarding', version=1, is_active=True,
    )


def _ob_step(template, order, code):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        requires_comment_on_reject=False,
        requires_comment_on_request_changes=False,
    )


def _ob_mapping(org, template, client=None):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='client_onboarding', template=template,
        client=client, site=None, is_active=True,
    )


def _ob_sac(org, step_code, named_user, client=None, department=None):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='client_onboarding', step_code=step_code,
        client=client, site=None,
        assignment_mode='named_user', named_user=named_user,
        department=department, is_active=True,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class OBTestBase(TestCase):
    """
    Two-client org. client_a has a full workflow config; client_b has none.
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='OB Org', code='ob-org')
        cls.org2 = Organization.objects.create(name='OB Org2', code='ob-org2')

        # Scope nodes
        cls.n_org = _node(cls.org, 'ob-org', 'company', None, 0, 'ob-org')
        cls.n_cl_a = _node(cls.org, 'ob-cl-a', 'client', cls.n_org, 1, 'ob-org/ob-cl-a')
        cls.n_site_a = _node(cls.org, 'ob-site-a', 'site', cls.n_cl_a, 2, 'ob-org/ob-cl-a/ob-site-a')
        cls.n_cl_b = _node(cls.org, 'ob-cl-b', 'client', cls.n_org, 1, 'ob-org/ob-cl-b')

        cls.client_a = Client.objects.create(
            org=cls.org, name='OB Client A', code='ob-cl-a', scope_node=cls.n_cl_a,
        )
        cls.client_b = Client.objects.create(
            org=cls.org, name='OB Client B', code='ob-cl-b', scope_node=cls.n_cl_b,
        )
        cls.site_a = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, name='OB Site A', code='ob-site-a',
            scope_node=cls.n_site_a,
        )

        cls.dept_ops = _dept(cls.org, 'Operations', 'ob-ops')
        cls.dept_hr = _dept(cls.org, 'HR', 'ob-hr')

        # Roles
        cls.role_admin = _role(cls.org, 'admin')
        cls.role_sales = _role(cls.org, 'sales_manager')
        cls.role_ops = _role(cls.org, 'operations')

        # Users
        cls.admin = _user('ob_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_org)

        cls.sales_user = _user('ob_sales', org=cls.org)
        _assign(cls.sales_user, cls.role_sales, cls.n_org)

        # User scoped to client_a only
        cls.cl_a_user = _user('ob_cl_a_user', org=cls.org)
        _assign(cls.cl_a_user, cls.role_admin, cls.n_cl_a)

        # User scoped to client_b only
        cls.cl_b_user = _user('ob_cl_b_user', org=cls.org)
        _assign(cls.cl_b_user, cls.role_admin, cls.n_cl_b)

        cls.superuser = _user('ob_super', is_superuser=True)

        # Workflow staff (assigned to steps)
        cls.ops_worker = _user('ob_ops_worker', org=cls.org, department=cls.dept_ops)
        cls.hr_worker = _user('ob_hr_worker', org=cls.org, department=cls.dept_hr)

        # Workflow template + 2 steps + org-level mapping + SACs
        cls.template = _ob_template(cls.org)
        cls.step1 = _ob_step(cls.template, 1, 'ob_ops_review')
        cls.step2 = _ob_step(cls.template, 2, 'ob_hr_review')
        cls.mapping = _ob_mapping(cls.org, cls.template)
        cls.sac1 = _ob_sac(cls.org, 'ob_ops_review', cls.ops_worker)
        cls.sac2 = _ob_sac(cls.org, 'ob_hr_review', cls.hr_worker)

        # MRF workflow setup (for non-regression test)
        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='MRF Default', code='ob-mrf-default',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.mrf_step = WorkflowStepTemplate.objects.create(
            template=cls.mrf_template, order=1, code='ob_mrf_hr',
            name='HR Review', assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.mrf_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='ob_mrf_hr',
            assignment_mode='named_user', named_user=cls.hr_worker,
            is_active=True,
        )

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)

    def _list_url(self):
        return '/api/onboarding/client-requests/'

    def _detail_url(self, pk):
        return f'/api/onboarding/client-requests/{pk}/'

    def _start_url(self, ob_id):
        return f'/api/workflow/client-onboarding/{ob_id}/start/'

    def _config_check_url(self, ob_id):
        return f'/api/workflow/client-onboarding/{ob_id}/config-check/'

    def _instance_url(self, instance_id):
        return f'/api/workflow/instances/{instance_id}/'

    def _act_url(self, instance_id, step_id):
        return f'/api/workflow/instances/{instance_id}/steps/{step_id}/act/'


# ─── 1–6: Model / CRUD ────────────────────────────────────────────────────────

class TestOnboardingCRUD(OBTestBase):

    def test_create_onboarding_request(self):
        """Scenario 1: model create with valid data."""
        req = _onboarding(self.org, self.client_a, self.sales_user)
        self.assertEqual(req.status, 'draft')
        self.assertEqual(req.org_id, self.org.pk)
        self.assertEqual(req.client_id, self.client_a.pk)

    def test_list_requires_read_capability(self):
        """Scenario 2: list endpoint requires client_onboarding.read."""
        no_cap = _user('ob_nocap', org=self.org)
        role = _role(self.org, 'no_cap_role')
        _assign(no_cap, role, self.n_org)
        self._login(no_cap)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 403)

    def test_create_via_api_sets_requested_by(self):
        """Scenario 3: API create sets requested_by from the authenticated user."""
        self._login(self.sales_user)
        resp = self.api.post(self._list_url(), {
            'org': self.org.pk,
            'client': self.client_a.pk,
            'onboarding_type': 'new_client',
            'summary': 'Test onboarding',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        created = ClientOnboardingRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(created.requested_by_id, self.sales_user.pk)
        self.assertEqual(created.status, 'draft')

    def test_create_ignores_posted_org_and_uses_client_org(self):
        self._login(self.sales_user)
        resp = self.api.post(self._list_url(), {
            'org': self.org2.pk,
            'client': self.client_a.pk,
            'onboarding_type': 'new_client',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        created = ClientOnboardingRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(created.org_id, self.client_a.org_id)

    def test_create_rejects_client_outside_actor_scope(self):
        self._login(self.cl_a_user)
        resp = self.api.post(self._list_url(), {
            'client': self.client_b.pk,
            'onboarding_type': 'new_client',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_update_requires_update_capability(self):
        """Scenario 4: PATCH requires client_onboarding.update."""
        req = _onboarding(self.org, self.client_a, self.sales_user)
        ops_user = _user('ob_ops_only', org=self.org)
        _assign(ops_user, self.role_ops, self.n_org)
        self._login(ops_user)
        resp = self.api.patch(self._detail_url(req.pk), {'summary': 'Updated'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_delete_requires_delete_capability(self):
        """Scenario 5: DELETE requires client_onboarding.delete."""
        req = _onboarding(self.org, self.client_a, self.sales_user)
        self._login(self.sales_user)
        resp = self.api.delete(self._detail_url(req.pk))
        self.assertEqual(resp.status_code, 403)

    def test_cross_org_access_blocked(self):
        """Scenario 6: superuser of org2 cannot see org's requests (scope mismatch)."""
        _onboarding(self.org, self.client_a, self.sales_user)
        # user from org2 with no assignments to org
        n_org2 = _node(self.org2, 'ob-org2', 'company', None, 0, 'ob-org2')
        org2_user = _user('ob_org2_user', org=self.org2)
        role2 = _role(self.org2, 'admin')  # 'admin' has all capabilities
        _assign(org2_user, role2, n_org2)
        self._login(org2_user)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)


# ─── 7–8: Scope filtering ─────────────────────────────────────────────────────

class TestOnboardingScopeFiltering(OBTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.req_a = _onboarding(cls.org, cls.client_a, cls.admin, 'new_client')
        cls.req_b = _onboarding(cls.org, cls.client_b, cls.admin, 'new_site_expansion')

    def test_client_a_user_cannot_see_client_b_request(self):
        """Scenario 7: user scoped to client-a gets 404 for client-b request."""
        self._login(self.cl_a_user)
        resp = self.api.get(self._detail_url(self.req_b.pk))
        self.assertEqual(resp.status_code, 404)

    def test_client_a_user_can_see_client_a_request(self):
        """Scenario 8: user scoped to client-a sees client-a requests."""
        self._login(self.cl_a_user)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        ids = [item['id'] for item in resp.data['results']]
        self.assertIn(self.req_a.pk, ids)
        self.assertNotIn(self.req_b.pk, ids)


# ─── 9–12: Workflow start ─────────────────────────────────────────────────────

class TestOnboardingWorkflowStart(OBTestBase):

    def test_start_workflow_creates_instance_and_snapshots(self):
        """Scenario 9: start creates WorkflowInstance + all step snapshots."""
        req = _onboarding(self.org, self.client_a, self.admin)
        instance = start_client_onboarding_workflow(req, actor=self.admin)

        self.assertIsNotNone(instance.pk)
        self.assertEqual(instance.status, 'active')
        self.assertEqual(instance.client_onboarding_request_id, req.pk)
        self.assertIsNone(instance.mrf_id)

        steps = list(instance.steps.order_by('step_order'))
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].step_code, 'ob_ops_review')
        self.assertEqual(steps[0].assigned_user_id, self.ops_worker.pk)
        self.assertEqual(steps[1].step_code, 'ob_hr_review')
        self.assertEqual(steps[1].assigned_user_id, self.hr_worker.pk)

    def test_start_moves_status_to_in_review(self):
        """Scenario 10: start sets onboarding request status to in_review."""
        req = _onboarding(self.org, self.client_a, self.admin)
        start_client_onboarding_workflow(req, actor=self.admin)
        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')
        self.assertIsNotNone(req.submitted_at)

    def test_workflow_start_is_explicit_not_automatic(self):
        """Scenario 11: creating a request does not auto-start a workflow."""
        req = _onboarding(self.org, self.client_a, self.admin)
        self.assertEqual(WorkflowInstance.objects.filter(
            client_onboarding_request=req,
        ).count(), 0)

    def test_duplicate_start_raises(self):
        """Scenario 12: starting workflow twice raises WorkflowConfigurationError."""
        req = _onboarding(self.org, self.client_a, self.admin)
        start_client_onboarding_workflow(req, actor=self.admin)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            start_client_onboarding_workflow(req, actor=self.admin)
        self.assertIn('already has an active workflow', str(ctx.exception))

    def test_api_start_returns_201(self):
        """API start endpoint returns 201 with instance data."""
        req = _onboarding(self.org, self.client_a, self.admin)
        self._login(self.admin)
        resp = self.api.post(self._start_url(req.pk))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client_onboarding_request'], req.pk)
        self.assertIsNone(resp.data['mrf'])

    def test_api_start_out_of_scope_returns_404(self):
        """User scoped to client-b cannot start workflow for client-a request."""
        req = _onboarding(self.org, self.client_a, self.admin)
        self._login(self.cl_b_user)
        resp = self.api.post(self._start_url(req.pk))
        self.assertEqual(resp.status_code, 404)


# ─── 13–15: Config check ──────────────────────────────────────────────────────

class TestOnboardingConfigCheck(OBTestBase):

    def test_config_check_ok_true_when_complete(self):
        """Scenario 13: fully configured returns ok=true."""
        req = _onboarding(self.org, self.client_a, self.admin)
        self._login(self.admin)
        resp = self.api.get(self._config_check_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['ok'])
        self.assertEqual(len(resp.data['errors']), 0)
        step_codes = [s['step_code'] for s in resp.data['steps']]
        self.assertIn('ob_ops_review', step_codes)
        self.assertIn('ob_hr_review', step_codes)

    def test_config_check_missing_template_returns_ok_false(self):
        """Scenario 14: org with no template mapping returns ok=false."""
        org3 = Organization.objects.create(name='OB Org3', code='ob-org3')
        n3 = _node(org3, 'ob-org3', 'company', None, 0, 'ob-org3')
        n3c = _node(org3, 'ob3-cl', 'client', n3, 1, 'ob-org3/ob3-cl')
        cl3 = Client.objects.create(org=org3, name='CL3', code='ob3-cl', scope_node=n3c)
        actor3 = _user('ob_actor3', org=org3)
        role3 = _role(org3, 'admin')  # 'admin' has all capabilities
        _assign(actor3, role3, n3)

        req = ClientOnboardingRequest.objects.create(
            org=org3, client=cl3, requested_by=actor3,
            onboarding_type='new_client', status='draft',
        )
        self._login(actor3)
        resp = self.api.get(self._config_check_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertTrue(len(resp.data['errors']) > 0)
        self.assertIsNone(resp.data['template'])

    def test_config_check_missing_sac_returns_ok_false(self):
        """Scenario 15: template exists but no SAC for a step returns ok=false."""
        # Use a separate org that has a template but only partial SAC coverage
        org4 = Organization.objects.create(name='OB Org4', code='ob-org4')
        n4 = _node(org4, 'ob-org4', 'company', None, 0, 'ob-org4')
        n4c = _node(org4, 'ob4-cl', 'client', n4, 1, 'ob-org4/ob4-cl')
        cl4 = Client.objects.create(org=org4, name='CL4', code='ob4-cl', scope_node=n4c)
        actor4 = _user('ob_actor4', org=org4)
        role4 = _role(org4, 'admin')  # 'admin' maps to ALL_CAPABILITIES in ROLE_CAPABILITIES
        _assign(actor4, role4, n4)

        # Template with 2 steps; only 1 has an SAC
        t4 = _ob_template(org4, 'ob4-default')
        _ob_step(t4, 1, 'ob4_step1')
        _ob_step(t4, 2, 'ob4_step2_no_sac')
        _ob_mapping(org4, t4)
        worker4 = _user('ob4_worker', org=org4)
        _ob_sac(org4, 'ob4_step1', worker4)
        # ob4_step2_no_sac has no SAC → config check should fail

        req = ClientOnboardingRequest.objects.create(
            org=org4, client=cl4, requested_by=actor4,
            onboarding_type='new_client', status='draft',
        )
        self._login(actor4)
        resp = self.api.get(self._config_check_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertTrue(len(resp.data['errors']) > 0)


# ─── 16–17: Workflow outcomes ─────────────────────────────────────────────────

class TestOnboardingWorkflowOutcomes(OBTestBase):

    def _start(self):
        req = _onboarding(self.org, self.client_a, self.admin)
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        steps = {s.step_code: s for s in instance.steps.all()}
        return req, instance, steps

    def test_final_approval_sets_status_approved(self):
        """Scenario 16: approve all steps sets onboarding request to approved."""
        req, instance, steps = self._start()
        step1 = steps['ob_ops_review']
        step2 = steps['ob_hr_review']

        act_on_step(step1, actor=self.ops_worker, action='approve')
        act_on_step(step2, actor=self.hr_worker, action='approve')

        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertIsNotNone(req.approved_at)

        instance.refresh_from_db()
        self.assertEqual(instance.status, 'approved')

    def test_rejection_sets_status_rejected(self):
        """Scenario 17: rejecting a step sets onboarding request to rejected."""
        req, instance, steps = self._start()
        step1 = steps['ob_ops_review']

        act_on_step(step1, actor=self.ops_worker, action='reject')

        req.refresh_from_db()
        self.assertEqual(req.status, 'rejected')
        self.assertIsNotNone(req.rejected_at)

        instance.refresh_from_db()
        self.assertEqual(instance.status, 'rejected')

    def test_approve_moves_to_next_step(self):
        """Approving step 1 activates step 2."""
        req, instance, steps = self._start()
        step1 = steps['ob_ops_review']
        step2 = steps['ob_hr_review']

        act_on_step(step1, actor=self.ops_worker, action='approve')

        step2.refresh_from_db()
        self.assertEqual(step2.status, 'active')
        instance.refresh_from_db()
        self.assertEqual(instance.status, 'active')


# ─── 18–19: Reassign + department safety ─────────────────────────────────────

class TestOnboardingReassign(OBTestBase):

    def setUp(self):
        super().setUp()
        req = _onboarding(self.org, self.client_a, self.admin)
        self.instance = start_client_onboarding_workflow(req, actor=self.admin)
        self.active_step = self.instance.steps.filter(status='active').first()

    def test_reassign_within_department_via_api_succeeds(self):
        """Scenario 18: reassign to another user in same dept (no dept on step) succeeds."""
        # step has no dept — any same-org user works
        new_user = _user('ob_reassign_ok', org=self.org)
        self._login(self.admin)
        resp = self.api.post(
            f'/api/workflow/instances/{self.instance.pk}/steps/{self.active_step.pk}/reassign/',
            {'new_user': new_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.active_step.refresh_from_db()
        self.assertEqual(self.active_step.assigned_user_id, new_user.pk)

    def test_reassign_cross_department_raises_400_when_dept_set(self):
        """Scenario 19: step with dept=ops rejects user from hr dept."""
        # Add department to step via a fresh SAC
        sac_with_dept = StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='client_onboarding',
            step_code='ob_cross_dept_test',
            assignment_mode='named_user', named_user=self.ops_worker,
            department=self.dept_ops, is_active=True,
        )
        # Manually set dept on the active step to simulate dept-constrained step
        self.active_step.assigned_department = self.dept_ops
        self.active_step.assigned_department_name_snapshot = self.dept_ops.name
        self.active_step.save(update_fields=[
            'assigned_department', 'assigned_department_name_snapshot',
        ])

        self._login(self.admin)
        resp = self.api.post(
            f'/api/workflow/instances/{self.instance.pk}/steps/{self.active_step.pk}/reassign/',
            {'new_user': self.hr_worker.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('must belong to department', resp.data['detail'])


# ─── 20: MRF non-regression ───────────────────────────────────────────────────

class TestMRFNonRegression(OBTestBase):

    def test_mrf_workflow_still_starts_after_schema_change(self):
        """Scenario 20: existing MRF workflow start still works."""
        mrf = _mrf_obj(self.org, self.site_a, self.admin)
        instance = start_mrf_workflow(mrf, actor=self.admin)

        self.assertIsNotNone(instance.pk)
        self.assertEqual(instance.status, 'active')
        self.assertEqual(instance.mrf_id, mrf.pk)
        self.assertIsNone(instance.client_onboarding_request_id)

        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'hr_review')

    def test_workflow_instance_requires_exactly_one_target(self):
        instance = WorkflowInstance(
            org=self.org,
            template=self.template,
            template_version=1,
            initiated_by=self.admin,
            status='active',
        )
        with self.assertRaises(ValidationError):
            instance.full_clean()

        req = _onboarding(self.org, self.client_a, self.admin)
        mrf = _mrf_obj(self.org, self.site_a, self.admin)
        instance = WorkflowInstance(
            org=self.org,
            mrf=mrf,
            client_onboarding_request=req,
            template=self.template,
            template_version=1,
            initiated_by=self.admin,
            status='active',
        )
        with self.assertRaises(ValidationError):
            instance.full_clean()

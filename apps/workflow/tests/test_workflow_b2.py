"""
apps/workflow/tests/test_workflow_b2.py

Phase Workflow-B2 hardening tests — scope filtering + department scope validation.

Scenarios:
  Scope filtering (3 scenarios):
  1.  client_admin from client-b gets 404 for workflow on client-a MRF
  2.  client_admin from client-a can access workflow on client-a MRF
  3.  config-check returns 404 for out-of-scope MRF

  Department scope (12 scenarios):
  4.  site-level SAC with org-level dept  -> valid
  5.  site-level SAC with same-client-level dept -> valid
  6.  site-level SAC with same-site-level dept -> valid
  7.  site-level SAC with different-site dept -> invalid
  8.  site-level SAC with different-client dept -> invalid
  9.  client-level SAC with org-level dept -> valid
  10. client-level SAC with same-client dept -> valid
  11. client-level SAC with different-client dept -> invalid
  12. client-level SAC with site-level dept -> invalid
  13. org-level SAC with org-level dept -> valid
  14. org-level SAC with client-level dept -> invalid
  15. org-level SAC with site-level dept -> invalid
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.core.models import Organization, ScopeNode, Department
from apps.mrf.models import ManpowerRequest
from apps.sites.models import Client, SiteProfile
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    WorkflowInstance,
)
from apps.workflow.services import resolve_step_assignment, start_mrf_workflow


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


def _dept(org, name, code, client=None, site=None):
    return Department.objects.create(org=org, name=name, code=code, client=client, site=site)


def _template(org, code):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code, trigger_type='mrf', version=1, is_active=True,
    )


def _step(template, order, code):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        requires_comment_on_reject=True,
        requires_comment_on_request_changes=True,
    )


def _mapping(org, template, client=None, site=None):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='mrf', template=template,
        client=client, site=site, is_active=True,
    )


def _sac(org, step_code, named_user, department=None, client=None, site=None):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='mrf', step_code=step_code,
        client=client, site=site,
        assignment_mode='named_user', named_user=named_user,
        department=department,
        is_active=True,
    )


def _mrf(org, site, requested_by, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status=status,
        billing_type='billable',
    )


# ─── Base class ──────────────────────────────────────────────────────────────

class B2TestBase(TestCase):
    """
    Two-client org for scope tests.
    Org -> company_node
      -> client_a_node -> site_a_node
      -> client_b_node -> site_b_node
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='B2 Org', code='b2-org')

        # Scope tree
        cls.n_org = _node(cls.org, 'b2-org', 'company', None, 0, 'b2-org')
        cls.n_cl_a = _node(cls.org, 'b2-cl-a', 'client', cls.n_org, 1, 'b2-org/b2-cl-a')
        cls.n_site_a = _node(cls.org, 'b2-site-a', 'site', cls.n_cl_a, 2, 'b2-org/b2-cl-a/b2-site-a')
        cls.n_cl_b = _node(cls.org, 'b2-cl-b', 'client', cls.n_org, 1, 'b2-org/b2-cl-b')
        cls.n_site_b = _node(cls.org, 'b2-site-b', 'site', cls.n_cl_b, 2, 'b2-org/b2-cl-b/b2-site-b')

        # Clients and sites wired to scope nodes
        cls.client_a = Client.objects.create(
            org=cls.org, name='B2 Client A', code='b2-cl-a', scope_node=cls.n_cl_a,
        )
        cls.site_a = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, name='B2 Site A', code='b2-site-a',
            scope_node=cls.n_site_a,
        )
        cls.client_b = Client.objects.create(
            org=cls.org, name='B2 Client B', code='b2-cl-b', scope_node=cls.n_cl_b,
        )
        cls.site_b = SiteProfile.objects.create(
            org=cls.org, client=cls.client_b, name='B2 Site B', code='b2-site-b',
            scope_node=cls.n_site_b,
        )

        # Departments
        cls.dept_org = _dept(cls.org, 'Org Dept', 'b2-org-dept')
        cls.dept_cl_a = _dept(cls.org, 'Client A Dept', 'b2-cl-a-dept', client=cls.client_a)
        cls.dept_site_a = _dept(cls.org, 'Site A Dept', 'b2-site-a-dept', client=cls.client_a, site=cls.site_a)
        cls.dept_cl_b = _dept(cls.org, 'Client B Dept', 'b2-cl-b-dept', client=cls.client_b)
        cls.dept_site_b = _dept(cls.org, 'Site B Dept', 'b2-site-b-dept', client=cls.client_b, site=cls.site_b)

        # Roles
        cls.role_client_admin = _role(cls.org, 'client_admin')
        cls.role_workflow_admin = _role(cls.org, 'workflow_admin')
        cls.role_admin = _role(cls.org, 'admin')

        # Users scoped to client_a
        cls.user_a = _user('b2_user_a', org=cls.org, department=cls.dept_org)
        _assign(cls.user_a, cls.role_client_admin, cls.n_cl_a)

        # Users scoped to client_b
        cls.user_b = _user('b2_user_b', org=cls.org, department=cls.dept_org)
        _assign(cls.user_b, cls.role_client_admin, cls.n_cl_b)

        # Org-wide admin (to start workflows)
        cls.org_admin = _user('b2_org_admin', org=cls.org)
        _assign(cls.org_admin, cls.role_admin, cls.n_org)

        cls.superuser = _user('b2_super', is_superuser=True)

        # Workflow template + mapping (org-level)
        cls.template = _template(cls.org, 'b2-mrf-default')
        cls.step1 = _step(cls.template, 1, 'b2_hr_review')
        _mapping(cls.org, cls.template)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)

    def _workflow_url(self, mrf_id):
        return f'/api/workflow/mrf/{mrf_id}/start/'

    def _instance_url(self, instance_id):
        return f'/api/workflow/instances/{instance_id}/'

    def _config_check_url(self, mrf_id):
        return f'/api/workflow/mrf/{mrf_id}/config-check/'


# ─── Scope filtering (scenarios 1–3) ─────────────────────────────────────────

class TestScopeFiltering(B2TestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # MRF on site_a (client-a)
        cls.mrf_a = _mrf(cls.org, cls.site_a, cls.org_admin)

        # SAC for step so workflow can be started
        cls.wf_user = _user('b2_wf_user', org=cls.org, department=cls.dept_org)
        cls.sac = _sac(cls.org, 'b2_hr_review', cls.wf_user)

    def _start_and_get_instance_id(self):
        """Start workflow on mrf_a as superuser; return instance id."""
        self.api.force_authenticate(user=self.superuser)
        resp = self.api.post(self._workflow_url(self.mrf_a.pk))
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data['id']

    def test_out_of_scope_user_gets_404_on_workflow_instance(self):
        """Scenario 1: user_b (client-b scope) cannot see client-a workflow instance."""
        instance_id = self._start_and_get_instance_id()
        self._login(self.user_b)
        resp = self.api.get(self._instance_url(instance_id))
        self.assertEqual(resp.status_code, 404)

    def test_in_scope_user_can_access_workflow_instance(self):
        """Scenario 2: user_a (client-a scope) can read the client-a workflow instance."""
        # Grant workflow.read to client_admin role
        from apps.access.models import Permission, AccessRolePermission
        perm, _ = Permission.objects.get_or_create(
            resource='workflow', action='read',
            defaults={'description': 'Read workflow'},
        )
        AccessRolePermission.objects.get_or_create(role=self.role_client_admin, permission=perm)

        instance_id = self._start_and_get_instance_id()
        self._login(self.user_a)
        resp = self.api.get(self._instance_url(instance_id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], instance_id)

    def test_out_of_scope_user_gets_404_on_config_check(self):
        """Scenario 3: user_b cannot call config-check for client-a MRF."""
        from apps.access.models import Permission, AccessRolePermission
        perm, _ = Permission.objects.get_or_create(
            resource='workflow', action='read',
            defaults={'description': 'Read workflow'},
        )
        AccessRolePermission.objects.get_or_create(role=self.role_client_admin, permission=perm)

        self._login(self.user_b)
        resp = self.api.get(self._config_check_url(self.mrf_a.pk))
        self.assertEqual(resp.status_code, 404)


# ─── Department scope validation — service layer (scenarios 4–15) ─────────────

class TestDepartmentScopeValidation(B2TestBase):
    """
    Tests resolve_step_assignment() department scope checks directly.
    Uses unique step codes per test to avoid SAC collisions.
    """

    # ── Site-level SAC ────────────────────────────────────────────────────────

    def test_site_sac_org_level_dept_valid(self):
        """Scenario 4: site-level SAC with org-level dept is accepted."""
        user = _user('b2_s4', org=self.org, department=self.dept_org)
        _sac(self.org, 'b2_s4_step', user,
             department=self.dept_org, client=self.client_a, site=self.site_a)
        config = resolve_step_assignment(
            'mrf', self.org, 'b2_s4_step',
            client=self.client_a, site=self.site_a,
        )
        self.assertEqual(config.department_id, self.dept_org.pk)

    def test_site_sac_same_client_dept_valid(self):
        """Scenario 5: site-level SAC with same-client dept is accepted."""
        user = _user('b2_s5', org=self.org, department=self.dept_cl_a)
        _sac(self.org, 'b2_s5_step', user,
             department=self.dept_cl_a, client=self.client_a, site=self.site_a)
        config = resolve_step_assignment(
            'mrf', self.org, 'b2_s5_step',
            client=self.client_a, site=self.site_a,
        )
        self.assertEqual(config.department_id, self.dept_cl_a.pk)

    def test_site_sac_same_site_dept_valid(self):
        """Scenario 6: site-level SAC with same-site dept is accepted."""
        user = _user('b2_s6', org=self.org, department=self.dept_site_a)
        _sac(self.org, 'b2_s6_step', user,
             department=self.dept_site_a, client=self.client_a, site=self.site_a)
        config = resolve_step_assignment(
            'mrf', self.org, 'b2_s6_step',
            client=self.client_a, site=self.site_a,
        )
        self.assertEqual(config.department_id, self.dept_site_a.pk)

    def test_site_sac_different_site_dept_raises(self):
        """Scenario 7: site-level SAC with dept from site_b raises error."""
        user = _user('b2_s7', org=self.org, department=self.dept_site_b)
        _sac(self.org, 'b2_s7_step', user,
             department=self.dept_site_b, client=self.client_a, site=self.site_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment(
                'mrf', self.org, 'b2_s7_step',
                client=self.client_a, site=self.site_a,
            )
        self.assertIn('different site', str(ctx.exception))

    def test_site_sac_different_client_dept_raises(self):
        """Scenario 8: site-level SAC with client-b dept raises error."""
        user = _user('b2_s8', org=self.org, department=self.dept_cl_b)
        _sac(self.org, 'b2_s8_step', user,
             department=self.dept_cl_b, client=self.client_a, site=self.site_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment(
                'mrf', self.org, 'b2_s8_step',
                client=self.client_a, site=self.site_a,
            )
        self.assertIn('different client', str(ctx.exception))

    # ── Client-level SAC ──────────────────────────────────────────────────────

    def test_client_sac_org_level_dept_valid(self):
        """Scenario 9: client-level SAC with org-level dept is accepted."""
        user = _user('b2_s9', org=self.org, department=self.dept_org)
        _sac(self.org, 'b2_s9_step', user,
             department=self.dept_org, client=self.client_a)
        config = resolve_step_assignment(
            'mrf', self.org, 'b2_s9_step',
            client=self.client_a,
        )
        self.assertEqual(config.department_id, self.dept_org.pk)

    def test_client_sac_same_client_dept_valid(self):
        """Scenario 10: client-level SAC with same-client dept is accepted."""
        user = _user('b2_s10', org=self.org, department=self.dept_cl_a)
        _sac(self.org, 'b2_s10_step', user,
             department=self.dept_cl_a, client=self.client_a)
        config = resolve_step_assignment(
            'mrf', self.org, 'b2_s10_step',
            client=self.client_a,
        )
        self.assertEqual(config.department_id, self.dept_cl_a.pk)

    def test_client_sac_different_client_dept_raises(self):
        """Scenario 11: client-a SAC with client-b dept raises error."""
        user = _user('b2_s11', org=self.org, department=self.dept_cl_b)
        _sac(self.org, 'b2_s11_step', user,
             department=self.dept_cl_b, client=self.client_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment(
                'mrf', self.org, 'b2_s11_step',
                client=self.client_a,
            )
        self.assertIn('incompatible', str(ctx.exception))

    def test_client_sac_site_level_dept_raises(self):
        """Scenario 12: client-level SAC with site-level dept raises error."""
        user = _user('b2_s12', org=self.org, department=self.dept_site_a)
        _sac(self.org, 'b2_s12_step', user,
             department=self.dept_site_a, client=self.client_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment(
                'mrf', self.org, 'b2_s12_step',
                client=self.client_a,
            )
        self.assertIn('incompatible', str(ctx.exception))

    # ── Org-level SAC ─────────────────────────────────────────────────────────

    def test_org_sac_org_level_dept_valid(self):
        """Scenario 13: org-level SAC with org-level dept is accepted."""
        user = _user('b2_s13', org=self.org, department=self.dept_org)
        _sac(self.org, 'b2_s13_step', user, department=self.dept_org)
        config = resolve_step_assignment('mrf', self.org, 'b2_s13_step')
        self.assertEqual(config.department_id, self.dept_org.pk)

    def test_org_sac_client_level_dept_raises(self):
        """Scenario 14: org-level SAC with client-scoped dept raises error."""
        user = _user('b2_s14', org=self.org, department=self.dept_cl_a)
        _sac(self.org, 'b2_s14_step', user, department=self.dept_cl_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment('mrf', self.org, 'b2_s14_step')
        self.assertIn('org-level', str(ctx.exception))

    def test_org_sac_site_level_dept_raises(self):
        """Scenario 15: org-level SAC with site-scoped dept raises error."""
        user = _user('b2_s15', org=self.org, department=self.dept_site_a)
        _sac(self.org, 'b2_s15_step', user, department=self.dept_site_a)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment('mrf', self.org, 'b2_s15_step')
        self.assertIn('org-level', str(ctx.exception))


# ─── Department scope validation — model clean() (B2 extra coverage) ──────────

class TestDepartmentScopeClean(B2TestBase):
    """
    Validates that StepAssignmentConfig.clean() enforces scope rules
    before hitting the DB at all (admin/form-level protection).
    """

    def _make_sac(self, step_code, user, department, client=None, site=None):
        return StepAssignmentConfig(
            org=self.org,
            trigger_type='mrf',
            step_code=step_code,
            assignment_mode='named_user',
            named_user=user,
            department=department,
            client=client,
            site=site,
            is_active=True,
        )

    def test_clean_site_sac_wrong_site_dept_raises(self):
        user = _user('b2_c1', org=self.org, department=self.dept_site_b)
        sac = self._make_sac('b2_c1_step', user, self.dept_site_b,
                             client=self.client_a, site=self.site_a)
        with self.assertRaises(DjangoValidationError) as ctx:
            sac.clean()
        self.assertIn('department', ctx.exception.message_dict)

    def test_clean_client_sac_site_dept_raises(self):
        user = _user('b2_c2', org=self.org, department=self.dept_site_a)
        sac = self._make_sac('b2_c2_step', user, self.dept_site_a, client=self.client_a)
        with self.assertRaises(DjangoValidationError) as ctx:
            sac.clean()
        self.assertIn('department', ctx.exception.message_dict)

    def test_clean_org_sac_client_dept_raises(self):
        user = _user('b2_c3', org=self.org, department=self.dept_cl_a)
        sac = self._make_sac('b2_c3_step', user, self.dept_cl_a)
        with self.assertRaises(DjangoValidationError) as ctx:
            sac.clean()
        self.assertIn('department', ctx.exception.message_dict)

    def test_clean_org_sac_org_dept_passes(self):
        user = _user('b2_c4', org=self.org, department=self.dept_org)
        sac = self._make_sac('b2_c4_step', user, self.dept_org)
        sac.clean()  # Must not raise

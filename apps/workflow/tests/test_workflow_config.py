"""
apps/workflow/tests/test_workflow_config.py

Phase Workflow-Config-A tests.

Scenarios:
  Capabilities:
  1.  Read endpoint returns 403 without workflow.config.read.
  2.  Write endpoint returns 403 with only workflow.config.read.

  Approval Flows:
  3.  Admin can list flows.
  4.  Admin can create a flow.
  5.  Admin can update a flow name.
  6.  Destroy soft-deactivates (is_active=False, returns 204).
  7.  Duplicate code in same org returns 400.
  8.  Invalid trigger_type returns 400.

  Approval Steps:
  9.  Create step with valid data succeeds.
  10. Update step name via PATCH succeeds.
  11. Duplicate order within template returns 400.
  12. Duplicate code within template returns 400.
  13. Invalid transition target (on_approve_next) returns 400.
  14. Delete blocked if step has workflow step instances; returns 400.
  15. Delete with no instances hard-deletes.

  Flow Rules:
  16. Create company-default rule succeeds.
  17. Create client-specific rule succeeds.
  18. Create site-specific rule succeeds.
  19. Duplicate active company rule blocked.
  20. Template trigger mismatch returns 400.
  21. site.client ≠ client mismatch returns 400.
  22. Soft-delete allows creating a replacement rule.

  Assignment Configs:
  23. Create valid named-user assignment succeeds.
  24. Inactive user rejected.
  25. User in wrong department rejected.
  26. Wrong department scope (client-scoped dept on org-level SAC) rejected.
  27. Duplicate active assignment blocked.
  28. Soft-delete allows replacement.

  Config Preview:
  29. Org-default preview returns ok=true with 5 steps.
  30. Client-specific template overrides org default in preview.
  31. Missing template returns ok=false.
  32. Missing SAC for one step returns ok=false.
  33. request_type parameter is required.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Department, Organization, ScopeNode
from apps.mobilisation.models import MobilisationSetupRequest
from apps.sites.models import Client, SiteProfile
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowInstance,
    WorkflowStepInstance,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)


# ─── Base ─────────────────────────────────────────────────────────────────────

class ConfigTestBase(TestCase):
    """
    One org with company/client/site scope tree.
    Two capability levels: manage (hr_admin) and read-only (finance).
    Pre-built template + 2 steps + org-default mapping + 2 SACs for preview tests.
    """

    BASE = '/api/workflow'

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Config Test Org', code='cfg-test')

        # Scope nodes
        cls.n_company = ScopeNode.objects.create(
            org=cls.org, code='cfg-test', name='Cfg Test', node_type='company',
            parent=None, depth=0, path='cfg-test', is_active=True,
        )
        cls.n_client = ScopeNode.objects.create(
            org=cls.org, code='cfg-client', name='Cfg Client', node_type='client',
            parent=cls.n_company, depth=1, path='cfg-test/cfg-client', is_active=True,
        )
        cls.n_site = ScopeNode.objects.create(
            org=cls.org, code='cfg-site', name='Cfg Site', node_type='site',
            parent=cls.n_client, depth=2, path='cfg-test/cfg-client/cfg-site', is_active=True,
        )

        # Client + Site
        cls.wf_client = Client.objects.create(
            org=cls.org, name='Cfg Client Ltd', code='CFG-CLIENT', scope_node=cls.n_client,
        )
        cls.wf_site = SiteProfile.objects.create(
            org=cls.org, client=cls.wf_client,
            name='Cfg Site A', code='CFG-SITE', scope_node=cls.n_site,
        )

        # A second client for cross-client mismatch tests
        cls.n_client2 = ScopeNode.objects.create(
            org=cls.org, code='cfg-client2', name='Cfg Client 2', node_type='client',
            parent=cls.n_company, depth=1, path='cfg-test/cfg-client2', is_active=True,
        )
        cls.wf_client2 = Client.objects.create(
            org=cls.org, name='Cfg Client 2', code='CFG-CLIENT2', scope_node=cls.n_client2,
        )

        # Departments
        cls.dept_ops = Department.objects.create(org=cls.org, code='ops', name='Operations')
        cls.dept_hr = Department.objects.create(org=cls.org, code='hr', name='HR')
        # A client-scoped department (not org-level)
        cls.dept_client_scoped = Department.objects.create(
            org=cls.org, client=cls.wf_client, code='ops-client', name='Ops Client',
        )

        # Roles
        role_admin, _ = AccessRole.objects.get_or_create(
            org=cls.org, code='admin', defaults={'name': 'Admin'},
        )
        role_finance, _ = AccessRole.objects.get_or_create(
            org=cls.org, code='finance', defaults={'name': 'Finance'},
        )
        role_field, _ = AccessRole.objects.get_or_create(
            org=cls.org, code='field_supervisor', defaults={'name': 'Field Supervisor'},
        )

        # DB permissions for runtime capability lookup
        bootstrap_role_permissions(role_admin)
        bootstrap_role_permissions(role_finance)
        bootstrap_role_permissions(role_field)

        # Users
        cls.u_manage = cls._make_user('cfg.manage', role_admin)
        cls.u_read = cls._make_user('cfg.read', role_finance)
        cls.u_no_cap = cls._make_user('cfg.nocap', role_field)

        # A user in the Ops dept (for SAC assignment tests)
        cls.u_ops = cls._make_user('cfg.ops', role_admin, dept=cls.dept_ops)

        # An inactive user
        cls.u_inactive = cls._make_user('cfg.inactive', role_admin, is_active=False)

        # Pre-built template for steps/rules/preview tests
        cls.template = WorkflowTemplate.objects.create(
            org=cls.org, name='CFG Template', code='cfg-tmpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        cls.step1 = WorkflowStepTemplate.objects.create(
            template=cls.template, order=1, code='step-a', name='Step A',
            assignment_mode='named_user', actor_type='internal',
        )
        cls.step2 = WorkflowStepTemplate.objects.create(
            template=cls.template, order=2, code='step-b', name='Step B',
            assignment_mode='named_user', actor_type='internal',
        )

        # A second template with a different trigger_type (mrf) for mismatch tests
        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='MRF Template', code='cfg-mrf-tmpl',
            trigger_type='mrf', version=1, is_active=True,
        )

        # Org-default mapping for preview tests
        cls.default_mapping = WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='client_onboarding',
            template=cls.template, client=None, site=None, is_active=True,
        )

        # SACs for all steps (preview ok=true)
        cls.sac1 = StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='client_onboarding', step_code='step-a',
            assignment_mode='named_user', named_user=cls.u_ops,
            department=cls.dept_ops, is_active=True,
        )
        cls.sac2 = StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='client_onboarding', step_code='step-b',
            assignment_mode='named_user', named_user=cls.u_manage, is_active=True,
        )

    @classmethod
    def _make_user(cls, username, role, dept=None, is_active=True):
        u = User.objects.create_user(username=username, password='pass', is_active=is_active)
        u.org = cls.org
        if dept:
            u.department = dept
        u.save()
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=cls.n_company)
        return u

    def _api(self, user=None):
        c = APIClient()
        c.force_authenticate(user=user or self.u_manage)
        return c

    # URL helpers
    def _flows_list(self):
        return f'{self.BASE}/config/flows/'

    def _flow_detail(self, pk):
        return f'{self.BASE}/config/flows/{pk}/'

    def _steps_list(self):
        return f'{self.BASE}/config/steps/'

    def _step_detail(self, pk):
        return f'{self.BASE}/config/steps/{pk}/'

    def _rules_list(self):
        return f'{self.BASE}/config/rules/'

    def _rule_detail(self, pk):
        return f'{self.BASE}/config/rules/{pk}/'

    def _assignments_list(self):
        return f'{self.BASE}/config/assignments/'

    def _assignment_detail(self, pk):
        return f'{self.BASE}/config/assignments/{pk}/'

    def _preview(self, **params):
        return f'{self.BASE}/config/preview/'


# ─── Capabilities ─────────────────────────────────────────────────────────────

class TestConfigCapabilities(ConfigTestBase):

    def test_read_requires_workflow_config_read(self):
        """Scenario 1: user without any config capability gets 403 on list."""
        api = self._api(self.u_no_cap)
        resp = api.get(self._flows_list())
        self.assertEqual(resp.status_code, 403)

    def test_write_requires_workflow_config_manage(self):
        """Scenario 2: user with only read capability gets 403 on create."""
        api = self._api(self.u_read)
        resp = api.post(self._flows_list(), {
            'name': 'New Flow', 'code': 'new-flow', 'trigger_type': 'mrf',
            'version': 1, 'description': '', 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_read_user_can_list(self):
        """finance role has workflow.config.read → 200 on list."""
        api = self._api(self.u_read)
        resp = api.get(self._flows_list())
        self.assertEqual(resp.status_code, 200)

    def test_manage_user_can_create(self):
        """admin role has workflow.config.manage → 201 on create."""
        api = self._api(self.u_manage)
        resp = api.post(self._flows_list(), {
            'name': 'Cap Test Flow', 'code': 'cap-test-flow',
            'trigger_type': 'mrf', 'version': 1, 'description': '', 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201)


# ─── Approval Flows ───────────────────────────────────────────────────────────

class TestApprovalFlowAPI(ConfigTestBase):

    def test_list_returns_org_flows(self):
        """Scenario 3: list returns templates for user's org only."""
        api = self._api()
        resp = api.get(self._flows_list())
        self.assertEqual(resp.status_code, 200)
        codes = [r['code'] for r in resp.data['results']]
        self.assertIn('cfg-tmpl', codes)
        self.assertIn('cfg-mrf-tmpl', codes)

    def test_create_flow(self):
        """Scenario 4: create new WorkflowTemplate."""
        api = self._api()
        resp = api.post(self._flows_list(), {
            'name': 'New Onboarding Flow', 'code': 'new-ob-flow',
            'trigger_type': 'client_onboarding', 'version': 2,
            'description': 'Test flow', 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['code'], 'new-ob-flow')
        self.assertEqual(resp.data['org'], self.org.pk)

    def test_update_flow_name(self):
        """Scenario 5: PATCH name of an existing template."""
        api = self._api()
        resp = api.patch(self._flow_detail(self.template.pk), {'name': 'Renamed'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.template.refresh_from_db()
        self.assertEqual(self.template.name, 'Renamed')

    def test_destroy_soft_deactivates(self):
        """Scenario 6: DELETE sets is_active=False, returns 204."""
        tpl = WorkflowTemplate.objects.create(
            org=self.org, name='To Deactivate', code='to-deact',
            trigger_type='mrf', version=1, is_active=True,
        )
        api = self._api()
        resp = api.delete(self._flow_detail(tpl.pk))
        self.assertEqual(resp.status_code, 204)
        tpl.refresh_from_db()
        self.assertFalse(tpl.is_active)

    def test_duplicate_code_returns_400(self):
        """Scenario 7: code unique per org."""
        api = self._api()
        resp = api.post(self._flows_list(), {
            'name': 'Dup', 'code': 'cfg-tmpl',
            'trigger_type': 'client_onboarding', 'version': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', resp.data)

    def test_invalid_trigger_type_returns_400(self):
        """Scenario 8: unknown trigger_type rejected."""
        api = self._api()
        resp = api.post(self._flows_list(), {
            'name': 'Bad Type', 'code': 'bad-type',
            'trigger_type': 'not_a_type', 'version': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('trigger_type', resp.data)


# ─── Approval Steps ───────────────────────────────────────────────────────────

class TestApprovalStepAPI(ConfigTestBase):

    def test_create_step(self):
        """Scenario 9: create a new step on the existing template."""
        api = self._api()
        resp = api.post(self._steps_list(), {
            'template': self.template.pk, 'order': 3, 'code': 'step-c',
            'name': 'Step C', 'assignment_mode': 'named_user', 'actor_type': 'internal',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['code'], 'step-c')
        self.assertEqual(resp.data['template_code'], 'cfg-tmpl')

    def test_update_step_name(self):
        """Scenario 10: PATCH name via partial update."""
        api = self._api()
        resp = api.patch(self._step_detail(self.step1.pk), {'name': 'Renamed Step'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.step1.refresh_from_db()
        self.assertEqual(self.step1.name, 'Renamed Step')

    def test_duplicate_order_returns_400(self):
        """Scenario 11: order unique within template."""
        api = self._api()
        resp = api.post(self._steps_list(), {
            'template': self.template.pk, 'order': 1, 'code': 'step-dup-order',
            'name': 'Dup Order', 'assignment_mode': 'named_user', 'actor_type': 'internal',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('order', resp.data)

    def test_duplicate_code_returns_400(self):
        """Scenario 12: code unique within template."""
        api = self._api()
        resp = api.post(self._steps_list(), {
            'template': self.template.pk, 'order': 9, 'code': 'step-a',
            'name': 'Dup Code', 'assignment_mode': 'named_user', 'actor_type': 'internal',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', resp.data)

    def test_invalid_transition_target_returns_400(self):
        """Scenario 13: on_approve_next references non-existent step code."""
        api = self._api()
        resp = api.post(self._steps_list(), {
            'template': self.template.pk, 'order': 8, 'code': 'step-bad-trans',
            'name': 'Bad Transition', 'assignment_mode': 'named_user',
            'actor_type': 'internal', 'on_approve_next': 'step-does-not-exist',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('on_approve_next', resp.data)

    def test_delete_blocked_if_step_instance_exists(self):
        """Scenario 14: step with step instances cannot be deleted."""
        # Create a minimal onboarding request + workflow instance + step instance
        req = MobilisationSetupRequest.objects.create(
            org=self.org, client=self.wf_client, requested_by=self.u_manage,
            mobilisation_type='new_client', status='draft',
        )
        wi = WorkflowInstance.objects.create(
            org=self.org, client_onboarding_request=req,
            template=self.template, template_version=1,
            status='active', initiated_by=self.u_manage,
        )
        WorkflowStepInstance.objects.create(
            workflow=wi, step_template=self.step1,
            step_order=1, step_code=self.step1.code, step_name=self.step1.name,
            assignment_mode='named_user', actor_type='internal',
            on_approve_next='', on_reject_target='', on_request_changes_target='',
            requires_comment_on_reject=True, requires_comment_on_request_changes=True,
            status='active',
        )
        api = self._api()
        resp = api.delete(self._step_detail(self.step1.pk))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('step instances', resp.data['detail'])

    def test_delete_step_with_no_instances(self):
        """Scenario 15: step with no instances is hard-deleted."""
        step = WorkflowStepTemplate.objects.create(
            template=self.template, order=50, code='step-deletable',
            name='Deletable', assignment_mode='named_user', actor_type='internal',
        )
        api = self._api()
        resp = api.delete(self._step_detail(step.pk))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(WorkflowStepTemplate.objects.filter(pk=step.pk).exists())


# ─── Flow Rules ───────────────────────────────────────────────────────────────

class TestFlowRuleAPI(ConfigTestBase):

    def _create_rule(self, **kwargs):
        api = self._api()
        payload = {'trigger_type': 'mrf', 'template': self.mrf_template.pk, 'is_active': True}
        payload.update(kwargs)
        return api.post(self._rules_list(), payload, format='json')

    def test_create_company_default_rule(self):
        """Scenario 16: company-level mapping (no client, no site)."""
        resp = self._create_rule()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['mapping_level'], 'company')
        self.assertIsNone(resp.data['client'])
        self.assertIsNone(resp.data['site'])

    def test_create_client_specific_rule(self):
        """Scenario 17: client-level mapping."""
        # Use a different trigger_type to avoid conflict with company-level above
        api = self._api()
        tpl = WorkflowTemplate.objects.create(
            org=self.org, name='MRF2', code='mrf-tpl2',
            trigger_type='mrf', version=1, is_active=True,
        )
        resp = api.post(self._rules_list(), {
            'trigger_type': 'mrf', 'template': tpl.pk,
            'client': self.wf_client.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['mapping_level'], 'client')
        self.assertEqual(resp.data['client'], self.wf_client.pk)
        self.assertIsNone(resp.data['site'])

    def test_create_site_specific_rule(self):
        """Scenario 18: site-level mapping."""
        api = self._api()
        tpl = WorkflowTemplate.objects.create(
            org=self.org, name='MRF3', code='mrf-tpl3',
            trigger_type='mrf', version=1, is_active=True,
        )
        resp = api.post(self._rules_list(), {
            'trigger_type': 'mrf', 'template': tpl.pk,
            'client': self.wf_client.pk, 'site': self.wf_site.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['mapping_level'], 'site')
        self.assertEqual(resp.data['site'], self.wf_site.pk)

    def test_duplicate_active_company_rule_blocked(self):
        """Scenario 19: two active company-level rules for same trigger_type blocked."""
        # The default_mapping in setUpTestData is client_onboarding + org-level
        api = self._api()
        resp = api.post(self._rules_list(), {
            'trigger_type': 'client_onboarding', 'template': self.template.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already exists', str(resp.data))

    def test_template_trigger_mismatch_blocked(self):
        """Scenario 20: rule trigger_type != template trigger_type → 400."""
        resp = self._create_rule(
            trigger_type='client_onboarding',
            template=self.mrf_template.pk,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('template', resp.data)

    def test_site_client_mismatch_blocked(self):
        """Scenario 21: site belongs to client2, but rule sets client=wf_client → 400."""
        # Create a site under wf_client2
        n2 = ScopeNode.objects.create(
            org=self.org, code='cfg-site2', name='Cfg Site 2', node_type='site',
            parent=self.n_client2, depth=2, path='cfg-test/cfg-client2/cfg-site2', is_active=True,
        )
        site2 = SiteProfile.objects.create(
            org=self.org, client=self.wf_client2,
            name='Site2', code='CFG-SITE2', scope_node=n2,
        )
        tpl = WorkflowTemplate.objects.create(
            org=self.org, name='MRF Mismatch', code='mrf-mismatch',
            trigger_type='mrf', version=1, is_active=True,
        )
        api = self._api()
        resp = api.post(self._rules_list(), {
            'trigger_type': 'mrf', 'template': tpl.pk,
            'client': self.wf_client.pk, 'site': site2.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', resp.data)

    def test_soft_delete_allows_replacement(self):
        """Scenario 22: deactivating a rule allows creating a new active one at same level."""
        # Deactivate the pre-existing org-default mapping
        api = self._api()
        api.delete(self._rule_detail(self.default_mapping.pk))
        self.default_mapping.refresh_from_db()
        self.assertFalse(self.default_mapping.is_active)

        # Now create a replacement
        resp = api.post(self._rules_list(), {
            'trigger_type': 'client_onboarding', 'template': self.template.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── Assignment Configs ───────────────────────────────────────────────────────

class TestAssignmentConfigAPI(ConfigTestBase):

    def test_create_valid_named_user_assignment(self):
        """Scenario 23: create valid SAC for step-a with u_ops (Ops dept)."""
        # Delete the existing sac1 first to avoid duplicate
        self.sac1.delete()
        api = self._api()
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-a',
            'assignment_mode': 'named_user', 'named_user': self.u_ops.pk,
            'department': self.dept_ops.pk, 'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['step_code'], 'step-a')
        self.assertEqual(resp.data['named_user'], self.u_ops.pk)
        self.assertEqual(resp.data['assignment_level'], 'company')

    def test_inactive_user_rejected(self):
        """Scenario 24: inactive user → 400."""
        api = self._api()
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-z',
            'assignment_mode': 'named_user', 'named_user': self.u_inactive.pk,
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('named_user', resp.data)

    def test_user_wrong_department_rejected(self):
        """Scenario 25: named_user.department ≠ SAC department → 400."""
        api = self._api()
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-z',
            'assignment_mode': 'named_user', 'named_user': self.u_manage.pk,
            'department': self.dept_ops.pk,  # u_manage has no dept
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_client_scoped_dept_on_org_level_sac_rejected(self):
        """Scenario 26: client-scoped department on org-level SAC → 400."""
        api = self._api()
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-scope-test',
            'assignment_mode': 'named_user', 'named_user': self.u_ops.pk,
            'department': self.dept_client_scoped.pk,  # client-scoped, not org-level
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_active_assignment_blocked(self):
        """Scenario 27: second active org-level SAC for same step_code → 400."""
        # sac1 already exists for step-a
        api = self._api()
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-a',
            'assignment_mode': 'named_user', 'named_user': self.u_manage.pk,
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already exists', str(resp.data))

    def test_soft_delete_allows_replacement(self):
        """Scenario 28: deactivating SAC allows creating a new active one."""
        api = self._api()
        # Soft-delete sac2
        api.delete(self._assignment_detail(self.sac2.pk))
        self.sac2.refresh_from_db()
        self.assertFalse(self.sac2.is_active)

        # Create replacement
        resp = api.post(self._assignments_list(), {
            'trigger_type': 'client_onboarding', 'step_code': 'step-b',
            'assignment_mode': 'named_user', 'named_user': self.u_ops.pk,
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── Config Preview ───────────────────────────────────────────────────────────

class TestConfigPreviewAPI(ConfigTestBase):

    def _get_preview(self, user=None, **params):
        api = self._api(user)
        return api.get(self._preview(), params)

    def test_org_default_preview_ok_true(self):
        """Scenario 29: org-level default resolves all steps → ok=true."""
        resp = self._get_preview(request_type='client_onboarding')
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertTrue(data['ok'], data.get('errors'))
        self.assertEqual(data['request_type'], 'client_onboarding')
        self.assertEqual(data['selected_rule_level'], 'company')
        self.assertIsNotNone(data['selected_flow'])
        self.assertEqual(data['selected_flow']['code'], 'cfg-tmpl')
        self.assertEqual(len(data['steps']), 2)
        for s in data['steps']:
            self.assertTrue(s['assignment_ok'])

    def test_client_specific_rule_overrides_org_default(self):
        """Scenario 30: client-level mapping resolves, level is 'client'."""
        # Create a client-specific template + mapping
        client_tpl = WorkflowTemplate.objects.create(
            org=self.org, name='Client Template', code='client-tmpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        WorkflowStepTemplate.objects.create(
            template=client_tpl, order=1, code='client-step-a', name='Client Step A',
            assignment_mode='named_user', actor_type='internal',
        )
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='client_onboarding',
            template=client_tpl, client=self.wf_client, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='client_onboarding', step_code='client-step-a',
            assignment_mode='named_user', named_user=self.u_ops, is_active=True,
        )

        resp = self._get_preview(request_type='client_onboarding', client=self.wf_client.pk)
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertTrue(data['ok'], data.get('errors'))
        self.assertEqual(data['selected_rule_level'], 'client')
        self.assertEqual(data['selected_flow']['code'], 'client-tmpl')

    def test_missing_template_returns_ok_false(self):
        """Scenario 31: no mapping for trigger_type=mrf → ok=false."""
        # No mrf mapping exists in this org (only client_onboarding)
        resp = self._get_preview(request_type='mrf')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertTrue(len(resp.data['errors']) > 0)

    def test_missing_sac_returns_ok_false(self):
        """Scenario 32: template exists but a step has no SAC → ok=false."""
        # Create a template with 1 step that has no SAC
        partial_tpl = WorkflowTemplate.objects.create(
            org=self.org, name='Partial', code='partial-tmpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        WorkflowStepTemplate.objects.create(
            template=partial_tpl, order=1, code='partial-step', name='Partial Step',
            assignment_mode='named_user', actor_type='internal',
        )
        # Need a client with its own mapping so it doesn't fall back to org-default
        n_partial = ScopeNode.objects.create(
            org=self.org, code='partial-client', name='Partial', node_type='client',
            parent=self.n_company, depth=1, path='cfg-test/partial-client', is_active=True,
        )
        partial_client = Client.objects.create(
            org=self.org, name='Partial Client', code='PARTIAL-CLIENT', scope_node=n_partial,
        )
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='client_onboarding',
            template=partial_tpl, client=partial_client, site=None, is_active=True,
        )
        # No SAC for partial-step

        resp = self._get_preview(
            request_type='client_onboarding', client=partial_client.pk,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertTrue(len(resp.data['errors']) > 0)

    def test_request_type_required(self):
        """Scenario 33: omitting request_type → 400."""
        api = self._api()
        resp = api.get(self._preview())
        self.assertEqual(resp.status_code, 400)
        self.assertIn('request_type', resp.data['detail'])

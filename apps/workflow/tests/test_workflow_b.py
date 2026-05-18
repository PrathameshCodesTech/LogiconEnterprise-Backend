"""
apps/workflow/tests/test_workflow_b.py

Phase Workflow-B tests — department-aware workflow + config-check + MRF workflow state.

Scenarios:
  1.  Department assignment config accepts matching department/user.
  2.  Mismatch department/user fails at resolve time.
  3.  Department from different org fails at resolve time.
  4.  Site-level department can be used for site-level assignment.
  5.  Runtime step snapshots department name/code.
  6.  Changing user department after workflow start does not change snapshot.
  7.  Config-check returns ok=true for complete config.
  8.  Config-check returns missing template error.
  9.  Config-check returns missing assignment error.
  10. Config-check returns department mismatch error.
  11. MRF detail returns workflow fields when not started.
  12. MRF detail returns active workflow fields after start.
  13. MRF detail returns approved/rejected after completion.
  14. Queue/claim config still returns unsupported phase error.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode, Department
from apps.mrf.models import ManpowerRequest
from apps.sites.models import Client, SiteProfile
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    WorkflowInstance, WorkflowStepInstance,
)
from apps.workflow.services import (
    resolve_step_assignment,
    start_mrf_workflow,
    act_on_step,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, department=None, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass',
        is_superuser=is_superuser, is_staff=is_superuser,
    )
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


def _template(org, code='mrf-b-default'):
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


def _sac(org, step_code, named_user, department=None, client=None, site=None, mode='named_user'):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='mrf', step_code=step_code,
        client=client, site=site,
        assignment_mode=mode, named_user=named_user,
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

class WorkflowBTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='WFB Org', code='wfb-org')
        cls.org2 = Organization.objects.create(name='WFB Org2', code='wfb-org2')

        # Scope tree — needed for scope-based MRF filtering
        cls.n_company = _node(cls.org, 'wfb-org', 'company', None, 0, 'wfb-org')
        cls.n_client_a = _node(cls.org, 'wfb-cl-a', 'client', cls.n_company, 1, 'wfb-org/wfb-cl-a')
        cls.n_site_a = _node(cls.org, 'wfb-site-a', 'site', cls.n_client_a, 2, 'wfb-org/wfb-cl-a/wfb-site-a')
        cls.n_company2 = _node(cls.org2, 'wfb-org2', 'company', None, 0, 'wfb-org2')

        # Departments
        cls.dept_hr = _dept(cls.org, 'HR', 'hr')
        cls.dept_finance = _dept(cls.org, 'Finance', 'finance')
        cls.dept_org2 = _dept(cls.org2, 'HR Org2', 'hr-org2')

        # Client and site — scope_node wired so scope filter can match
        cls.client_a = Client.objects.create(
            org=cls.org, name='WFB Client A', code='wfb-cl-a', scope_node=cls.n_client_a,
        )
        cls.site_a = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, name='WFB Site A', code='wfb-site-a',
            scope_node=cls.n_site_a,
        )
        # Site-level department
        cls.dept_site = _dept(cls.org, 'Site Ops', 'site-ops', client=cls.client_a, site=cls.site_a)

        # Roles
        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr_admin = _role(cls.org, 'hr_admin')
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr_admin)

        # Users — assigned to departments
        cls.hr_user = _user('wfb_hr_user', org=cls.org, department=cls.dept_hr)
        cls.finance_user = _user('wfb_finance_user', org=cls.org, department=cls.dept_finance)
        cls.no_dept_user = _user('wfb_no_dept', org=cls.org)

        # Admin actors
        cls.admin = _user('wfb_admin', org=cls.org)
        cls.hr_admin_user = _user('wfb_hr_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)
        _assign(cls.hr_admin_user, cls.role_hr_admin, cls.n_company)

        cls.superuser = _user('wfb_super', is_superuser=True)

        # Default template with two steps
        cls.template = _template(cls.org)
        cls.step_hr = _step(cls.template, 1, 'hr_review')
        cls.step_fin = _step(cls.template, 2, 'finance_review')
        cls.mapping = _mapping(cls.org, cls.template)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)

    def _config_check_url(self, mrf_id):
        return f'/api/workflow/mrf/{mrf_id}/config-check/'

    def _mrf_url(self, pk=None):
        base = '/api/mrf/requests/'
        return f'{base}{pk}/' if pk else base


# ─── 1–4: Department validation in assignment config ─────────────────────────

class TestDepartmentAssignmentConfig(WorkflowBTestBase):

    def test_config_with_matching_department_user_resolves(self):
        """Scenario 1: SAC with department=HR and named_user from HR resolves cleanly."""
        sac = _sac(self.org, 'hr_dept_match', self.hr_user, department=self.dept_hr)
        config = resolve_step_assignment('mrf', self.org, 'hr_dept_match')
        self.assertEqual(config.pk, sac.pk)
        self.assertEqual(config.department_id, self.dept_hr.pk)

    def test_config_with_mismatched_department_user_raises(self):
        """Scenario 2: named_user is in Finance dept but SAC sets HR dept → error at resolve time."""
        _sac(self.org, 'hr_dept_mismatch', self.finance_user, department=self.dept_hr)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment('mrf', self.org, 'hr_dept_mismatch')
        self.assertIn('does not belong to department', str(ctx.exception))
        self.assertIn('HR', str(ctx.exception))

    def test_config_clean_raises_on_department_org_mismatch(self):
        """Scenario 3: department from org2 → clean() raises ValidationError."""
        sac = StepAssignmentConfig(
            org=self.org,
            trigger_type='mrf',
            step_code='org2_dept_test',
            assignment_mode='named_user',
            named_user=self.hr_user,
            department=self.dept_org2,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            sac.clean()
        self.assertIn('department', ctx.exception.message_dict)

    def test_site_level_department_accepted_for_site_sac(self):
        """Scenario 4: site-level department can be used in a site-scoped SAC."""
        site_user = _user('wfb_site_user', org=self.org, department=self.dept_site)
        sac = _sac(
            self.org, 'site_dept_step', site_user,
            department=self.dept_site,
            client=self.client_a, site=self.site_a,
        )
        config = resolve_step_assignment(
            'mrf', self.org, 'site_dept_step',
            client=self.client_a, site=self.site_a,
        )
        self.assertEqual(config.pk, sac.pk)
        self.assertEqual(config.department_id, self.dept_site.pk)

    def test_no_department_config_resolves_normally(self):
        """No department set on SAC → resolves without department check (backwards compat)."""
        _sac(self.org, 'no_dept_step', self.hr_user, department=None)
        config = resolve_step_assignment('mrf', self.org, 'no_dept_step')
        self.assertIsNone(config.department_id)


# ─── 5–6: Department snapshot at runtime ─────────────────────────────────────

class TestDepartmentSnapshot(WorkflowBTestBase):

    def _start_workflow(self):
        """Helper: start workflow with hr + finance users, both having department config."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user, department=self.dept_hr)
        _sac(self.org, 'finance_review', self.finance_user, department=self.dept_finance)
        return start_mrf_workflow(mrf, actor=self.admin), mrf

    def test_step_instance_snapshots_department_name_code(self):
        """Scenario 5: when workflow starts, department name/code are snapshotted on step instance."""
        instance, _ = self._start_workflow()
        hr_step = instance.steps.get(step_code='hr_review')
        self.assertEqual(hr_step.assigned_department_id, self.dept_hr.pk)
        self.assertEqual(hr_step.assigned_department_name_snapshot, 'HR')
        self.assertEqual(hr_step.assigned_department_code_snapshot, 'hr')

        fin_step = instance.steps.get(step_code='finance_review')
        self.assertEqual(fin_step.assigned_department_id, self.dept_finance.pk)
        self.assertEqual(fin_step.assigned_department_name_snapshot, 'Finance')
        self.assertEqual(fin_step.assigned_department_code_snapshot, 'finance')

    def test_snapshot_unchanged_after_department_rename(self):
        """Scenario 6: renaming the Department after workflow start does not change snapshot."""
        instance, _ = self._start_workflow()
        hr_step = instance.steps.get(step_code='hr_review')

        # Rename the department
        self.dept_hr.name = 'Human Resources'
        self.dept_hr.save(update_fields=['name'])

        hr_step.refresh_from_db()
        # Snapshot still holds the original value
        self.assertEqual(hr_step.assigned_department_name_snapshot, 'HR')

        # Reset for other tests
        self.dept_hr.name = 'HR'
        self.dept_hr.save(update_fields=['name'])

    def test_step_instance_has_no_department_when_sac_has_none(self):
        """Step with no department SAC → snapshot fields are blank."""
        mrf = _mrf(self.org, self.site_a, self.admin, status='submitted')
        _sac(self.org, 'hr_review', self.hr_user, department=None)
        _sac(self.org, 'finance_review', self.finance_user, department=None)
        instance = start_mrf_workflow(mrf, actor=self.admin)
        hr_step = instance.steps.get(step_code='hr_review')
        self.assertIsNone(hr_step.assigned_department_id)
        self.assertEqual(hr_step.assigned_department_name_snapshot, '')
        self.assertEqual(hr_step.assigned_department_code_snapshot, '')


# ─── 7–10: Config check endpoint ─────────────────────────────────────────────

class TestConfigCheckEndpoint(WorkflowBTestBase):

    def test_config_check_ok_true_for_complete_config(self):
        """Scenario 7: fully configured template + both step assignments → ok=true."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user)
        _sac(self.org, 'finance_review', self.finance_user)

        self._login(self.hr_admin_user)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['ok'])
        self.assertEqual(len(resp.data['errors']), 0)
        self.assertEqual(resp.data['mrf'], mrf.pk)
        step_codes = [s['step_code'] for s in resp.data['steps']]
        self.assertIn('hr_review', step_codes)
        self.assertIn('finance_review', step_codes)
        for step in resp.data['steps']:
            self.assertTrue(step['assignment_ok'])

    def test_config_check_missing_template_returns_ok_false(self):
        """Scenario 8: no template mapping for this org → ok=false with error."""
        org3 = Organization.objects.create(name='WFB Org3', code='wfb-org3')
        n3 = _node(org3, 'wfb-org3', 'company', None, 0, 'wfb-org3')
        n3_client = _node(org3, 'c3-wfb', 'client', n3, 1, 'wfb-org3/c3-wfb')
        n3_site = _node(org3, 's3-wfb', 'site', n3_client, 2, 'wfb-org3/c3-wfb/s3-wfb')
        client3 = Client.objects.create(org=org3, name='C3', code='c3-wfb', scope_node=n3_client)
        site3 = SiteProfile.objects.create(
            org=org3, client=client3, name='S3', code='s3-wfb', scope_node=n3_site,
        )
        actor3 = _user('wfb_actor3', org=org3)
        role3 = _role(org3, 'hr_admin')
        bootstrap_role_permissions(role3)
        _assign(actor3, role3, n3)

        mrf = _mrf(org3, site3, actor3)
        self._login(actor3)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        self.assertTrue(len(resp.data['errors']) > 0)
        self.assertIsNone(resp.data['template'])

    def test_config_check_missing_step_assignment_returns_ok_false(self):
        """Scenario 9: template exists but no SAC for one step → ok=false."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user)
        # finance_review has no SAC

        self._login(self.hr_admin_user)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        error_text = ' '.join(resp.data['errors'])
        self.assertIn('finance_review', error_text)

    def test_config_check_department_mismatch_returns_ok_false(self):
        """Scenario 10: SAC has dept=HR but user is from Finance → ok=false."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.finance_user, department=self.dept_hr)
        _sac(self.org, 'finance_review', self.finance_user)

        self._login(self.hr_admin_user)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['ok'])
        error_text = ' '.join(resp.data['errors'])
        self.assertIn('does not belong to department', error_text)

    def test_config_check_unauthenticated_returns_401(self):
        """Config-check requires auth."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 401)

    def test_config_check_no_capability_returns_403(self):
        """User without workflow.read or workflow.start_workflow gets 403."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        no_cap = _user('wfb_nocap_check', org=self.org)
        _assign(no_cap, _role(self.org, 'client_user'), self.n_company)
        self._login(no_cap)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_config_check_department_displayed_in_step_info(self):
        """Config-check includes department name in step info when configured."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user, department=self.dept_hr)
        _sac(self.org, 'finance_review', self.finance_user, department=self.dept_finance)

        self._login(self.hr_admin_user)
        resp = self.api.get(self._config_check_url(mrf.pk))
        self.assertTrue(resp.data['ok'])
        hr_step = next(s for s in resp.data['steps'] if s['step_code'] == 'hr_review')
        self.assertEqual(hr_step['department'], 'HR')
        self.assertEqual(hr_step['assigned_user'], 'wfb_hr_user')


# ─── 11–13: MRF workflow state in response ───────────────────────────────────

class TestMRFWorkflowStateFields(WorkflowBTestBase):

    def test_mrf_detail_workflow_not_started(self):
        """Scenario 11: MRF with no workflow → workflow_status=not_started, nulls."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        self._login(self.admin)
        resp = self.api.get(self._mrf_url(mrf.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['workflow_status'], 'not_started')
        self.assertIsNone(resp.data['workflow_instance_id'])
        self.assertIsNone(resp.data['workflow_current_step_id'])
        self.assertIsNone(resp.data['workflow_current_step_code'])
        self.assertIsNone(resp.data['workflow_current_step_name'])
        self.assertIsNone(resp.data['workflow_current_assigned_user'])
        self.assertIsNone(resp.data['workflow_current_assigned_user_name'])
        self.assertIsNone(resp.data['workflow_current_department_name'])

    def test_mrf_detail_active_workflow_fields(self):
        """Scenario 12: started workflow → fields populated with current step info."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user, department=self.dept_hr)
        _sac(self.org, 'finance_review', self.finance_user)
        instance = start_mrf_workflow(mrf, actor=self.admin)

        self._login(self.admin)
        resp = self.api.get(self._mrf_url(mrf.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['workflow_status'], 'active')
        self.assertEqual(resp.data['workflow_instance_id'], instance.pk)
        self.assertEqual(resp.data['workflow_current_step_code'], 'hr_review')
        self.assertEqual(resp.data['workflow_current_step_name'], 'hr_review')
        self.assertEqual(resp.data['workflow_current_assigned_user'], self.hr_user.pk)
        self.assertEqual(resp.data['workflow_current_assigned_user_name'], 'wfb_hr_user')
        self.assertEqual(resp.data['workflow_current_department_name'], 'HR')

    def test_mrf_detail_approved_workflow_fields(self):
        """Scenario 13a: fully approved workflow → workflow_status=approved, step fields null."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user)
        _sac(self.org, 'finance_review', self.finance_user)
        instance = start_mrf_workflow(mrf, actor=self.admin)

        # Approve both steps
        hr_step = instance.steps.get(step_code='hr_review')
        act_on_step(hr_step, actor=self.hr_user, action='approve')
        fin_step = instance.steps.get(step_code='finance_review')
        act_on_step(fin_step, actor=self.finance_user, action='approve')

        self._login(self.admin)
        resp = self.api.get(self._mrf_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['workflow_status'], 'approved')
        self.assertIsNone(resp.data['workflow_current_step_id'])

    def test_mrf_detail_rejected_workflow_fields(self):
        """Scenario 13b: rejected workflow → workflow_status=rejected."""
        mrf = _mrf(self.org, self.site_a, self.admin)
        _sac(self.org, 'hr_review', self.hr_user)
        _sac(self.org, 'finance_review', self.finance_user)
        instance = start_mrf_workflow(mrf, actor=self.admin)

        hr_step = instance.steps.get(step_code='hr_review')
        act_on_step(hr_step, actor=self.hr_user, action='reject', comment='Not now.')

        self._login(self.admin)
        resp = self.api.get(self._mrf_url(mrf.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['workflow_status'], 'rejected')
        self.assertIsNone(resp.data['workflow_current_step_id'])


# ─── 14: Queue/claim still unsupported ───────────────────────────────────────

class TestQueueClaimStillUnsupported(WorkflowBTestBase):

    def test_queue_assignment_mode_raises_error(self):
        """Scenario 14: queue-mode SAC → WorkflowConfigurationError at resolve time."""
        _sac(self.org, 'queue_step', None, mode='queue')
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment('mrf', self.org, 'queue_step')
        self.assertIn('queue', str(ctx.exception))
        self.assertIn('not supported in Phase 1', str(ctx.exception))

    def test_claim_assignment_mode_raises_error(self):
        """Claim-mode SAC → WorkflowConfigurationError at resolve time."""
        _sac(self.org, 'claim_step', None, mode='claim')
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            resolve_step_assignment('mrf', self.org, 'claim_step')
        self.assertIn('claim', str(ctx.exception))
        self.assertIn('not supported in Phase 1', str(ctx.exception))

"""
apps/workflow/tests/test_workflow_c.py

Phase Workflow-C tests — reassign department safety.

Scenarios:
  1. Reassign active step to user in same department -> succeeds.
  2. Reassign active step to user in different department -> WorkflowConfigurationError.
  3. Reassign active step with no assigned_department -> succeeds (no dept check).
  4. Superuser reassign also cannot bypass department mismatch.
  5. Audit action is created on successful reassignment.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.core.models import Organization, ScopeNode, Department
from apps.mrf.models import ManpowerRequest
from apps.sites.models import Client, SiteProfile
from apps.access.tests.utils import bootstrap_role_permissions
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    WorkflowAction,
)
from apps.workflow.services import start_mrf_workflow, reassign_step


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


def _mrf(org, site, requested_by):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status='submitted',
        billing_type='billable',
    )


# ─── Base class ──────────────────────────────────────────────────────────────

class WorkflowCTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='WFC Org', code='wfc-org')

        cls.n_org = _node(cls.org, 'wfc-org', 'company', None, 0, 'wfc-org')
        cls.n_client = _node(cls.org, 'wfc-cl', 'client', cls.n_org, 1, 'wfc-org/wfc-cl')
        cls.n_site = _node(cls.org, 'wfc-site', 'site', cls.n_client, 2, 'wfc-org/wfc-cl/wfc-site')

        cls.client = Client.objects.create(
            org=cls.org, name='WFC Client', code='wfc-cl', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client, name='WFC Site', code='wfc-site',
            scope_node=cls.n_site,
        )

        cls.dept_hr = _dept(cls.org, 'HR', 'wfc-hr')
        cls.dept_finance = _dept(cls.org, 'Finance', 'wfc-finance')

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_reassign = _role(cls.org, 'workflow_reassign')
        bootstrap_role_permissions(cls.role_admin)

        # Actor who will start/reassign workflows
        cls.actor = _user('wfc_actor', org=cls.org)
        _assign(cls.actor, cls.role_admin, cls.n_org)

        cls.superuser = _user('wfc_super', is_superuser=True)

        # HR users
        cls.hr_user1 = _user('wfc_hr1', org=cls.org, department=cls.dept_hr)
        cls.hr_user2 = _user('wfc_hr2', org=cls.org, department=cls.dept_hr)

        # Finance user (different dept)
        cls.finance_user = _user('wfc_finance', org=cls.org, department=cls.dept_finance)

        # User with no department
        cls.nodept_user = _user('wfc_nodept', org=cls.org)

        # Workflow template
        cls.template = WorkflowTemplate.objects.create(
            org=cls.org, name='WFC Default', code='wfc-default',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.step_with_dept = WorkflowStepTemplate.objects.create(
            template=cls.template, order=1, code='wfc_hr_step', name='HR Step',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False,
            requires_comment_on_request_changes=False,
        )
        cls.step_no_dept = WorkflowStepTemplate.objects.create(
            template=cls.template, order=2, code='wfc_fin_step', name='Finance Step',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False,
            requires_comment_on_request_changes=False,
        )

        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.template,
            client=None, site=None, is_active=True,
        )

        # SAC: step 1 with dept=HR, step 2 without dept
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='wfc_hr_step',
            assignment_mode='named_user', named_user=cls.hr_user1,
            department=cls.dept_hr, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='wfc_fin_step',
            assignment_mode='named_user', named_user=cls.finance_user,
            department=None, is_active=True,
        )

    def _start(self):
        mrf = _mrf(self.org, self.site, self.actor)
        instance = start_mrf_workflow(mrf, actor=self.actor)
        steps = {s.step_code: s for s in instance.steps.all()}
        return instance, steps

    def _reassign_url(self, instance_id, step_id):
        return f'/api/workflow/instances/{instance_id}/steps/{step_id}/reassign/'


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestReassignDepartmentSafety(WorkflowCTestBase):

    def test_reassign_to_same_department_succeeds(self):
        """Scenario 1: reassigning to another user in the same dept works."""
        _, steps = self._start()
        step = steps['wfc_hr_step']
        self.assertEqual(step.assigned_department_id, self.dept_hr.pk)

        reassign_step(step, actor=self.actor, new_user=self.hr_user2)

        step.refresh_from_db()
        self.assertEqual(step.assigned_user_id, self.hr_user2.pk)

    def test_reassign_to_different_department_raises(self):
        """Scenario 2: reassigning to a user from a different dept raises WorkflowConfigurationError."""
        _, steps = self._start()
        step = steps['wfc_hr_step']

        with self.assertRaises(WorkflowConfigurationError) as ctx:
            reassign_step(step, actor=self.actor, new_user=self.finance_user)
        self.assertIn('must belong to department', str(ctx.exception))
        self.assertIn('HR', str(ctx.exception))

    def test_reassign_step_with_no_department_succeeds(self):
        """Scenario 3: step has no assigned_department → any same-org user is accepted."""
        _, steps = self._start()
        fin_step = steps['wfc_fin_step']
        self.assertIsNone(fin_step.assigned_department_id)

        reassign_step(fin_step, actor=self.actor, new_user=self.nodept_user)

        fin_step.refresh_from_db()
        self.assertEqual(fin_step.assigned_user_id, self.nodept_user.pk)

    def test_superuser_reassign_cannot_bypass_department_mismatch(self):
        """Scenario 4: superuser cannot reassign to wrong-dept user either."""
        _, steps = self._start()
        step = steps['wfc_hr_step']

        with self.assertRaises(WorkflowConfigurationError) as ctx:
            reassign_step(step, actor=self.superuser, new_user=self.finance_user)
        self.assertIn('must belong to department', str(ctx.exception))

    def test_audit_action_created_on_successful_reassign(self):
        """Scenario 5: WorkflowAction with action='reassign' is written on success."""
        _, steps = self._start()
        step = steps['wfc_hr_step']
        old_user = step.assigned_user

        reassign_step(step, actor=self.actor, new_user=self.hr_user2, comment='Coverage test')

        action = WorkflowAction.objects.filter(
            step_instance=step, action='reassign',
        ).first()
        self.assertIsNotNone(action)
        self.assertEqual(action.reassign_from_id, old_user.pk)
        self.assertEqual(action.reassign_to_id, self.hr_user2.pk)
        self.assertEqual(action.comment, 'Coverage test')

    def test_department_snapshot_unchanged_after_reassign(self):
        """Reassigning must not change the department snapshot fields."""
        _, steps = self._start()
        step = steps['wfc_hr_step']
        original_dept_id = step.assigned_department_id
        original_dept_name = step.assigned_department_name_snapshot
        original_dept_code = step.assigned_department_code_snapshot

        reassign_step(step, actor=self.actor, new_user=self.hr_user2)

        step.refresh_from_db()
        self.assertEqual(step.assigned_department_id, original_dept_id)
        self.assertEqual(step.assigned_department_name_snapshot, original_dept_name)
        self.assertEqual(step.assigned_department_code_snapshot, original_dept_code)


class TestReassignViaAPI(WorkflowCTestBase):
    """API-level checks: department mismatch returns 400, not a 5xx."""

    def setUp(self):
        self.api = APIClient()
        instance, steps = self._start()
        self.instance = instance
        self.step = steps['wfc_hr_step']

    def test_api_reassign_wrong_dept_returns_400(self):
        self.api.force_authenticate(user=self.superuser)
        resp = self.api.post(
            self._reassign_url(self.instance.pk, self.step.pk),
            {'new_user': self.finance_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('must belong to department', resp.data['detail'])

    def test_api_reassign_same_dept_returns_200(self):
        self.api.force_authenticate(user=self.superuser)
        resp = self.api.post(
            self._reassign_url(self.instance.pk, self.step.pk),
            {'new_user': self.hr_user2.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['assigned_user'], self.hr_user2.pk)

"""
apps/workflow/tests/test_workflow_engine.py

Comprehensive tests for the MRF workflow engine.

Scenarios:
  1.  Unauthenticated start → 401
  2.  No capability start → 403
  3.  Start workflow success → 201, MRF status hr_review
  4.  Start workflow: no template mapping → 400
  5.  Start workflow: no step assignment config → 400
  6.  Start workflow: duplicate active workflow → 400
  7.  Start workflow: wrong org MRF → 404
  8.  Get instance: unauthenticated → 401
  9.  Get instance: no capability → 403
  10. Get instance: success → 200 with steps and current_step
  11. Act on step: not assigned → 403
  12. Act on step: step not active → 400
  13. Act on step: approve moves to next step
  14. Act on step: approve on last step completes workflow → MRF approved
  15. Act on step: reject without required comment → 400
  16. Act on step: reject completes workflow → MRF rejected
  17. Act on step: reject with target step reactivates that step
  18. Reassign step: no capability → 403
  19. Reassign step: success → audit trail written
  20. Reassign step: inactive user → 400
  21. Reassign step: different org user → 400
  22. Reassign step: already completed step → 400
  23. resolve_workflow_template: site > client > org default fallback
  24. resolve_step_assignment: date window respected
  25. Inactive mapping does not block new active mapping (Fix 1)
  26. Inactive SAC does not block new active SAC (Fix 1)
  27. Step template validation: invalid on_approve_next code raises ValidationError (Fix 2)
  28. Step template validation: 'END' is always valid for on_approve_next (Fix 2)
  29. Step template validation: invalid on_reject_target code raises ValidationError (Fix 2)
  30. Step template validation: blank transition targets always valid (Fix 2)
  31. actor_type 'field' is accepted (Fix 3)
"""

from datetime import date

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.access.tests.utils import bootstrap_role_permissions
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    WorkflowInstance, WorkflowStepInstance, WorkflowAction,
)
from apps.workflow.services import (
    resolve_workflow_template,
    resolve_step_assignment,
    start_mrf_workflow,
    act_on_step,
    reassign_step,
)


# ─── URL helpers ──────────────────────────────────────────────────────────────

def _start_url(mrf_id):
    return f'/api/workflow/mrf/{mrf_id}/start/'


def _detail_url(instance_id):
    return f'/api/workflow/instances/{instance_id}/'


def _act_url(instance_id, step_id):
    return f'/api/workflow/instances/{instance_id}/steps/{step_id}/act/'


def _reassign_url(instance_id, step_id):
    return f'/api/workflow/instances/{instance_id}/steps/{step_id}/reassign/'


# ─── Helpers ─────────────────────────────────────────────────────────────────

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


def _template(org, code='mrf-default', version=1):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code, trigger_type='mrf',
        version=version, is_active=True,
    )


def _step(template, order, code, on_approve_next='', on_reject_target='',
          requires_comment_on_reject=True, sla_hours=None):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        on_approve_next=on_approve_next,
        on_reject_target=on_reject_target,
        requires_comment_on_reject=requires_comment_on_reject,
        requires_comment_on_request_changes=True,
        sla_hours=sla_hours,
    )


def _mapping(org, template, client=None, site=None):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='mrf', template=template,
        client=client, site=site, is_active=True,
    )


def _sac(org, step_code, named_user, client=None, site=None,
         effective_from=None, effective_to=None):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='mrf', step_code=step_code,
        client=client, site=site,
        assignment_mode='named_user', named_user=named_user,
        effective_from=effective_from, effective_to=effective_to,
        is_active=True,
    )


def _mrf(org, site, requested_by, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status=status,
        billing_type='billable',
    )


# ─── Base test class ─────────────────────────────────────────────────────────

class WorkflowTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='WF Test Org', code='wf-org')
        cls.org2 = Organization.objects.create(name='WF Other Org', code='wf-org2')

        cls.n_company = _node(cls.org, 'wf-org', 'company', None, 0, 'wf-org')

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr_admin = _role(cls.org, 'hr_admin')
        cls.role_hr_exec = _role(cls.org, 'hr_executive')
        cls.role_finance = _role(cls.org, 'finance')
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr_admin)
        bootstrap_role_permissions(cls.role_hr_exec)
        bootstrap_role_permissions(cls.role_finance)

        cls.superuser = _user('wf_super', is_superuser=True)
        cls.admin = _user('wf_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.hr_admin = _user('wf_hradmin', org=cls.org)
        _assign(cls.hr_admin, cls.role_hr_admin, cls.n_company)

        cls.hr_exec = _user('wf_hrexec', org=cls.org)
        _assign(cls.hr_exec, cls.role_hr_exec, cls.n_company)

        cls.finance_user = _user('wf_finance', org=cls.org)
        _assign(cls.finance_user, cls.role_finance, cls.n_company)

        cls.noauth = _user('wf_noauth', org=cls.org)

        # Client and site — scope nodes required for filter_mrfs_for_user path matching
        cls.n_client = _node(cls.org, 'wf-client', 'client', cls.n_company, 1, 'wf-org/wf-client')
        cls.n_site = _node(cls.org, 'wf-site', 'site', cls.n_client, 2, 'wf-org/wf-client/wf-site')
        cls.client_obj = Client.objects.create(
            org=cls.org, name='WF Client', code='wf-client', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client_obj,
            name='WF Site', code='wf-site', scope_node=cls.n_site,
        )

        # Template with two steps
        cls.template = _template(cls.org, 'wf-mrf-default')
        cls.step1 = _step(cls.template, 1, 'hr_review')
        cls.step2 = _step(cls.template, 2, 'finance_review')

        # Org-level default mapping
        cls.mapping = _mapping(cls.org, cls.template)

        # Org-level default step assignment configs
        cls.sac1 = _sac(cls.org, 'hr_review', cls.hr_admin)
        cls.sac2 = _sac(cls.org, 'finance_review', cls.finance_user)

        # MRF for testing — must have a billable line item for readiness gate
        cls.mrf = _mrf(cls.org, cls.site, cls.hr_exec)
        cls.job_role = JobRole.objects.create(
            org=cls.org, name='WF Guard', code='wf-guard', skill_category='unskilled',
        )
        cls.srr = SiteRoleRequirement.objects.create(
            site=cls.site, job_role=cls.job_role,
            approved_headcount=20, billing_type='billable',
            effective_from=date.today(), is_active=True,
        )
        MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role,
            site_role_requirement=cls.srr, headcount=2,
        )

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c


# ─── Service tests: resolve_workflow_template ─────────────────────────────────

class TestResolveWorkflowTemplate(WorkflowTestBase):

    def test_org_default_found(self):
        template = resolve_workflow_template('mrf', self.org)
        self.assertEqual(template.pk, self.template.pk)

    def test_site_level_takes_priority(self):
        site_template = _template(self.org, 'wf-site-specific')
        _mapping(self.org, site_template, site=self.site)
        template = resolve_workflow_template('mrf', self.org, client=self.client_obj, site=self.site)
        self.assertEqual(template.pk, site_template.pk)

    def test_client_level_over_org_default(self):
        client_template = _template(self.org, 'wf-client-specific')
        _mapping(self.org, client_template, client=self.client_obj)
        template = resolve_workflow_template('mrf', self.org, client=self.client_obj)
        self.assertEqual(template.pk, client_template.pk)

    def test_no_mapping_raises_error(self):
        org2 = self.org2
        with self.assertRaises(WorkflowConfigurationError):
            resolve_workflow_template('mrf', org2)

    def test_inactive_template_raises_error(self):
        self.template.is_active = False
        self.template.save()
        try:
            with self.assertRaises(WorkflowConfigurationError):
                resolve_workflow_template('mrf', self.org)
        finally:
            self.template.is_active = True
            self.template.save()


# ─── Service tests: resolve_step_assignment ───────────────────────────────────

class TestResolveStepAssignment(WorkflowTestBase):

    def test_org_default_found(self):
        config = resolve_step_assignment('mrf', self.org, 'hr_review')
        self.assertEqual(config.named_user_id, self.hr_admin.pk)

    def test_site_level_takes_priority(self):
        site_user = _user('wf_site_reviewer', org=self.org)
        _sac(self.org, 'hr_review', site_user, site=self.site)
        config = resolve_step_assignment('mrf', self.org, 'hr_review', site=self.site)
        self.assertEqual(config.named_user_id, site_user.pk)

    def test_missing_config_raises_error(self):
        with self.assertRaises(WorkflowConfigurationError):
            resolve_step_assignment('mrf', self.org, 'nonexistent_step')

    def test_inactive_config_excluded(self):
        inactive_user = _user('wf_inactive_reviewer', org=self.org)
        sac = _sac(self.org, 'unique_inactive_step', inactive_user)
        sac.is_active = False
        sac.save()
        with self.assertRaises(WorkflowConfigurationError):
            resolve_step_assignment('mrf', self.org, 'unique_inactive_step')

    def test_date_window_respected(self):
        future_user = _user('wf_future_reviewer', org=self.org)
        tomorrow = (timezone.now() + timezone.timedelta(days=1)).date()
        _sac(self.org, 'future_step', future_user, effective_from=tomorrow)
        with self.assertRaises(WorkflowConfigurationError):
            resolve_step_assignment('mrf', self.org, 'future_step')

    def test_inactive_named_user_raises_error(self):
        inactive_user = _user('wf_inactive_nu', org=self.org)
        inactive_user.is_active = False
        inactive_user.save()
        _sac(self.org, 'step_inactive_nu', inactive_user)
        with self.assertRaises(WorkflowConfigurationError):
            resolve_step_assignment('mrf', self.org, 'step_inactive_nu')


# ─── API tests: start workflow ────────────────────────────────────────────────

class TestStartMRFWorkflow(WorkflowTestBase):

    def setUp(self):
        self.local_mrf = _mrf(self.org, self.site, self.hr_exec)
        MRFLineItem.objects.create(
            mrf=self.local_mrf,
            job_role=self.job_role,
            site_role_requirement=self.srr,
            headcount=2,
        )

    def test_unauthenticated_returns_401(self):
        resp = self._api().post(_start_url(self.local_mrf.pk))
        self.assertEqual(resp.status_code, 401)

    def test_no_capability_returns_403(self):
        resp = self._api(self.noauth).post(_start_url(self.local_mrf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_finance_user_has_no_start_capability(self):
        resp = self._api(self.finance_user).post(_start_url(self.local_mrf.pk))
        self.assertEqual(resp.status_code, 403)

    def test_hr_exec_can_start(self):
        resp = self._api(self.hr_exec).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'active')
        self.assertEqual(len(resp.data['steps']), 2)

    def test_start_sets_mrf_status_to_hr_review(self):
        resp = self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.local_mrf.refresh_from_db()
        self.assertEqual(self.local_mrf.status, 'hr_review')

    def test_start_activates_first_step(self):
        resp = self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 201)
        instance = WorkflowInstance.objects.get(pk=resp.data['id'])
        first_step = instance.steps.order_by('step_order').first()
        self.assertEqual(first_step.status, 'active')
        second_step = instance.steps.order_by('step_order').last()
        self.assertEqual(second_step.status, 'pending')

    def test_start_assigns_users_from_config(self):
        resp = self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 201)
        instance = WorkflowInstance.objects.get(pk=resp.data['id'])
        steps = list(instance.steps.order_by('step_order'))
        self.assertEqual(steps[0].assigned_user_id, self.hr_admin.pk)
        self.assertEqual(steps[1].assigned_user_id, self.finance_user.pk)

    def test_start_writes_audit_action(self):
        resp = self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 201)
        instance = WorkflowInstance.objects.get(pk=resp.data['id'])
        action = instance.audit_trail.first()
        self.assertIsNotNone(action)
        self.assertEqual(action.action, 'start')

    def test_duplicate_start_returns_400(self):
        self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        resp = self._api(self.hr_admin).post(_start_url(self.local_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 400)

    def test_wrong_org_mrf_returns_404(self):
        org2_client = Client.objects.create(org=self.org2, name='O2 Client', code='o2-client')
        org2_site = SiteProfile.objects.create(
            org=self.org2, client=org2_client, name='O2 Site', code='o2-site',
        )
        org2_user = _user('wf_org2_user', org=self.org2)
        org2_mrf = _mrf(self.org2, org2_site, org2_user)
        resp = self._api(self.hr_admin).post(_start_url(org2_mrf.pk), format='json')
        self.assertEqual(resp.status_code, 404)

    def test_no_template_mapping_returns_400(self):
        org3 = Organization.objects.create(name='WF No Template Org', code='wf-org3')
        org3_client = Client.objects.create(org=org3, name='O3 Client', code='o3-client')
        org3_site = SiteProfile.objects.create(
            org=org3, client=org3_client, name='O3 Site', code='o3-site',
        )
        actor = _user('wf_org3_actor', org=org3, is_superuser=True)
        org3_mrf = _mrf(org3, org3_site, actor)
        with self.assertRaises(WorkflowConfigurationError):
            start_mrf_workflow(org3_mrf, actor)


# ─── API tests: get workflow instance ────────────────────────────────────────

class TestWorkflowInstanceDetail(WorkflowTestBase):

    def setUp(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        self.instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)

    def test_unauthenticated_returns_401(self):
        resp = self._api().get(_detail_url(self.instance.pk))
        self.assertEqual(resp.status_code, 401)

    def test_no_capability_returns_403(self):
        resp = self._api(self.noauth).get(_detail_url(self.instance.pk))
        self.assertEqual(resp.status_code, 403)

    def test_hr_exec_can_read(self):
        resp = self._api(self.hr_exec).get(_detail_url(self.instance.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], self.instance.pk)

    def test_response_includes_steps(self):
        resp = self._api(self.hr_admin).get(_detail_url(self.instance.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['steps']), 2)

    def test_response_includes_current_step(self):
        resp = self._api(self.hr_admin).get(_detail_url(self.instance.pk))
        self.assertIsNotNone(resp.data['current_step'])
        self.assertEqual(resp.data['current_step']['step_code'], 'hr_review')

    def test_wrong_org_returns_404(self):
        # Give org2_user the hr_admin role (so they have workflow.read) but in org2
        org2_node = _node(self.org2, 'wf-org2', 'company', None, 0, 'wf-org2')
        org2_role = _role(self.org2, 'hr_admin')
        bootstrap_role_permissions(org2_role)
        org2_user = _user('wf_o2_reader', org=self.org2)
        _assign(org2_user, org2_role, org2_node)
        resp = self._api(org2_user).get(_detail_url(self.instance.pk))
        self.assertEqual(resp.status_code, 404)


# ─── API tests: act on step ───────────────────────────────────────────────────

class TestActOnStep(WorkflowTestBase):

    def setUp(self):
        self.local_mrf = _mrf(self.org, self.site, self.hr_exec)
        self.instance = start_mrf_workflow(self.local_mrf, actor=self.hr_admin)
        self.active_step = self.instance.steps.filter(status='active').first()

    def test_unauthenticated_returns_401(self):
        resp = self._api().post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_non_assigned_user_returns_403(self):
        resp = self._api(self.hr_exec).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_act_on_any_step(self):
        resp = self._api(self.superuser).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_approve_moves_to_next_step(self):
        self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        self.active_step.refresh_from_db()
        self.assertEqual(self.active_step.status, 'approved')
        next_step = self.instance.steps.filter(status='active').first()
        self.assertIsNotNone(next_step)
        self.assertEqual(next_step.step_code, 'finance_review')

    def test_approve_last_step_completes_workflow(self):
        # Approve step 1
        self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        # Approve step 2
        step2 = self.instance.steps.filter(status='active').first()
        self._api(self.finance_user).post(
            _act_url(self.instance.pk, step2.pk),
            {'action': 'approve'}, format='json',
        )
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.status, 'approved')
        self.local_mrf.refresh_from_db()
        self.assertEqual(self.local_mrf.status, 'approved')
        self.assertIsNotNone(self.local_mrf.approved_at)

    def test_reject_requires_comment(self):
        resp = self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'reject', 'comment': ''}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_reject_completes_workflow_as_rejected(self):
        resp = self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'reject', 'comment': 'Not approved'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.status, 'rejected')
        self.local_mrf.refresh_from_db()
        self.assertEqual(self.local_mrf.status, 'rejected')
        self.assertIsNotNone(self.local_mrf.rejected_at)

    def test_reject_with_target_reactivates_that_step(self):
        # Create a fresh template with reject loop
        loop_template = _template(self.org, 'loop-template')
        s1 = _step(loop_template, 1, 'step_a')
        s2 = _step(loop_template, 2, 'step_b', on_reject_target='step_a')
        # Map it and create SAC
        loop_mrf = _mrf(self.org, self.site, self.hr_exec)
        site_mapping = WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='mrf', template=loop_template,
            site=self.site, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_a',
            site=self.site, assignment_mode='named_user', named_user=self.hr_admin,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='step_b',
            site=self.site, assignment_mode='named_user', named_user=self.finance_user,
            is_active=True,
        )
        instance = start_mrf_workflow(loop_mrf, actor=self.hr_admin)
        active = instance.steps.filter(status='active').first()
        # Approve step_a, then step_b rejects back to step_a
        self._api(self.hr_admin).post(
            _act_url(instance.pk, active.pk),
            {'action': 'approve'}, format='json',
        )
        step_b = instance.steps.filter(status='active').first()
        self._api(self.finance_user).post(
            _act_url(instance.pk, step_b.pk),
            {'action': 'reject', 'comment': 'Needs rework'}, format='json',
        )
        # step_a should be active again
        active.refresh_from_db()
        self.assertEqual(active.status, 'active')
        instance.refresh_from_db()
        self.assertEqual(instance.status, 'active')

    def test_act_on_non_active_step_returns_400(self):
        # pending step is step 2 (finance_review), assigned to finance_user
        pending_step = self.instance.steps.filter(status='pending').first()
        # Use the assigned user so we pass the actor check and hit the status check
        resp = self._api(self.finance_user).post(
            _act_url(self.instance.pk, pending_step.pk),
            {'action': 'approve'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_action_returns_400(self):
        resp = self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'invalid_action'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_approve_writes_audit_action(self):
        self._api(self.hr_admin).post(
            _act_url(self.instance.pk, self.active_step.pk),
            {'action': 'approve'}, format='json',
        )
        actions = self.instance.audit_trail.filter(action='approve')
        self.assertEqual(actions.count(), 1)
        self.assertEqual(actions.first().actor_id, self.hr_admin.pk)


# ─── API tests: reassign step ────────────────────────────────────────────────

class TestReassignStep(WorkflowTestBase):

    def setUp(self):
        self.local_mrf = _mrf(self.org, self.site, self.hr_exec)
        self.instance = start_mrf_workflow(self.local_mrf, actor=self.hr_admin)
        self.active_step = self.instance.steps.filter(status='active').first()
        self.new_assignee = _user('wf_new_assignee', org=self.org)

    def test_unauthenticated_returns_401(self):
        resp = self._api().post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': self.new_assignee.pk}, format='json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_no_capability_returns_403(self):
        resp = self._api(self.hr_exec).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': self.new_assignee.pk}, format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_hr_admin_can_reassign(self):
        resp = self._api(self.hr_admin).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': self.new_assignee.pk, 'comment': 'OOO cover'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.active_step.refresh_from_db()
        self.assertEqual(self.active_step.assigned_user_id, self.new_assignee.pk)

    def test_reassign_writes_audit_action(self):
        self._api(self.hr_admin).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': self.new_assignee.pk, 'comment': 'delegate'}, format='json',
        )
        action = self.instance.audit_trail.filter(action='reassign').first()
        self.assertIsNotNone(action)
        self.assertEqual(action.reassign_from_id, self.hr_admin.pk)
        self.assertEqual(action.reassign_to_id, self.new_assignee.pk)

    def test_inactive_user_returns_400(self):
        inactive = _user('wf_inactive', org=self.org)
        inactive.is_active = False
        inactive.save()
        resp = self._api(self.hr_admin).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': inactive.pk}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_different_org_user_returns_400(self):
        other_org_user = _user('wf_other_org', org=self.org2)
        resp = self._api(self.hr_admin).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': other_org_user.pk}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_reassign_completed_step_returns_400(self):
        # Complete the step first
        act_on_step(self.active_step, self.hr_admin, 'approve')
        self.active_step.refresh_from_db()
        resp = self._api(self.hr_admin).post(
            _reassign_url(self.instance.pk, self.active_step.pk),
            {'new_user': self.new_assignee.pk}, format='json',
        )
        self.assertEqual(resp.status_code, 400)


# ─── Fix 1: inactive rows do not block new active mapping/config ──────────────

class TestConstraintAllowsInactiveRows(WorkflowTestBase):

    def test_inactive_wtm_does_not_block_new_active_wtm(self):
        # Create and deactivate an org-level mapping in a fresh org to avoid
        # conflicting with setUpTestData's mapping on self.org.
        org_x = Organization.objects.create(name='WF Constraint Org', code='wf-cx')
        tmpl = _template(org_x, 'cx-template')
        old = WorkflowTemplateMapping.objects.create(
            org=org_x, trigger_type='mrf', template=tmpl, is_active=True,
        )
        old.is_active = False
        old.save()
        # Should not raise — inactive old row must not block this
        new = WorkflowTemplateMapping.objects.create(
            org=org_x, trigger_type='mrf', template=tmpl, is_active=True,
        )
        self.assertTrue(new.pk)

    def test_inactive_sac_does_not_block_new_active_sac(self):
        org_y = Organization.objects.create(name='WF SAC Org', code='wf-cy')
        actor = _user('wf_cy_actor', org=org_y)
        old = StepAssignmentConfig.objects.create(
            org=org_y, trigger_type='mrf', step_code='review',
            assignment_mode='named_user', named_user=actor, is_active=True,
        )
        old.is_active = False
        old.save()
        new_actor = _user('wf_cy_actor2', org=org_y)
        new = StepAssignmentConfig.objects.create(
            org=org_y, trigger_type='mrf', step_code='review',
            assignment_mode='named_user', named_user=new_actor, is_active=True,
        )
        self.assertTrue(new.pk)


# ─── Fix 2: WorkflowStepTemplate transition target validation ─────────────────

class TestStepTemplateValidation(WorkflowTestBase):

    def setUp(self):
        self.val_template = _template(self.org, 'val-template')
        self.step_x = _step(self.val_template, 1, 'step_x')
        self.step_y = _step(self.val_template, 2, 'step_y')

    def test_valid_on_approve_next_passes(self):
        self.step_x.on_approve_next = 'step_y'
        self.step_x.clean()  # no exception

    def test_end_keyword_is_valid_for_on_approve_next(self):
        self.step_x.on_approve_next = 'END'
        self.step_x.clean()  # no exception

    def test_blank_approve_next_is_valid(self):
        self.step_x.on_approve_next = ''
        self.step_x.clean()  # no exception

    def test_invalid_on_approve_next_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        self.step_x.on_approve_next = 'nonexistent_step'
        with self.assertRaises(ValidationError) as ctx:
            self.step_x.clean()
        self.assertIn('on_approve_next', ctx.exception.message_dict)

    def test_invalid_on_reject_target_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        self.step_y.on_reject_target = 'typo_step'
        with self.assertRaises(ValidationError) as ctx:
            self.step_y.clean()
        self.assertIn('on_reject_target', ctx.exception.message_dict)

    def test_valid_on_reject_target_passes(self):
        self.step_y.on_reject_target = 'step_x'
        self.step_y.clean()  # no exception

    def test_invalid_on_request_changes_target_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        self.step_y.on_request_changes_target = 'bad_code'
        with self.assertRaises(ValidationError) as ctx:
            self.step_y.clean()
        self.assertIn('on_request_changes_target', ctx.exception.message_dict)


# ─── Fix 3: 'field' actor_type is accepted ───────────────────────────────────

class TestFieldActorType(WorkflowTestBase):

    def test_field_actor_type_is_accepted(self):
        ft = _template(self.org, 'field-actor-template')
        step = WorkflowStepTemplate.objects.create(
            template=ft, order=1, code='field_step', name='Field Step',
            assignment_mode='named_user', actor_type='field',
        )
        self.assertEqual(step.actor_type, 'field')

"""
apps/workflow/tests/test_workflow_tasks.py

API tests for GET /api/workflow/my-tasks/
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    CLIENT_READ,
    DEPARTMENT_READ,
    JOB_ROLE_READ,
    SITE_READ,
    SITE_ROLE_REQ_READ,
    WORKFLOW_APPROVE,
    WORKFLOW_REJECT,
)
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import MRFLineItem
from apps.mobilisation.models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedUser,
)
from apps.sites.models import Client, SiteProfile
from apps.workflow.models import (
    WorkflowTemplate,
    WorkflowStepTemplate,
    WorkflowTemplateMapping,
    StepAssignmentConfig,
    WorkflowStepInstance,
)
from apps.workflow.services import start_mrf_workflow, start_client_onboarding_workflow, act_on_step
from apps.workflow.tests.test_workflow_engine import (
    WorkflowTestBase,
    _user,
    _role,
    _assign,
    _node,
    _template,
    _step,
    _mapping,
    _sac,
    _mrf,
)


def _my_tasks_url(all_param=None):
    if all_param:
        return f'/api/workflow/my-tasks/?all={all_param}'
    return '/api/workflow/my-tasks/'


def _my_task_detail_url(step_id):
    return f'/api/workflow/my-tasks/{step_id}/'


class TestMyWorkflowTasks(WorkflowTestBase):

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def test_unauthenticated_returns_401(self):
        resp = self._api().get(_my_tasks_url())
        self.assertEqual(resp.status_code, 401)

    def test_hr_admin_sees_only_own_active_task(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        resp = self._api(self.hr_admin).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['count'], 1)
        row = resp.data['results'][0]
        self.assertEqual(row['step_code'], 'hr_review')
        self.assertEqual(row['assigned_user'], self.hr_admin.pk)
        self.assertEqual(row['target_type'], 'mrf')
        self.assertEqual(row['target_id'], local_mrf.pk)

    def test_finance_does_not_see_hr_task_before_hr_approves(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        resp = self._api(self.finance_user).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)

    def test_hr_task_disappears_after_hr_approves(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        self._api(self.hr_admin).post(
            f'/api/workflow/instances/{instance.pk}/steps/{step.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        resp = self._api(self.hr_admin).get(_my_tasks_url())
        self.assertEqual(resp.data['count'], 0)

    def test_finance_sees_task_after_hr_approves(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        self._api(self.hr_admin).post(
            f'/api/workflow/instances/{instance.pk}/steps/{step.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        resp = self._api(self.finance_user).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['step_code'], 'finance_review')

    def test_no_tasks_after_workflow_completes(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        s1 = instance.steps.filter(step_code='hr_review').first()
        self._api(self.hr_admin).post(
            f'/api/workflow/instances/{instance.pk}/steps/{s1.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        s2 = instance.steps.filter(status='active').first()
        self._api(self.finance_user).post(
            f'/api/workflow/instances/{instance.pk}/steps/{s2.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        for user in (self.hr_admin, self.finance_user):
            resp = self._api(user).get(_my_tasks_url())
            self.assertEqual(resp.data['count'], 0, user.username)

    def test_reject_back_finance_task_gone_hr_task_returns(self):
        loop_template = _template(self.org, 'tasks-loop-template')
        _step(loop_template, 1, 'step_a')
        _step(loop_template, 2, 'step_b', on_reject_target='step_a')
        WorkflowTemplateMapping.objects.create(
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
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step_a = instance.steps.filter(step_code='step_a').first()
        self._api(self.hr_admin).post(
            f'/api/workflow/instances/{instance.pk}/steps/{step_a.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        step_b = instance.steps.filter(status='active').first()
        self.assertEqual(step_b.step_code, 'step_b')
        self.assertEqual(self._api(self.finance_user).get(_my_tasks_url()).data['count'], 1)
        self._api(self.finance_user).post(
            f'/api/workflow/instances/{instance.pk}/steps/{step_b.pk}/act/',
            {'action': 'reject', 'comment': 'back to HR'}, format='json',
        )
        self.assertEqual(self._api(self.finance_user).get(_my_tasks_url()).data['count'], 0)
        hr_resp = self._api(self.hr_admin).get(_my_tasks_url())
        self.assertEqual(hr_resp.data['count'], 1)
        self.assertEqual(hr_resp.data['results'][0]['step_code'], 'step_a')

    def test_workflow_read_without_assignment_sees_nothing(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        resp = self._api(self.hr_exec).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)

    def test_scope_does_not_leak_other_client_tasks(self):
        n_b = _node(self.org, 'wf-client-b', 'client', self.n_company, 1, 'wf-org/wf-client-b')
        client_b = Client.objects.create(
            org=self.org, name='WF Client B', code='wf-client-b', scope_node=n_b,
        )
        n_site_b = _node(self.org, 'wf-site-b', 'site', n_b, 2, 'wf-org/wf-client-b/wf-site-b')
        site_b = SiteProfile.objects.create(
            org=self.org, client=client_b, name='WF Site B', code='wf-site-b', scope_node=n_site_b,
        )
        local_mrf = _mrf(self.org, site_b, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        active = instance.steps.filter(status='active').first()
        scoped_only = _user('wf_scoped_only', org=self.org)
        role = _role(self.org, 'scoped_bucket')
        bootstrap_role_permissions(role, caps=[
            CLIENT_READ, SITE_READ, SITE_ROLE_REQ_READ, JOB_ROLE_READ, DEPARTMENT_READ,
            WORKFLOW_APPROVE,
        ])
        _assign(scoped_only, role, self.n_client)
        WorkflowStepInstance.objects.filter(pk=active.pk).update(assigned_user=scoped_only)
        resp = self._api(scoped_only).get(_my_tasks_url())
        self.assertEqual(resp.data['count'], 0)

    def test_client_onboarding_task_shape(self):
        ob_template = WorkflowTemplate.objects.create(
            org=self.org, name='OB Tasks', code='ob-tasks-tpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        WorkflowStepTemplate.objects.create(
            template=ob_template, order=1, code='ob_hr', name='OB HR',
            assignment_mode='named_user', actor_type='internal',
            on_approve_next='END',
            requires_comment_on_reject=True, requires_comment_on_request_changes=True,
        )
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='client_onboarding', template=ob_template,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='client_onboarding', step_code='ob_hr',
            client=self.client_obj, assignment_mode='named_user',
            named_user=self.hr_admin, is_active=True,
        )
        req = MobilisationSetupRequest.objects.create(
            org=self.org, client=self.client_obj, requested_by=self.hr_exec,
            mobilisation_type='new_client', status='draft',
        )
        MobilisationProposedDepartment.objects.create(
            request=req, name='Test Dept', code='td-tasks',
            scope_level='client', is_active=True,
        )
        MobilisationProposedUser.objects.create(
            request=req, full_name='Test User', email='tasks-user@test.local',
            scope_level='client', access_role=self.role_admin, is_active=True,
        )
        start_client_onboarding_workflow(req, actor=self.hr_exec)
        resp = self._api(self.hr_admin).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        row = resp.data['results'][0]
        self.assertEqual(row['target_type'], 'mobilisation')
        self.assertEqual(row['target_id'], req.pk)
        self.assertEqual(row['target_url'], f'/mobilisation/{req.pk}')
        self.assertIn('Mobilisation', row['target_title'])
        self.assertIn(self.client_obj.name, row['target_title'])
        self.assertEqual(row['client_id'], self.client_obj.pk)
        self.assertIsNone(row['site_id'])
        self.assertIsNone(row['site_name'])
        self.assertIsNone(row['requesting_department_name'])
        self.assertIsNone(row['required_department_name'])
        self.assertIsNone(row['line_item_count'])

    def test_mrf_task_includes_line_item_count_and_departments(self):
        job_role = JobRole.objects.create(
            org=self.org, name='Line Test Role', code='line-test-role',
            skill_category='unskilled', is_active=True,
        )
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        MRFLineItem.objects.create(mrf=local_mrf, job_role=job_role, headcount=2)
        MRFLineItem.objects.create(mrf=local_mrf, job_role=job_role, headcount=1)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        resp = self._api(self.hr_admin).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        row = resp.data['results'][0]
        self.assertEqual(row['line_item_count'], 2)
        self.assertEqual(row['target_title'], f'MRF #{local_mrf.pk} - {self.site.name}')

    def test_superuser_default_only_assigned_superuser_all_true_sees_all(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        self.assertEqual(self._api(self.superuser).get(_my_tasks_url()).data['count'], 0)
        all_resp = self._api(self.superuser).get(_my_tasks_url(all_param='true'))
        self.assertEqual(all_resp.status_code, 200)
        self.assertEqual(all_resp.data['count'], 1)
        self.assertEqual(all_resp.data['results'][0]['step_code'], 'hr_review')

    def test_assignee_without_workflow_read_sees_assigned_task(self):
        n_bucket_site = _node(
            self.org, 'wf-site-bucket', 'site', self.n_client, 2,
            'wf-org/wf-client/wf-site-bucket',
        )
        site_bucket = SiteProfile.objects.create(
            org=self.org, client=self.client_obj,
            name='WF Site Bucket', code='wf-site-bucket', scope_node=n_bucket_site,
        )
        bucket_template = _template(self.org, 'bucket-mrf-tpl')
        _step(bucket_template, 1, 'hr_review')
        _step(bucket_template, 2, 'finance_review')
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='mrf', template=bucket_template,
            site=site_bucket, is_active=True,
        )
        bucket_user = User.objects.create_user(username='wf_bucket_only', password='pass')
        bucket_user.org = self.org
        bucket_user.save()
        role = _role(self.org, 'bucket_approver')
        bootstrap_role_permissions(role, caps=[
            CLIENT_READ, SITE_READ, SITE_ROLE_REQ_READ, JOB_ROLE_READ, DEPARTMENT_READ,
            WORKFLOW_APPROVE, WORKFLOW_REJECT,
        ])
        _assign(bucket_user, role, n_bucket_site)
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='hr_review',
            site=site_bucket, assignment_mode='named_user', named_user=bucket_user,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='finance_review',
            site=site_bucket, assignment_mode='named_user', named_user=self.finance_user,
            is_active=True,
        )
        local_mrf = _mrf(self.org, site_bucket, self.hr_exec)
        start_mrf_workflow(local_mrf, actor=self.hr_admin)
        resp = self._api(bucket_user).get(_my_tasks_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['assigned_user'], bucket_user.pk)


class TestMyWorkflowTaskDetail(WorkflowTestBase):

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def test_detail_unauthenticated_returns_401(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api().get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 401)

    def test_detail_assigned_hr_returns_200(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('task', resp.data)
        self.assertIn('workflow', resp.data)
        self.assertIn('target', resp.data)
        self.assertIn('actions', resp.data)
        self.assertEqual(resp.data['task']['step_id'], step.pk)
        self.assertEqual(resp.data['task']['step_code'], 'hr_review')
        self.assertEqual(resp.data['workflow']['id'], instance.pk)
        self.assertTrue(resp.data['workflow']['steps'])
        self.assertTrue(any(a['action'] == 'start' for a in resp.data['workflow']['audit_trail']))

    def test_detail_non_assigned_finance_returns_404(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(self.finance_user).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 404)

    def test_detail_workflow_read_not_assigned_returns_404(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(self.hr_exec).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 404)

    def test_detail_out_of_scope_returns_404(self):
        n_b = _node(self.org, 'wf-client-b2', 'client', self.n_company, 1, 'wf-org/wf-client-b2')
        client_b = Client.objects.create(
            org=self.org, name='WF Client B2', code='wf-client-b2', scope_node=n_b,
        )
        n_site_b = _node(self.org, 'wf-site-b2', 'site', n_b, 2, 'wf-org/wf-client-b2/wf-site-b2')
        site_b = SiteProfile.objects.create(
            org=self.org, client=client_b, name='WF Site B2', code='wf-site-b2', scope_node=n_site_b,
        )
        local_mrf = _mrf(self.org, site_b, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        active = instance.steps.filter(status='active').first()
        scoped_only = _user('wf_scoped_detail', org=self.org)
        role = _role(self.org, 'scoped_detail')
        bootstrap_role_permissions(role, caps=[
            CLIENT_READ, SITE_READ, SITE_ROLE_REQ_READ, JOB_ROLE_READ, DEPARTMENT_READ,
            WORKFLOW_APPROVE,
        ])
        _assign(scoped_only, role, self.n_client)
        WorkflowStepInstance.objects.filter(pk=active.pk).update(assigned_user=scoped_only)
        resp = self._api(scoped_only).get(_my_task_detail_url(active.pk))
        self.assertEqual(resp.status_code, 404)

    def test_detail_hr_step_404_after_approve_finance_detail_works(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        hr_step = instance.steps.filter(step_code='hr_review').first()
        self._api(self.hr_admin).post(
            f'/api/workflow/instances/{instance.pk}/steps/{hr_step.pk}/act/',
            {'action': 'approve'}, format='json',
        )
        self.assertEqual(
            self._api(self.hr_admin).get(_my_task_detail_url(hr_step.pk)).status_code,
            404,
        )
        fin_step = instance.steps.filter(status='active').first()
        resp = self._api(self.finance_user).get(_my_task_detail_url(fin_step.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['task']['step_code'], 'finance_review')

    def test_detail_mrf_includes_target_line_items_and_act_url(self):
        job_role = JobRole.objects.create(
            org=self.org, name='Detail Role', code='detail-role',
            skill_category='unskilled', is_active=True,
        )
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        MRFLineItem.objects.create(mrf=local_mrf, job_role=job_role, headcount=3)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['target']['type'], 'mrf')
        self.assertEqual(resp.data['target']['mrf']['id'], local_mrf.pk)
        self.assertEqual(resp.data['target']['mrf']['site'], self.site.pk)
        self.assertEqual(len(resp.data['target']['line_items']), 1)
        self.assertEqual(resp.data['target']['line_items'][0]['headcount'], 3)
        self.assertEqual(resp.data['target']['line_items'][0]['job_role_name'], 'Detail Role')
        self.assertIn('/api/workflow/instances/', resp.data['actions']['act_url'])
        self.assertTrue(resp.data['actions']['act_url'].rstrip('/').endswith(f'{instance.pk}/steps/{step.pk}/act'))

    def test_detail_mrf_includes_budget_reservation_fields(self):
        budget = BudgetPlan.objects.create(
            org=self.org,
            name='Task Detail Budget',
            code='task-detail-budget',
            budget_nature='billable',
            budget_type='manpower',
            client=self.client_obj,
            period_start=datetime.date(2026, 1, 1),
            amount=Decimal('50000.00'),
            currency='INR',
            status='active',
            is_active=True,
        )
        job_role = JobRole.objects.create(
            org=self.org, name='Reserved Detail Role', code='reserved-detail-role',
            skill_category='unskilled', is_active=True,
        )
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        local_mrf.budget_plan = budget
        local_mrf.save(update_fields=['budget_plan'])
        MRFLineItem.objects.create(
            mrf=local_mrf,
            job_role=job_role,
            headcount=2,
            budget_plan=budget,
            budget_max=Decimal('12000.00'),
        )
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))

        self.assertEqual(resp.status_code, 200, resp.data)
        mrf = resp.data['target']['mrf']
        self.assertEqual(mrf['budget_reserved_amount'], '12000.00')
        self.assertEqual(mrf['budget_committed_amount'], '0.00')
        self.assertEqual(mrf['budget_reservation_status'], 'reserved')

    def test_detail_client_onboarding_shape(self):
        ob_template = WorkflowTemplate.objects.create(
            org=self.org, name='OB Detail', code='ob-detail-tpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        WorkflowStepTemplate.objects.create(
            template=ob_template, order=1, code='ob_hr_d', name='OB HR D',
            assignment_mode='named_user', actor_type='internal',
            on_approve_next='END',
            requires_comment_on_reject=True, requires_comment_on_request_changes=True,
        )
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='client_onboarding', template=ob_template,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='client_onboarding', step_code='ob_hr_d',
            client=self.client_obj, assignment_mode='named_user',
            named_user=self.hr_admin, is_active=True,
        )
        req = MobilisationSetupRequest.objects.create(
            org=self.org, client=self.client_obj, requested_by=self.hr_exec,
            mobilisation_type='new_client', status='draft',
            summary='Onboarding summary text',
            operations_notes='Ops note',
        )
        start_client_onboarding_workflow(req, actor=self.hr_exec)
        step = WorkflowStepInstance.objects.filter(
            workflow__client_onboarding_request=req, status='active',
        ).first()
        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['target']['type'], 'mobilisation')
        self.assertEqual(resp.data['target']['line_items'], [])
        ob = resp.data['target']['mobilisation']
        self.assertEqual(ob['id'], req.pk)
        self.assertEqual(ob['status'], 'in_review')
        self.assertIn('Ops note', ob['notes'])

    def test_detail_superuser_can_access_assigned_others_step(self):
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(self.superuser).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['task']['assigned_user'], self.hr_admin.pk)

    def test_detail_assignee_without_mrf_or_workflow_read_ok(self):
        n_bucket_site = _node(
            self.org, 'wf-site-bucket-d', 'site', self.n_client, 2,
            'wf-org/wf-client/wf-site-bucket-d',
        )
        site_bucket = SiteProfile.objects.create(
            org=self.org, client=self.client_obj,
            name='WF Site Bucket D', code='wf-site-bucket-d', scope_node=n_bucket_site,
        )
        bucket_template = _template(self.org, 'bucket-mrf-tpl-d')
        _step(bucket_template, 1, 'hr_review')
        _step(bucket_template, 2, 'finance_review')
        WorkflowTemplateMapping.objects.create(
            org=self.org, trigger_type='mrf', template=bucket_template,
            site=site_bucket, is_active=True,
        )
        bucket_user = User.objects.create_user(username='wf_bucket_detail', password='pass')
        bucket_user.org = self.org
        bucket_user.save()
        role = _role(self.org, 'bucket_approver_d')
        bootstrap_role_permissions(role, caps=[
            CLIENT_READ, SITE_READ, SITE_ROLE_REQ_READ, JOB_ROLE_READ, DEPARTMENT_READ,
            WORKFLOW_APPROVE, WORKFLOW_REJECT,
        ])
        _assign(bucket_user, role, n_bucket_site)
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='hr_review',
            site=site_bucket, assignment_mode='named_user', named_user=bucket_user,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=self.org, trigger_type='mrf', step_code='finance_review',
            site=site_bucket, assignment_mode='named_user', named_user=self.finance_user,
            is_active=True,
        )
        local_mrf = _mrf(self.org, site_bucket, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()
        resp = self._api(bucket_user).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['target']['type'], 'mrf')


# ─── Onboarding task drawer tests ─────────────────────────────────────────────

class TestOnboardingTaskDrawerDetail(WorkflowTestBase):
    """
    Tests for GET /api/workflow/my-tasks/{step_id}/ when the task is linked
    to a mobilisation workflow — verifies proposed setup data is present.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='OB Drawer Guard', code='ob-drawer-guard',
            skill_category='unskilled', is_active=True,
        )

        cls.ob_template = WorkflowTemplate.objects.create(
            org=cls.org, name='OB Drawer Tpl', code='ob-drawer-tpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        WorkflowStepTemplate.objects.create(
            template=cls.ob_template, order=1, code='ob_drawer_review',
            name='OB Drawer Review',
            assignment_mode='named_user', actor_type='internal',
            on_approve_next='END',
            requires_comment_on_reject=False,
            requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='client_onboarding', template=cls.ob_template,
            is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='client_onboarding', step_code='ob_drawer_review',
            assignment_mode='named_user', named_user=cls.hr_admin, is_active=True,
        )

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def _start_ob_workflow(self, req):
        instance = start_client_onboarding_workflow(req, actor=self.hr_exec)
        step = WorkflowStepInstance.objects.filter(
            workflow=instance, status='active',
        ).first()
        return instance, step

    def _make_req(self, **kwargs):
        defaults = dict(
            org=self.org, client=self.client_obj, requested_by=self.hr_exec,
            mobilisation_type='new_client', status='draft',
            summary='Drawer test summary',
        )
        defaults.update(kwargs)
        return MobilisationSetupRequest.objects.create(**defaults)

    # ── test_01 ──────────────────────────────────────────────────────────────

    def test_01_drawer_has_mobilisation_scalar_fields(self):
        """mobilisation payload contains core scalar fields from the setup request."""
        req = self._make_req(summary='Custom summary text')
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['target']['type'], 'mobilisation')
        ob = resp.data['target']['mobilisation']
        self.assertEqual(ob['id'], req.pk)
        self.assertEqual(ob['mobilisation_type'], 'new_client')
        self.assertEqual(ob['client'], self.client_obj.pk)
        self.assertEqual(ob['client_name'], self.client_obj.name)
        self.assertEqual(ob['requested_by_username'], self.hr_exec.username)
        self.assertEqual(ob['summary'], 'Custom summary text')
        self.assertEqual(ob['finalization_status'], 'not_finalized')
        self.assertIsNone(ob['source_sales_lead'])

    # ── test_03 ──────────────────────────────────────────────────────────────

    def test_03_drawer_proposed_departments_with_real_site_name(self):
        """proposed_departments list includes real_site_name from FK."""
        req = self._make_req()
        MobilisationProposedDepartment.objects.create(
            request=req, real_site=self.site, name='Security Dept',
            code='drw-d1', scope_level='site', is_active=True,
        )
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        depts = resp.data['target']['mobilisation']['proposed_departments']
        self.assertEqual(len(depts), 1)
        self.assertEqual(depts[0]['name'], 'Security Dept')
        self.assertEqual(depts[0]['real_site_name'], self.site.name)

    # ── test_07 ──────────────────────────────────────────────────────────────

    def test_07_mrf_target_unchanged_by_onboarding_changes(self):
        """MRF task drawer still returns correct mrf target shape with no proposed_* keys."""
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['target']['type'], 'mrf')
        mrf_data = resp.data['target']['mrf']
        self.assertEqual(mrf_data['id'], local_mrf.pk)
        self.assertNotIn('proposed_sites', mrf_data)
        self.assertNotIn('proposed_client_name', mrf_data)

    # ── test_08 ──────────────────────────────────────────────────────────────

    def test_08_non_assigned_user_gets_404_for_onboarding_task(self):
        """A user not assigned to the step cannot access the onboarding task drawer."""
        req = self._make_req()
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.finance_user).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 404)

    # ── test_09 ──────────────────────────────────────────────────────────────

    def test_09_proposed_users_in_drawer(self):
        """mobilisation drawer contains a proposed_users list with correct base fields."""
        req = self._make_req()
        MobilisationProposedUser.objects.create(
            request=req,
            full_name='Dana Lee',
            email='dana@drawertest.com',
            user_type='client',
            access_role=self.role_admin,
            scope_level='client',
            is_primary_contact=True,
            is_active=True,
        )
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        ob = resp.data['target']['mobilisation']
        self.assertIn('proposed_users', ob)
        users = ob['proposed_users']
        self.assertEqual(len(users), 1)
        u = users[0]
        self.assertEqual(u['email'], 'dana@drawertest.com')
        self.assertEqual(u['full_name'], 'Dana Lee')
        self.assertTrue(u['is_primary_contact'])
        self.assertEqual(u['invite_status'], 'pending')

    # ── test_10 ──────────────────────────────────────────────────────────────

    def test_10_proposed_users_role_and_site_display_fields(self):
        """proposed_users entries include access_role_code, access_role_name, real_site_name."""
        req = self._make_req()
        MobilisationProposedUser.objects.create(
            request=req,
            full_name='Eve Torres',
            email='eve@drawertest.com',
            user_type='client',
            access_role=self.role_admin,
            scope_level='site',
            real_site=self.site,
            is_active=True,
        )
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        users = resp.data['target']['mobilisation']['proposed_users']
        self.assertEqual(len(users), 1)
        u = users[0]
        self.assertEqual(u['access_role_code'], self.role_admin.code)
        self.assertEqual(u['access_role_name'], self.role_admin.name)
        self.assertEqual(u['real_site_name'], self.site.name)
        self.assertEqual(u['scope_level'], 'site')

    # ── test_11 ──────────────────────────────────────────────────────────────

    def test_11_inactive_proposed_users_included_in_drawer(self):
        """Both active and inactive proposed users are returned so approver sees full submission."""
        req = self._make_req()
        MobilisationProposedUser.objects.create(
            request=req, full_name='Active User', email='active@drawertest.com',
            user_type='client', access_role=self.role_admin,
            scope_level='client', is_active=True,
        )
        MobilisationProposedUser.objects.create(
            request=req, full_name='Inactive User', email='inactive@drawertest.com',
            user_type='client', access_role=self.role_admin,
            scope_level='client', is_active=False,
        )
        _, step = self._start_ob_workflow(req)

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        users = resp.data['target']['mobilisation']['proposed_users']
        self.assertEqual(len(users), 2)
        active_flags = {u['email']: u['is_active'] for u in users}
        self.assertTrue(active_flags['active@drawertest.com'])
        self.assertFalse(active_flags['inactive@drawertest.com'])

    # ── test_12 ──────────────────────────────────────────────────────────────

    def test_12_mrf_drawer_has_no_proposed_users_key(self):
        """MRF task drawer does not contain proposed_users — regression check."""
        local_mrf = _mrf(self.org, self.site, self.hr_exec)
        instance = start_mrf_workflow(local_mrf, actor=self.hr_admin)
        step = instance.steps.filter(status='active').first()

        resp = self._api(self.hr_admin).get(_my_task_detail_url(step.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        mrf_data = resp.data['target']['mrf']
        self.assertNotIn('proposed_users', mrf_data)

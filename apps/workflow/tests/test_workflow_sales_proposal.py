"""
Sales proposal internal approval workflow tests (trigger_type sales_proposal).
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_CONFIG_READ,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement, ProposalVersion
from apps.sales.services import generate_proposal_version, submit_to_operations
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate, WorkflowTemplateMapping,
    StepAssignmentConfig, WorkflowInstance,
)
from apps.workflow.services import start_sales_proposal_workflow, act_on_step
from apps.workflow.tests.helpers import bootstrap_legacy_workflow


def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )


def _user(username, org):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    return u


def _grant_workflow(user, org, scope_node, extra_caps=None):
    caps = [WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE]
    if extra_caps:
        caps.extend(extra_caps)
    role = AccessRole.objects.get_or_create(
        org=org, code=f'wf_{user.username}', defaults={'name': f'wf_{user.username}'},
    )[0]
    bootstrap_role_permissions(role, caps)
    UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)
    return role


def _proposal(org, user):
    from apps.sales.tests.proposal_wage_fixtures import (
        ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
        wire_site_and_requirement_for_wages,
    )
    lead = SalesLead.objects.create(
        org=org, client_name='Acme Corp', client_email='acme@test.com', sales_person=user,
    )
    site = SalesLeadSite.objects.create(lead=lead, site_name='HQ', city='Mumbai', state='MH')
    jr = JobRole.objects.get_or_create(org=org, code='guard', defaults={'name': 'Guard'})[0]
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=jr, manpower_count=2, is_active=True,
    )
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=12000)
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    return generate_proposal_version(lead, user)


class TestSalesProposalTriggerType(TestCase):
    def test_template_accepts_sales_proposal_trigger(self):
        org = _org('sp_trig')
        tpl = WorkflowTemplate.objects.create(
            org=org, name='SP', code='sp_tpl', trigger_type='sales_proposal', version=1, is_active=True,
        )
        self.assertEqual(tpl.trigger_type, 'sales_proposal')

    def test_sac_accepts_sales_proposal_trigger(self):
        org = _org('sp_sac')
        user = _user('u_sac', org)
        sac = StepAssignmentConfig.objects.create(
            org=org, trigger_type='sales_proposal', step_code='review',
            assignment_mode='named_user', named_user=user, is_active=True,
        )
        self.assertEqual(sac.trigger_type, 'sales_proposal')


class TestStartSalesProposalWorkflow(TestCase):
    def setUp(self):
        self.org = _org('sp_start')
        self.scope = _node(self.org)
        self.user = _user('u_start', self.org)
        self.approver = _user('u_appr', self.org)
        _grant_workflow(self.user, self.org, self.scope)
        _grant_workflow(self.approver, self.org, self.scope)
        bootstrap_legacy_workflow(
            self.org, 'sales_proposal',
            [(1, 'review', 'Review')],
            self.approver,
        )
        self.proposal = _proposal(self.org, self.user)

    def test_creates_instance_with_proposal_version_only(self):
        instance = start_sales_proposal_workflow(self.proposal, self.user)
        self.assertIsNotNone(instance.pk)
        self.assertIsNone(instance.mrf_id)
        self.assertIsNone(instance.client_onboarding_request_id)
        self.assertEqual(instance.proposal_version_id, self.proposal.pk)

    def test_exactly_one_target_constraint_rejects_multiple_targets(self):
        from apps.mrf.models import ManpowerRequest
        from apps.sites.models import Client, SiteProfile

        template = WorkflowTemplate.objects.filter(org=self.org).first()
        client = Client.objects.create(org=self.org, name='C', code='c1')
        site = SiteProfile.objects.create(
            org=self.org, client=client, name='S', code='s1', is_active=True,
        )
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=site, requested_by=self.user,
            mrf_type='new_hiring', status='draft', billing_type='billable',
        )
        bad = WorkflowInstance(
            org=self.org,
            mrf=mrf,
            proposal_version=self.proposal,
            template=template,
            template_version=1,
            initiated_by=self.user,
        )
        with self.assertRaises(DjangoValidationError):
            bad.full_clean()

    def test_duplicate_active_workflow_blocked(self):
        start_sales_proposal_workflow(self.proposal, self.user)
        with self.assertRaises(WorkflowConfigurationError):
            start_sales_proposal_workflow(self.proposal, self.user)

    def test_start_updates_proposal_status_and_timestamp(self):
        start_sales_proposal_workflow(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, 'submitted_internal')
        self.assertEqual(self.proposal.internal_approval_status, 'in_progress')
        self.assertIsNotNone(self.proposal.submitted_internal_at)

    def test_approval_completion_internally_approves_proposal(self):
        instance = start_sales_proposal_workflow(self.proposal, self.user)
        step = instance.steps.filter(status='active').first()
        act_on_step(step, self.approver, 'approve')
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, 'internally_approved')
        self.assertEqual(self.proposal.internal_approval_status, 'approved')
        self.assertIsNotNone(self.proposal.internally_approved_at)

    def test_rejection_sets_internal_rejected_and_sales_review_stage(self):
        instance = start_sales_proposal_workflow(self.proposal, self.user)
        step = instance.steps.filter(status='active').first()
        act_on_step(step, self.approver, 'reject', comment='Needs changes')
        self.proposal.refresh_from_db()
        self.proposal.lead.refresh_from_db()
        self.assertEqual(self.proposal.status, 'internal_rejected')
        self.assertEqual(self.proposal.internal_approval_status, 'rejected')
        self.assertEqual(self.proposal.lead.current_stage, 'sales_review')


class TestSalesProposalWorkflowAPI(TestCase):
    def setUp(self):
        self.org = _org('sp_api')
        self.scope = _node(self.org)
        self.user = _user('u_api', self.org)
        self.approver = _user('u_api_appr', self.org)
        _grant_workflow(
            self.user, self.org, self.scope,
            extra_caps=[WORKFLOW_CONFIG_READ],
        )
        bootstrap_legacy_workflow(
            self.org, 'sales_proposal',
            [(1, 'review', 'Review')],
            self.approver,
        )
        self.proposal = _proposal(self.org, self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_start_endpoint_returns_201(self):
        url = f'/api/workflow/sales-proposals/{self.proposal.pk}/start/'
        resp = self.client.post(url, {}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['proposal_version'], self.proposal.pk)

    def test_routes_available_supports_sales_proposal(self):
        resp = self.client.get(
            '/api/workflow/routes/available/?trigger_type=sales_proposal',
        )
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_config_preview_supports_sales_proposal(self):
        resp = self.client.get(
            '/api/workflow/config/preview/?request_type=sales_proposal',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['request_type'], 'sales_proposal')
        self.assertTrue(resp.data['ok'], resp.data)


class TestSalesProposalMyTasks(TestCase):
    def setUp(self):
        self.org = _org('sp_tasks')
        self.scope = _node(self.org)
        self.submitter = _user('u_sub', self.org)
        self.approver = _user('u_task_appr', self.org)
        _grant_workflow(self.submitter, self.org, self.scope)
        _grant_workflow(self.approver, self.org, self.scope)
        bootstrap_legacy_workflow(
            self.org, 'sales_proposal',
            [(1, 'review', 'Review')],
            self.approver,
        )
        self.proposal = _proposal(self.org, self.submitter)
        start_sales_proposal_workflow(self.proposal, self.submitter)

    def test_my_tasks_list_includes_sales_proposal_target(self):
        c = APIClient()
        c.force_authenticate(self.approver)
        resp = c.get('/api/workflow/my-tasks/')
        self.assertEqual(resp.status_code, 200, resp.data)
        types = [r['target_type'] for r in resp.data['results']]
        self.assertIn('sales_proposal', types)
        row = next(r for r in resp.data['results'] if r['target_type'] == 'sales_proposal')
        self.assertEqual(row['target_id'], self.proposal.pk)

    def test_my_task_detail_includes_budget_and_breakup_lines(self):
        step = WorkflowInstance.objects.get(
            proposal_version=self.proposal, status='active',
        ).steps.filter(status='active').first()
        c = APIClient()
        c.force_authenticate(self.approver)
        resp = c.get(f'/api/workflow/my-tasks/{step.pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['target']['type'], 'sales_proposal')
        self.assertIn('budget_lines', resp.data['target'])
        self.assertIn('breakup_lines', resp.data['target'])
        self.assertIn('sales_proposal', resp.data['target'])


class TestSeedSalesProposalWorkflowDemo(TestCase):
    def test_seed_command_idempotent(self):
        from django.core.management import call_command
        _org('seed_sp_org')
        call_command('seed_sales_proposal_workflow_demo', '--org', 'seed_sp_org')
        call_command('seed_sales_proposal_workflow_demo', '--org', 'seed_sp_org')
        org = Organization.objects.get(code='seed_sp_org')
        self.assertTrue(
            WorkflowTemplate.objects.filter(org=org, trigger_type='sales_proposal').exists()
        )
        self.assertTrue(
            WorkflowTemplateMapping.objects.filter(org=org, trigger_type='sales_proposal').exists()
        )

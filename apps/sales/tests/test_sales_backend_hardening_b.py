"""
apps/sales/tests/test_sales_backend_hardening_b.py

Phase Sales-Backend-Hardening-B focused tests.

Scenarios:
  1.  submit_to_operations sets submitted_to_operations_at/by and creates SiteSurvey per site.
  2.  submit_to_operations accepts operations_owner via API.
  3.  submit_to_operations from non-draft stage raises 400.
  4.  assign_survey_owner sets assigned_to and assigned_at.
  5.  mark_survey_started sets status=in_progress, started_at; advances lead to site_survey_in_progress.
  6.  mark_survey_completed sets status=completed, completed_at; advances lead when all done.
  7.  mark_survey_completed does not advance lead if other surveys still pending.
  8.  approve_sales_role_requirement sets approved_by_operations/at/by; double-approve returns 400.
  9.  generate_proposal_version blocked from draft stage.
  10. generate_proposal_version blocked when survey not completed.
  11. generate_proposal_version blocked when site has no role requirements.
  12. generate_proposal_version populates role_requirement FK on budget lines.
  13. submit_proposal_for_internal_approval sets submitted_internal_at.
  14. mark_proposal_internally_approved sets internally_approved_at.
  15. record_client_response sets client_approved_at on approval + stores negotiation metadata.
  16. clone_proposal_for_revision copies role_requirement and override fields.
  17. SalesLead serializer exposes new fields (lead_source, industry, priority, etc.).
  18. SiteSurvey serializer exposes assigned_to_name, started_at, completed_at, survey_notes.
  19. SalesRoleRequirement serializer exposes approved_by_operations, approved_at.
  20. ProposalBudgetLine serializer exposes role_requirement, is_manual_override.
  21. ClientProposalResponse serializer exposes responded_by_name, meeting_notes.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE, SALES_LEAD_DELETE,
    SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
    SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_READ, SALES_SURVEY_UPDATE, SALES_SURVEY_ASSIGN,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode, Department
from apps.jobs.models import JobRole
from apps.sales.models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine,
    SiteSurveyShiftDeployment, SurveyRoleMapping,
)
from apps.sales.services import (
    submit_to_operations, assign_survey_owner, mark_survey_started, mark_survey_completed,
    approve_sales_role_requirement,
    generate_role_requirements_from_survey,
    generate_proposal_version,
    submit_proposal_for_internal_approval, mark_proposal_internally_approved,
    send_proposal_to_client, record_client_response, mark_lead_won_from_client_approval,
    clone_proposal_for_revision,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules
from apps.wages.models import WageCategory


# ─── URL prefixes ─────────────────────────────────────────────────────────────

LEADS_URL = '/api/sales/leads/'
SURVEYS_URL = '/api/sales/site-surveys/'
ROLE_REQS_URL = '/api/sales/role-requirements/'
PROPOSALS_URL = '/api/sales/proposal-versions/'
BUDGET_LINES_URL = '/api/sales/proposal-budget-lines/'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code):
    org = Organization.objects.create(name=f'Org {code}', code=code)
    seed_default_proposal_component_rules(org=org)
    return org


def _scope_node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )


def _operations_department(org):
    return Department.objects.get_or_create(
        org=org,
        code='operations',
        client=None,
        site=None,
        defaults={'name': 'Operations', 'is_active': True},
    )[0]


def _all_sales_caps():
    return [
        SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE, SALES_LEAD_DELETE,
        SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
        SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_READ, SALES_SURVEY_UPDATE, SALES_SURVEY_ASSIGN,
    ]


def _role(org, code, caps):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    bootstrap_role_permissions(role, caps)
    return role


def _user(username, org, role=None, scope_node=None):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _assign_to_operations_department(user):
    user.department = _operations_department(user.org)
    user.save(update_fields=['department', 'updated_at'])
    return user


def _job_role(org, name='Security Guard'):
    return JobRole.objects.get_or_create(
        org=org, code=name.lower().replace(' ', '_'),
        defaults={'name': name},
    )[0]


def _draft_lead_with_site_and_rr(org, job_role_name='Security Guard', manpower_count=5):
    lead = SalesLead.objects.create(
        org=org, client_name='Test Client', client_email='client@test.com',
        current_stage='draft', current_status='draft',
    )
    site = SalesLeadSite.objects.create(lead=lead, site_name='Site A', city='Mumbai', state='MH')
    jr = _job_role(org, job_role_name)
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=jr, manpower_count=manpower_count, is_active=True,
    )
    return lead, site, rr, jr


def _ready_lead(org, user):
    """Lead with completed survey and RR, stage=site_survey_completed."""
    from apps.sales.tests.proposal_wage_fixtures import (
        ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
        wire_site_and_requirement_for_wages,
    )
    lead, site, rr, jr = _draft_lead_with_site_and_rr(org)
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=12000)
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    return lead, site, rr


# ─── 1. submit_to_operations ─────────────────────────────────────────────────

class TestSubmitToOperations(TestCase):
    def setUp(self):
        self.org = _org('sto2')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_sto2', self.org, self.role, self.n)

    def test_creates_survey_per_active_site(self):
        lead, site, _, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        self.assertEqual(SiteSurvey.objects.filter(lead=lead).count(), 1)
        survey = SiteSurvey.objects.get(lead=lead)
        self.assertEqual(survey.site, site)
        self.assertEqual(survey.status, 'pending')

    def test_sets_submitted_at_and_by(self):
        lead, _, _, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        lead.refresh_from_db()
        self.assertIsNotNone(lead.submitted_to_operations_at)
        self.assertEqual(lead.submitted_to_operations_by, self.user)

    def test_idempotent_survey_creation(self):
        lead, site, _, _ = _draft_lead_with_site_and_rr(self.org)
        # Pre-existing survey should not be duplicated
        SiteSurvey.objects.create(lead=lead, site=site, status='pending')
        submit_to_operations(lead, self.user)
        self.assertEqual(SiteSurvey.objects.filter(lead=lead).count(), 1)

    def test_multiple_sites_create_multiple_surveys(self):
        lead, _, _, _ = _draft_lead_with_site_and_rr(self.org)
        SalesLeadSite.objects.create(lead=lead, site_name='Site B', city='Delhi', state='DL')
        submit_to_operations(lead, self.user)
        self.assertEqual(SiteSurvey.objects.filter(lead=lead).count(), 2)


# ─── 2. submit_to_operations with operations_owner via API ───────────────────

class TestSubmitToOperationsAPI(TestCase):
    def setUp(self):
        self.org = _org('sto2api')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm2', _all_sales_caps())
        self.user = _user('sm_sto2api', self.org, self.role, self.n)
        self.ops_user = _user('ops_sto2api', self.org, self.role, self.n)
        _assign_to_operations_department(self.ops_user)
        self.lead, _, _, _ = _draft_lead_with_site_and_rr(self.org)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_submit_with_operations_owner(self):
        resp = self._api().post(
            f'{LEADS_URL}{self.lead.pk}/submit-to-operations/',
            {'operations_owner': self.ops_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.operations_owner, self.ops_user)


# ─── 4. assign_survey_owner ───────────────────────────────────────────────────

class TestAssignSurveyOwner(TestCase):
    def setUp(self):
        self.org = _org('aso')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_aso', _all_sales_caps())
        self.user = _user('sm_aso', self.org, self.role, self.n)
        self.assignee = _user('ops_aso', self.org, self.role, self.n)
        _assign_to_operations_department(self.assignee)
        lead, site, _, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        self.survey = SiteSurvey.objects.get(lead=lead)

    def test_assign_sets_assigned_to_and_at(self):
        assign_survey_owner(self.survey, self.assignee, self.user)
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.assigned_to, self.assignee)
        self.assertIsNotNone(self.survey.assigned_at)

    def test_assign_owner_via_api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        resp = c.post(
            f'{SURVEYS_URL}{self.survey.pk}/assign-owner/',
            {'assigned_to': self.assignee.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['assigned_to'], self.assignee.pk)

    def test_assign_owner_via_api_requires_assign_capability(self):
        limited_role = _role(
            self.org,
            'survey_update_only',
            [SALES_SURVEY_READ, SALES_SURVEY_UPDATE],
        )
        limited_user = _user('survey_update_only', self.org, limited_role, self.n)
        c = APIClient()
        c.force_authenticate(limited_user)
        resp = c.post(
            f'{SURVEYS_URL}{self.survey.pk}/assign-owner/',
            {'assigned_to': self.assignee.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_assign_owner_rejects_non_operations_user(self):
        non_ops = _user('non_ops_aso', self.org, self.role, self.n)
        c = APIClient()
        c.force_authenticate(self.user)
        resp = c.post(
            f'{SURVEYS_URL}{self.survey.pk}/assign-owner/',
            {'assigned_to': non_ops.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('Operations department', resp.data['detail'])


# ─── 5. mark_survey_started ───────────────────────────────────────────────────

class TestMarkSurveyStarted(TestCase):
    def setUp(self):
        self.org = _org('mss')
        self.user = _user('u_mss', self.org)
        lead, site, _, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        self.survey = SiteSurvey.objects.get(lead=lead)
        self.lead = lead

    def test_sets_status_in_progress_and_started_at(self):
        mark_survey_started(self.survey, self.user)
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, 'in_progress')
        self.assertIsNotNone(self.survey.started_at)

    def test_advances_lead_to_survey_in_progress(self):
        mark_survey_started(self.survey, self.user)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'site_survey_in_progress')

    def test_does_not_downgrade_lead_if_already_past_submitted(self):
        self.lead.current_stage = 'site_survey_in_progress'
        self.lead.save()
        mark_survey_started(self.survey, self.user)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'site_survey_in_progress')


# ─── 6 & 7. mark_survey_completed ────────────────────────────────────────────

class TestMarkSurveyCompleted(TestCase):
    def setUp(self):
        self.org = _org('msc')
        self.node = _scope_node(self.org)
        self.role = _role(self.org, 'survey_msc', [SALES_SURVEY_READ, SALES_SURVEY_UPDATE])
        self.user = _user('u_msc', self.org, self.role, self.node)
        lead, site, _, jr = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        self.lead = lead
        self.site = site
        self.job_role = jr
        self.survey = SiteSurvey.objects.get(lead=lead)
        SiteSurveyShiftDeployment.objects.filter(survey=self.survey).delete()
        self.wage_category = WageCategory.objects.create(
            name='Skilled MSC',
            code='skilled_msc',
        )
        SurveyRoleMapping.objects.create(
            org=self.org,
            description_text='Security Guard',
            job_role=self.job_role,
            wage_category=self.wage_category,
            service_category='Security',
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey,
            description='Security Guard',
            total_count=5,
            line_type='item',
            is_applicable=True,
        )

    def _create_generated_requirement(self):
        generate_role_requirements_from_survey(self.survey, self.user)
        return SalesRoleRequirement.objects.get(
            lead=self.lead,
            site=self.site,
            survey=self.survey,
            job_role=self.job_role,
        )

    def test_blocks_completion_before_role_generation(self):
        with self.assertRaisesRegex(ValueError, 'Generate role requirements'):
            mark_survey_completed(self.survey, self.user)
        self.survey.refresh_from_db()
        self.assertNotEqual(self.survey.status, 'completed')

    def test_sets_status_completed_and_completed_at(self):
        self._create_generated_requirement()
        mark_survey_completed(self.survey, self.user)
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, 'completed')
        self.assertIsNotNone(self.survey.completed_at)

    def test_advances_lead_when_all_surveys_done(self):
        self._create_generated_requirement()
        mark_survey_completed(self.survey, self.user)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'site_survey_completed')

    def test_does_not_advance_lead_when_other_surveys_pending(self):
        self._create_generated_requirement()
        site2 = SalesLeadSite.objects.create(lead=self.lead, site_name='Site B')
        survey2 = SiteSurvey.objects.create(lead=self.lead, site=site2, status='pending')
        mark_survey_completed(self.survey, self.user)
        self.lead.refresh_from_db()
        # Still pending because survey2 is not done
        self.assertNotEqual(self.lead.current_stage, 'site_survey_completed')

    def test_blocks_completion_when_applicable_row_has_no_generated_requirement(self):
        self._create_generated_requirement()
        helper_role = _job_role(self.org, 'Helper')
        SurveyRoleMapping.objects.create(
            org=self.org,
            description_text='Helper',
            job_role=helper_role,
            wage_category=self.wage_category,
            service_category='Housekeeping',
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey,
            description='Helper',
            total_count=2,
            line_type='item',
            is_applicable=True,
            sort_order=2,
        )

        with self.assertRaisesRegex(ValueError, 'every applicable deployment row'):
            mark_survey_completed(self.survey, self.user)

    def test_blocks_completion_when_generated_requirement_is_outdated(self):
        self._create_generated_requirement()
        row = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey,
            description='Security Guard',
        )
        row.total_count = 8
        row.save(update_fields=['total_count', 'updated_at'])

        with self.assertRaisesRegex(ValueError, 'Regenerate role requirements'):
            mark_survey_completed(self.survey, self.user)

    def test_endpoint_returns_400_before_role_generation(self):
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.post(f'{SURVEYS_URL}{self.survey.pk}/mark-completed/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Generate role requirements', resp.data['detail'])


# ─── 8. approve_sales_role_requirement ───────────────────────────────────────

class TestApproveSalesRoleRequirement(TestCase):
    def setUp(self):
        self.org = _org('arr')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_arr', _all_sales_caps())
        self.user = _user('sm_arr', self.org, self.role, self.n)
        _, _, self.rr, _ = _draft_lead_with_site_and_rr(self.org)

    def test_approve_sets_fields(self):
        approve_sales_role_requirement(self.rr, self.user)
        self.rr.refresh_from_db()
        self.assertTrue(self.rr.approved_by_operations)
        self.assertIsNotNone(self.rr.approved_at)
        self.assertEqual(self.rr.approved_by, self.user)

    def test_double_approve_via_api_returns_400(self):
        approve_sales_role_requirement(self.rr, self.user)
        c = APIClient()
        c.force_authenticate(self.user)
        resp = c.post(f'{ROLE_REQS_URL}{self.rr.pk}/approve/', format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Already approved', resp.data['detail'])


# ─── 9, 10, 11. generate_proposal_version guardrails ─────────────────────────

class TestGenerateProposalGuardrails(TestCase):
    def setUp(self):
        self.org = _org('gpg')
        self.user = _user('u_gpg', self.org)

    def test_blocked_from_draft_stage(self):
        lead, _, _, _ = _draft_lead_with_site_and_rr(self.org)
        with self.assertRaises(ValueError) as cm:
            generate_proposal_version(lead, self.user)
        self.assertIn("draft", str(cm.exception))

    def test_blocked_when_survey_not_completed(self):
        lead, site, rr, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        # Survey exists but is pending — do NOT complete it
        lead.current_stage = 'site_survey_completed'
        lead.save()
        with self.assertRaises(ValueError) as cm:
            generate_proposal_version(lead, self.user)
        self.assertIn("completed survey", str(cm.exception))

    def test_blocked_when_site_has_no_role_requirements(self):
        lead, site, rr, _ = _draft_lead_with_site_and_rr(self.org)
        submit_to_operations(lead, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save()
        rr.is_active = False
        rr.save()
        with self.assertRaises(ValueError) as cm:
            generate_proposal_version(lead, self.user)
        self.assertIn("role requirements", str(cm.exception))


# ─── 12. role_requirement FK on budget lines ─────────────────────────────────

class TestProposalBudgetLineRoleFk(TestCase):
    def setUp(self):
        self.org = _org('rlfk')
        self.user = _user('u_rlfk', self.org)
        self.lead, self.site, self.rr = _ready_lead(self.org, self.user)

    def test_budget_line_has_role_requirement_fk(self):
        proposal = generate_proposal_version(self.lead, self.user)
        line = proposal.budget_lines.first()
        self.assertIsNotNone(line.role_requirement_id)
        self.assertEqual(line.role_requirement, self.rr)

    def test_budget_line_patch_marks_sales_override_and_recalculates_total(self):
        role = _role(self.org, 'proposal_editor', _all_sales_caps())
        scope = _scope_node(self.org)
        UserRoleAssignment.objects.create(user=self.user, role=role, scope_node=scope)
        proposal = generate_proposal_version(self.lead, self.user)
        line = proposal.budget_lines.first()

        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.patch(
            f'{BUDGET_LINES_URL}{line.pk}/',
            {'unit_cost': '25000.00', 'manpower_count': 3},
            format='json',
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('25000.00'))
        self.assertEqual(line.manpower_count, 3)
        self.assertEqual(line.total_cost, Decimal('75000.00'))
        self.assertTrue(line.is_manual_override)
        self.assertEqual(line.overridden_by_id, self.user.pk)
        self.assertIsNotNone(line.overridden_at)


# ─── 13. submitted_internal_at ───────────────────────────────────────────────

class TestProposalTimestamps(TestCase):
    def setUp(self):
        self.org = _org('pts')
        self.user = _user('u_pts', self.org)
        lead, _, _ = _ready_lead(self.org, self.user)
        self.proposal = generate_proposal_version(lead, self.user)
        from apps.workflow.tests.helpers import bootstrap_legacy_workflow
        bootstrap_legacy_workflow(
            self.org, 'sales_proposal', [(1, 'review', 'Review')], self.user,
        )

    def test_submit_internal_sets_timestamp(self):
        submit_proposal_for_internal_approval(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertIsNotNone(self.proposal.submitted_internal_at)

    def test_internally_approved_sets_timestamp(self):
        self.proposal.status = 'submitted_internal'
        self.proposal.internal_approval_status = 'in_progress'
        self.proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
        mark_proposal_internally_approved(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertIsNotNone(self.proposal.internally_approved_at)

    def test_client_approved_sets_client_approved_at(self):
        self.proposal.status = 'submitted_internal'
        self.proposal.internal_approval_status = 'in_progress'
        self.proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
        mark_proposal_internally_approved(self.proposal, self.user)
        send_proposal_to_client(self.proposal, self.user)
        record_client_response(self.proposal, 'approved', 'Good', self.user)
        self.proposal.refresh_from_db()
        self.assertIsNotNone(self.proposal.client_approved_at)


# ─── 15. record_client_response negotiation metadata ─────────────────────────

class TestClientResponseMetadata(TestCase):
    def setUp(self):
        self.org = _org('crm')
        self.user = _user('u_crm', self.org)
        lead, _, _ = _ready_lead(self.org, self.user)
        proposal = generate_proposal_version(lead, self.user)
        from apps.workflow.tests.helpers import bootstrap_legacy_workflow
        proposal.status = 'submitted_internal'
        proposal.internal_approval_status = 'in_progress'
        proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
        mark_proposal_internally_approved(proposal, self.user)
        send_proposal_to_client(proposal, self.user)
        self.proposal = proposal

    def test_negotiation_metadata_saved_to_response(self):
        from apps.sales.models import ClientProposalResponse
        record_client_response(
            self.proposal, 'negotiation_required', 'Cost too high', self.user,
            responded_by_name='John Client',
            responded_by_email='john@client.com',
            next_action_due_date='2026-06-01',
            meeting_notes='Discussed billing rates',
        )
        cpr = ClientProposalResponse.objects.filter(
            proposal_version=self.proposal,
        ).order_by('-created_at').first()
        self.assertEqual(cpr.responded_by_name, 'John Client')
        self.assertEqual(cpr.responded_by_email, 'john@client.com')
        self.assertEqual(str(cpr.next_action_due_date), '2026-06-01')
        self.assertEqual(cpr.meeting_notes, 'Discussed billing rates')


# ─── 16. clone_proposal_for_revision copies new fields ───────────────────────

class TestCloneProposalNewFields(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.org = _org('cpnf')
        self.user = _user('u_cpnf', self.org)
        lead, _, rr = _ready_lead(self.org, self.user)
        self.proposal = generate_proposal_version(lead, self.user)
        line = self.proposal.budget_lines.first()
        line.is_manual_override = True
        line.override_reason = 'Market adjustment'
        line.overridden_by = self.user
        line.overridden_at = timezone.now()
        line.save()

    def test_clone_copies_role_requirement_and_override(self):
        new_proposal = clone_proposal_for_revision(self.proposal, self.user)
        orig_line = self.proposal.budget_lines.first()
        new_line = new_proposal.budget_lines.first()
        self.assertEqual(new_line.role_requirement_id, orig_line.role_requirement_id)
        self.assertTrue(new_line.is_manual_override)
        self.assertEqual(new_line.override_reason, 'Market adjustment')
        self.assertEqual(new_line.overridden_by, self.user)


# ─── 17–21. Serializer field exposure ────────────────────────────────────────

class TestSerializerFieldExposure(TestCase):
    def setUp(self):
        self.org = _org('sfe')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_sfe', _all_sales_caps())
        self.user = _user('sm_sfe', self.org, self.role, self.n)
        self.lead, self.site, self.rr = _ready_lead(self.org, self.user)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_lead_serializer_exposes_new_fields(self):
        resp = self._api().get(f'/api/sales/leads/{self.lead.pk}/')
        self.assertEqual(resp.status_code, 200)
        for field in ('lead_source', 'industry', 'priority', 'expected_start_date',
                      'submitted_to_operations_at', 'operations_owner'):
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_survey_serializer_exposes_new_fields(self):
        survey = SiteSurvey.objects.get(lead=self.lead)
        resp = self._api().get(f'/api/sales/site-surveys/{survey.pk}/')
        self.assertEqual(resp.status_code, 200)
        for field in ('assigned_to', 'assigned_to_name', 'started_at', 'completed_at',
                      'due_date', 'survey_notes'):
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_role_requirement_serializer_exposes_approval_fields(self):
        resp = self._api().get(f'/api/sales/role-requirements/{self.rr.pk}/')
        self.assertEqual(resp.status_code, 200)
        for field in ('created_from_survey', 'approved_by_operations', 'approved_at', 'approved_by'):
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_proposal_budget_line_serializer_exposes_override_fields(self):
        proposal = generate_proposal_version(self.lead, self.user)
        line = proposal.budget_lines.first()
        resp = self._api().get(f'/api/sales/proposal-budget-lines/{line.pk}/')
        self.assertEqual(resp.status_code, 200)
        for field in ('role_requirement', 'is_manual_override', 'override_reason', 'overridden_by'):
            self.assertIn(field, resp.data, f"Missing field: {field}")

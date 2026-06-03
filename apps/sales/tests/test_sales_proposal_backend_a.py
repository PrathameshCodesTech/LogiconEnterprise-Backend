"""
apps/sales/tests/test_sales_proposal_backend_a.py

Phase Sales-Proposal-Backend-A focused tests.

Scenarios:
  1.  UniqueConstraint: duplicate (lead, version_number) raises IntegrityError.
  2.  Create SalesLead + SalesLeadSite via API.
  3.  POST submit-to-operations transitions lead stage to submitted_to_operations.
  4.  Submit-to-operations from non-draft stage raises 400.
  5.  Create SiteSurvey, complete survey via service transitions lead stage.
  6.  Create SalesRoleRequirements and retrieve via API.
  7.  POST generate-proposal creates ProposalVersion with status=generated.
  8.  ProposalBudgetLines created from role requirements.
  9.  clone-revision creates version_number+1 and copies budget lines.
  10. Source (sent) proposal is not mutated on clone.
  11. client-response with 'rejected' moves lead to client_rejected.
  12. client-response with 'approved' + mark_lead_won sets final_approved_proposal + stage=won.
  13. Org scoping: cross-org user cannot list or access leads from another org.
  14. Unauthorized user (no capability) gets 403 on lead create.
  15. SalesLeadDetail endpoint returns nested sites, surveys, role_requirements, proposal_versions.
"""

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE, SALES_LEAD_DELETE,
    SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
    SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.wages.models import MinimumWageRate
from apps.sales.models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine,
)
from apps.sales.services import (
    submit_to_operations, complete_site_survey, mark_survey_in_progress,
    generate_proposal_version, clone_proposal_for_revision,
    record_client_response, mark_lead_won_from_client_approval,
    send_proposal_to_client, mark_proposal_internally_approved,
    submit_proposal_for_internal_approval,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules


# ─── URL prefixes ─────────────────────────────────────────────────────────────

LEADS_URL = '/api/sales/leads/'
SITES_URL = '/api/sales/lead-sites/'
SURVEYS_URL = '/api/sales/site-surveys/'
ROLE_REQS_URL = '/api/sales/role-requirements/'
PROPOSALS_URL = '/api/sales/proposal-versions/'


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


def _role(org, code, caps):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    bootstrap_role_permissions(role, caps)
    return role


def _user(username, org, role=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass', is_superuser=is_superuser)
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _job_role(org, name='Security Guard'):
    return JobRole.objects.get_or_create(
        org=org, code=name.lower().replace(' ', '_'),
        defaults={'name': name},
    )[0]


def _lead(org, client_name='Test Client', **kwargs):
    kwargs.setdefault('client_email', 'client@example.com')
    return SalesLead.objects.create(
        org=org, client_name=client_name,
        current_stage='draft', current_status='draft',
        **kwargs
    )


def _internally_approve_for_send(proposal, user):
    proposal.status = 'submitted_internal'
    proposal.internal_approval_status = 'in_progress'
    proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
    mark_proposal_internally_approved(proposal, user)


def _lead_site(lead, site_name='Site A'):
    return SalesLeadSite.objects.create(
        lead=lead, site_name=site_name, city='Mumbai', state='MH',
    )


def _all_sales_caps():
    return [
        SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE, SALES_LEAD_DELETE,
        SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
        SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
    ]


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestProposalVersionConstraint(TestCase):
    """1. UniqueConstraint on (lead, version_number)."""

    def setUp(self):
        self.org = _org('pvc')
        self.lead = _lead(self.org)

    def test_duplicate_version_number_raises(self):
        ProposalVersion.objects.create(
            lead=self.lead, version_number=1, status='draft',
            internal_approval_status='not_started', client_approval_status='not_sent',
            grand_total=0, manpower_total=0, gst_applicable=False,
        )
        with self.assertRaises(IntegrityError):
            ProposalVersion.objects.create(
                lead=self.lead, version_number=1, status='draft',
                internal_approval_status='not_started', client_approval_status='not_sent',
                grand_total=0, manpower_total=0, gst_applicable=False,
            )


class TestSalesLeadCRUD(TestCase):
    """2. Create SalesLead + SalesLeadSite via API."""

    def setUp(self):
        self.org = _org('crud')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sales_mgr', _all_sales_caps())
        self.user = _user('sm', self.org, self.role, self.n)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_create_lead(self):
        resp = self._api().post(
            LEADS_URL,
            {'client_name': 'Acme Corp', 'rfp_required': False},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client_name'], 'Acme Corp')
        self.assertEqual(resp.data['current_stage'], 'draft')

    def test_create_lead_site(self):
        lead = _lead(self.org)
        resp = self._api().post(
            SITES_URL,
            {'lead': lead.pk, 'site_name': 'HQ Site', 'city': 'Delhi', 'state': 'DL'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['site_name'], 'HQ Site')


class TestSubmitToOperations(TestCase):
    """3 & 4. submit-to-operations transitions and guards."""

    def setUp(self):
        self.org = _org('sto')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_sto', self.org, self.role, self.n)
        self.lead = _lead(self.org)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_submit_to_operations_transitions_stage(self):
        resp = self._api().post(f'{LEADS_URL}{self.lead.pk}/submit-to-operations/', format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'submitted_to_operations')
        self.assertEqual(self.lead.current_status, 'active')

    def test_submit_from_non_draft_raises_400(self):
        self.lead.current_stage = 'budget_generated'
        self.lead.save()
        resp = self._api().post(f'{LEADS_URL}{self.lead.pk}/submit-to-operations/', format='json')
        self.assertEqual(resp.status_code, 400)


class TestSurveyFlow(TestCase):
    """5. Create SiteSurvey and complete survey via service."""

    def setUp(self):
        self.org = _org('surv')
        self.lead = _lead(self.org)
        self.site = _lead_site(self.lead)
        self.user = _user('ops', self.org)

    def test_complete_survey_transitions_lead(self):
        submit_to_operations(self.lead, self.user)
        mark_survey_in_progress(self.lead, self.user)
        complete_site_survey(self.lead, self.user)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'site_survey_completed')

    def test_create_site_survey_model(self):
        survey = SiteSurvey.objects.create(
            lead=self.lead, site=self.site,
            feasibility_status='feasible', status='completed',
        )
        self.assertEqual(survey.feasibility_status, 'feasible')

    def test_duplicate_survey_per_site_raises(self):
        SiteSurvey.objects.create(lead=self.lead, site=self.site, status='pending')
        with self.assertRaises(IntegrityError):
            SiteSurvey.objects.create(lead=self.lead, site=self.site, status='completed')


class TestRoleRequirements(TestCase):
    """6. Create SalesRoleRequirements and retrieve via API."""

    def setUp(self):
        self.org = _org('rrq')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_rrq', _all_sales_caps())
        self.user = _user('sm_rrq', self.org, self.role, self.n)
        self.lead = _lead(self.org)
        self.site = _lead_site(self.lead)
        self.job_role = _job_role(self.org)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_create_role_requirement(self):
        resp = self._api().post(
            ROLE_REQS_URL,
            {
                'lead': self.lead.pk,
                'site': self.site.pk,
                'job_role': self.job_role.pk,
                'manpower_count': 5,
                'service_category': 'security',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['manpower_count'], 5)

    def test_list_role_requirements_scoped_to_org(self):
        SalesRoleRequirement.objects.create(
            lead=self.lead, site=self.site, job_role=self.job_role, manpower_count=3,
        )
        resp = self._api().get(ROLE_REQS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)


def _ready_lead_with_rr(org, user, job_role_name='Housekeeping', manpower_count=10):
    """Create a lead with completed survey and role requirement ready for proposal generation."""
    from apps.sales.tests.proposal_wage_fixtures import (
        ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
        wire_site_and_requirement_for_wages,
    )
    lead = _lead(org)
    site = _lead_site(lead)
    job_role = _job_role(org, job_role_name)
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, job_role, monthly_wage=12000)
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=job_role, manpower_count=manpower_count, is_active=True,
    )
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    return lead, site, job_role


class TestGenerateProposal(TestCase):
    """7 & 8. generate-proposal creates ProposalVersion and budget lines."""

    def setUp(self):
        self.org = _org('gp')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_gp', _all_sales_caps())
        self.user = _user('sm_gp', self.org, self.role, self.n)
        self.lead, self.site, self.job_role = _ready_lead_with_rr(self.org, self.user)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_generate_proposal_creates_version(self):
        resp = self._api().post(f'{LEADS_URL}{self.lead.pk}/generate-proposal/', format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'generated')
        self.assertEqual(resp.data['version_number'], 1)

    def test_generate_proposal_creates_budget_lines(self):
        self._api().post(f'{LEADS_URL}{self.lead.pk}/generate-proposal/', format='json')
        proposal = ProposalVersion.objects.get(lead=self.lead)
        self.assertEqual(proposal.budget_lines.count(), 1)
        line = proposal.budget_lines.first()
        self.assertEqual(line.manpower_count, 10)
        self.assertIn('Housekeeping', line.description)  # site - job_role format

    def test_generate_proposal_sets_manpower_total(self):
        self._api().post(f'{LEADS_URL}{self.lead.pk}/generate-proposal/', format='json')
        proposal = ProposalVersion.objects.get(lead=self.lead)
        self.assertEqual(proposal.manpower_total, 10)

    def test_generate_proposal_advances_lead_stage(self):
        self._api().post(f'{LEADS_URL}{self.lead.pk}/generate-proposal/', format='json')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'budget_generated')

    def test_generate_proposal_missing_wage_returns_400_without_shell_version(self):
        MinimumWageRate.objects.filter(org=self.org).delete()
        resp = self._api().post(f'{LEADS_URL}{self.lead.pk}/generate-proposal/', format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('No wage rate configured', resp.data['detail'])
        self.assertFalse(ProposalVersion.objects.filter(lead=self.lead).exists())

    def test_second_generate_increments_version_number(self):
        generate_proposal_version(self.lead, self.user)
        proposal2 = generate_proposal_version(self.lead, self.user)
        self.assertEqual(proposal2.version_number, 2)


class TestCloneRevision(TestCase):
    """9 & 10. clone-revision creates new version; source proposal not mutated."""

    def setUp(self):
        self.org = _org('cr')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_cr', _all_sales_caps())
        self.user = _user('sm_cr', self.org, self.role, self.n)
        self.lead, self.site, self.job_role = _ready_lead_with_rr(self.org, self.user)
        self.proposal = generate_proposal_version(self.lead, self.user)
        # Advance to sent_to_client so we can test source is not mutated
        self.proposal.status = 'sent_to_client'
        self.proposal.save()

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_clone_creates_next_version_number(self):
        resp = self._api().post(f'{PROPOSALS_URL}{self.proposal.pk}/clone-revision/', format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['version_number'], 2)

    def test_clone_copies_budget_lines(self):
        clone = clone_proposal_for_revision(self.proposal, self.user)
        self.assertEqual(clone.budget_lines.count(), self.proposal.budget_lines.count())

    def test_clone_sets_source_version(self):
        clone = clone_proposal_for_revision(self.proposal, self.user)
        self.assertEqual(clone.source_version_id, self.proposal.pk)

    def test_clone_sets_status_to_draft(self):
        clone = clone_proposal_for_revision(self.proposal, self.user)
        self.assertEqual(clone.status, 'draft')

    def test_source_proposal_not_mutated(self):
        clone_proposal_for_revision(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, 'sent_to_client')


class TestClientResponseFlow(TestCase):
    """11 & 12. Client response transitions and mark_lead_won."""

    def setUp(self):
        self.org = _org('crf')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_crf', _all_sales_caps())
        self.user = _user('sm_crf', self.org, self.role, self.n)
        self.lead, self.site, self.job_role = _ready_lead_with_rr(self.org, self.user, manpower_count=3)
        self.proposal = generate_proposal_version(self.lead, self.user)
        from apps.workflow.tests.helpers import bootstrap_legacy_workflow
        # Walk through the full approval chain
        _internally_approve_for_send(self.proposal, self.user)
        send_proposal_to_client(self.proposal, self.user)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_client_rejected_moves_lead_to_rejected_stage(self):
        resp = self._api().post(
            f'{PROPOSALS_URL}{self.proposal.pk}/client-response/',
            {'client_response': 'rejected', 'remarks': 'Price too high'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'client_rejected')
        self.assertEqual(self.lead.current_status, 'rejected')

    def test_client_approved_marks_lead_stage(self):
        resp = self._api().post(
            f'{PROPOSALS_URL}{self.proposal.pk}/client-response/',
            {'client_response': 'approved', 'remarks': 'Looks good'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'client_approved')

    def test_mark_lead_won_sets_final_proposal_and_stage(self):
        record_client_response(self.proposal, 'approved', 'Great', self.user)
        mark_lead_won_from_client_approval(self.lead, self.proposal, self.user)
        self.lead.refresh_from_db()
        self.proposal.refresh_from_db()
        self.assertEqual(self.lead.current_stage, 'won')
        self.assertEqual(self.lead.current_status, 'won')
        self.assertEqual(self.lead.final_approved_proposal_id, self.proposal.pk)
        self.assertEqual(self.proposal.status, 'locked')
        self.assertTrue(self.proposal.is_final_approved_version)

    def test_mark_won_without_approval_raises(self):
        with self.assertRaises(ValueError):
            mark_lead_won_from_client_approval(self.lead, self.proposal, self.user)


class TestOrgScoping(TestCase):
    """13. Org scoping prevents cross-org access."""

    def setUp(self):
        self.org_a = _org('scopeA')
        self.org_b = _org('scopeB')
        self.n_a = _scope_node(self.org_a)
        self.n_b = _scope_node(self.org_b)
        caps = _all_sales_caps()
        self.role_a = _role(self.org_a, 'sm_a', caps)
        self.role_b = _role(self.org_b, 'sm_b', caps)
        self.user_a = _user('user_a', self.org_a, self.role_a, self.n_a)
        self.user_b = _user('user_b', self.org_b, self.role_b, self.n_b)
        _lead(self.org_a, 'Lead A')
        _lead(self.org_b, 'Lead B')

    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_user_sees_only_own_org_leads(self):
        resp_a = self._api(self.user_a).get(LEADS_URL)
        self.assertEqual(resp_a.status_code, 200)
        names = [l['client_name'] for l in resp_a.data['results']]
        self.assertIn('Lead A', names)
        self.assertNotIn('Lead B', names)

    def test_cross_org_lead_retrieve_returns_404(self):
        lead_b = SalesLead.objects.get(org=self.org_b)
        resp = self._api(self.user_a).get(f'{LEADS_URL}{lead_b.pk}/')
        self.assertEqual(resp.status_code, 404)


class TestPermissions(TestCase):
    """14. Unauthorized user gets 403 on lead create."""

    def setUp(self):
        self.org = _org('perm')
        self.n = _scope_node(self.org)
        # Role with only READ capability — no CREATE
        self.role = _role(self.org, 'readonly', [SALES_LEAD_READ])
        self.user = _user('ro', self.org, self.role, self.n)

    def test_no_create_cap_returns_403(self):
        c = APIClient()
        c.force_authenticate(self.user)
        resp = c.post(
            LEADS_URL,
            {'client_name': 'Blocked Lead'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)


class TestLeadDetailEndpoint(TestCase):
    """15. SalesLeadDetail endpoint returns nested resources."""

    def setUp(self):
        self.org = _org('det')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_det', _all_sales_caps())
        self.user = _user('sm_det', self.org, self.role, self.n)
        self.lead = _lead(self.org, 'Detail Client')
        self.site = SalesLeadSite.objects.create(
            lead=self.lead, site_name='Site X', city='Pune', state='MH',
        )
        self.job_role = _job_role(self.org, 'Electrician')
        from apps.sales.tests.proposal_wage_fixtures import (
            ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
            wire_site_and_requirement_for_wages,
        )
        wage_cat = ensure_wage_category()
        location = ensure_location_area_mumbai()
        ensure_minimum_wage(self.org, location, wage_cat, self.job_role, monthly_wage=18000)
        rr = SalesRoleRequirement.objects.create(
            lead=self.lead, site=self.site, job_role=self.job_role, manpower_count=2, is_active=True,
        )
        wire_site_and_requirement_for_wages(self.site, rr, location, wage_cat)
        submit_to_operations(self.lead, self.user)
        SiteSurvey.objects.filter(lead=self.lead).update(status='completed')
        self.lead.current_stage = 'site_survey_completed'
        self.lead.save(update_fields=['current_stage'])
        self.proposal = generate_proposal_version(self.lead, self.user)

    def _api(self):
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_detail_includes_sites(self):
        resp = self._api().get(f'{LEADS_URL}{self.lead.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('sites', resp.data)
        self.assertEqual(len(resp.data['sites']), 1)
        self.assertEqual(resp.data['sites'][0]['site_name'], 'Site X')

    def test_detail_includes_surveys(self):
        resp = self._api().get(f'{LEADS_URL}{self.lead.pk}/')
        self.assertIn('surveys', resp.data)
        self.assertEqual(len(resp.data['surveys']), 1)

    def test_detail_includes_role_requirements(self):
        resp = self._api().get(f'{LEADS_URL}{self.lead.pk}/')
        self.assertIn('role_requirements', resp.data)
        self.assertEqual(len(resp.data['role_requirements']), 1)

    def test_detail_includes_proposal_versions(self):
        resp = self._api().get(f'{LEADS_URL}{self.lead.pk}/')
        self.assertIn('proposal_versions', resp.data)
        self.assertEqual(len(resp.data['proposal_versions']), 1)
        self.assertEqual(resp.data['proposal_versions'][0]['version_number'], 1)

    def test_proposal_detail_includes_budget_lines(self):
        resp = self._api().get(f'{PROPOSALS_URL}{self.proposal.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('budget_lines', resp.data)
        self.assertEqual(len(resp.data['budget_lines']), 1)

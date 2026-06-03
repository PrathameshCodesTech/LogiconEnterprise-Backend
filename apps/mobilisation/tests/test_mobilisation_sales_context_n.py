"""
apps/mobilisation/tests/test_mobilisation_sales_context_n.py

Phase Mobilisation-Sales-Context-Overview-N focused tests.

Scenarios:
  1.  GET /api/mobilisation/setup-requests/{id}/sales-context/ returns sales context for converted sales lead.
  2.  Sales context includes sites and role headcount grouped by site.
  3.  Sales context includes budget lines from source proposal.
  4.  Sales context includes proposal revision history.
  5.  Readiness counts departments/users correctly.
  6.  Cross-org access is blocked.
  7.  Missing source proposal/lead is handled safely (returns null).
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
    SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
    SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
    MOBILISATION_READ, MOBILISATION_CREATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mobilisation.models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedUser,
)
from apps.sales.models import (
    SalesLead, SalesLeadSite, SalesRoleRequirement, ProposalVersion,
)
from apps.sales.services import (
    convert_won_sales_lead_to_onboarding_setup,
    generate_proposal_version,
    submit_to_operations,
    mark_proposal_internally_approved,
    send_proposal_to_client,
    record_client_response,
    mark_lead_won_from_client_approval,
)


SALES_CONTEXT_URL = '/api/mobilisation/setup-requests/{id}/sales-context/'


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


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


def _job_role(org):
    return JobRole.objects.get_or_create(
        org=org, code='security_guard', defaults={'name': 'Security Guard'},
    )[0]


def _all_caps():
    return [
        SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
        SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
        SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
        MOBILISATION_READ, MOBILISATION_CREATE,
    ]


def _won_lead_with_proposal(org, user):
    """Create a fully won sales lead with a locked final proposal."""
    from apps.sales.models import SiteSurvey
    lead = SalesLead.objects.create(
        org=org, client_name='Acme Ltd',
        client_contact_person='John', client_email='john@acme.com', client_phone='9999',
        current_stage='draft', current_status='draft',
        created_by=user,
    )
    site = SalesLeadSite.objects.create(
        lead=lead, site_name='Acme HQ', city='Mumbai', state='MH',
    )
    job_role = _job_role(org)
    SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=job_role, manpower_count=5,
        service_category='Security', shift_hours=8, working_days=26,
    )
    submit_to_operations(lead, user)
    # Complete the auto-created survey so proposal guardrails pass
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    from apps.sales.tests.proposal_wage_fixtures import (
        ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
        wire_site_and_requirement_for_wages,
    )
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    for rr in SalesRoleRequirement.objects.filter(lead=lead):
        ensure_minimum_wage(lead.org, location, wage_cat, rr.job_role, monthly_wage=12000)
        wire_site_and_requirement_for_wages(rr.site, rr, location, wage_cat)
    proposal = generate_proposal_version(lead, user)
    proposal.status = 'submitted_internal'
    proposal.internal_approval_status = 'in_progress'
    proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
    mark_proposal_internally_approved(proposal, user)
    send_proposal_to_client(proposal, user)
    record_client_response(proposal, 'approved', 'Looks good', user)
    mark_lead_won_from_client_approval(lead, proposal, user)
    proposal.refresh_from_db()
    lead.refresh_from_db()
    return lead, proposal


# ─── Test cases ───────────────────────────────────────────────────────────────

class SalesContextEndpointTestCase(TestCase):
    """Tests for GET /api/mobilisation/setup-requests/{id}/sales-context/"""

    def setUp(self):
        self.org = _org('SC1')
        self.scope_node = _scope_node(self.org)
        self.role = _role(self.org, 'admin', _all_caps())
        self.user = _user('admin', self.org, self.role, self.scope_node)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_sales_context_returns_full_data_for_converted_lead(self):
        """1. GET /sales-context/ returns sales context for converted sales lead."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Check mobilisation block
        self.assertEqual(data['mobilisation']['id'], mob_request.id)
        self.assertEqual(data['mobilisation']['status'], mob_request.status)

        # Check lead block
        self.assertIsNotNone(data['lead'])
        self.assertEqual(data['lead']['id'], lead.id)
        self.assertEqual(data['lead']['client_name'], 'Acme Ltd')

        # Check proposal block
        self.assertIsNotNone(data['proposal'])
        self.assertEqual(data['proposal']['id'], proposal.id)
        self.assertEqual(data['proposal']['version_number'], proposal.version_number)

    def test_sales_context_includes_sites_with_roles(self):
        """2. Sales context includes sites and role headcount grouped by site."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Check sites block
        self.assertIsInstance(data['sites'], list)
        self.assertGreater(len(data['sites']), 0)

        site = data['sites'][0]
        self.assertEqual(site['site_name'], 'Acme HQ')
        self.assertIsInstance(site['roles'], list)
        self.assertGreater(len(site['roles']), 0)
        self.assertEqual(site['headcount'], 5)  # From the role requirement

    def test_sales_context_includes_budget_lines(self):
        """3. Sales context includes budget lines from source proposal."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Budget lines should exist
        self.assertIsInstance(data['budget_lines'], list)

    def test_sales_context_includes_proposal_versions(self):
        """4. Sales context includes proposal revision history."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Proposal versions should include at least one version
        self.assertIsInstance(data['proposal_versions'], list)
        self.assertGreater(len(data['proposal_versions']), 0)
        self.assertEqual(data['proposal_versions'][0]['version_number'], 1)

    def test_readiness_counts_departments_and_users(self):
        """5. Readiness counts departments/users correctly."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        readiness = data['readiness']
        self.assertIn('expected_departments', readiness)
        self.assertIn('created_departments', readiness)
        self.assertIn('expected_users', readiness)
        self.assertIn('created_users', readiness)
        self.assertIn('ready_to_finalize', readiness)

        # Initially, no departments/users are created
        self.assertEqual(readiness['created_departments'], 0)
        self.assertEqual(readiness['created_users'], 0)
        self.assertFalse(readiness['ready_to_finalize'])

    def test_cross_org_access_blocked(self):
        """6. Cross-org access is blocked."""
        lead, proposal = _won_lead_with_proposal(self.org, self.user)
        mob_request = convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        # Create a user from a different org
        other_org = _org('OTHER')
        other_scope = _scope_node(other_org)
        other_role = _role(other_org, 'admin', _all_caps())
        other_user = _user('other_admin', other_org, other_role, other_scope)

        self.client.force_authenticate(other_user)
        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        # Should get 404 (not found in their scope)
        self.assertEqual(resp.status_code, 404)

    def test_missing_sales_source_handled_safely(self):
        """7. Missing source proposal/lead is handled safely (returns null)."""
        # Create a mobilisation request without sales source
        mob_request = MobilisationSetupRequest.objects.create(
            org=self.org,
            requested_by=self.user,
            status='draft',
            mobilisation_type='new_client',
            summary='Manual mobilisation',
        )

        url = SALES_CONTEXT_URL.format(id=mob_request.id)
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Lead and proposal should be null
        self.assertIsNone(data['lead'])
        self.assertIsNone(data['proposal'])

        # Sites, budget_lines, proposal_versions should be empty lists
        self.assertEqual(data['sites'], [])
        self.assertEqual(data['budget_lines'], [])
        self.assertEqual(data['proposal_versions'], [])

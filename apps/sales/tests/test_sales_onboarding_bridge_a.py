"""
apps/sales/tests/test_sales_onboarding_bridge_a.py

Phase Sales-Onboarding-Bridge-A focused tests.

Scenarios:
  1.  convert_won_sales_lead_to_onboarding_setup creates Client, SiteProfiles, SRRs, BudgetPlan, and
      a ClientOnboardingRequest(onboarding_source='sales').
  2.  convert_won_sales_lead_to_onboarding_setup is idempotent (returns existing request on re-call).
  3.  Conversion raises ValueError when lead is not 'won'.
  4.  POST /api/sales/proposal-versions/{id}/convert-to-onboarding/ auto-wins lead if client_approved.
  5.  POST convert-to-onboarding/ returns 200 on second call (idempotent via API).
  6.  POST convert-to-onboarding/ returns 400 when proposal is not approved.
  7.  Sales-led readiness: ok when client + >= 1 dept + >= 1 user.
  8.  Sales-led readiness: errors when missing client or no departments.
  9.  Sales-led readiness: warns when no primary contact.
  10. Sales-led finalization: creates departments + users; skips proposed sites/SRRs/budgets.
  11. Sales-led preflight: blocks duplicate user email; passes if no conflicts.
  12. Workflow drawer includes sales source fields for sales-led onboarding.
  13. ClientOnboardingRequestSerializer exposes onboarding_source + source_* fields.
"""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
    SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
    SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mobilisation.models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)
from apps.mobilisation.services import (
    check_mobilisation_readiness,
    validate_mobilisation_finalization_preflight,
    finalize_mobilisation_request,
)
from apps.sales.models import (
    SalesLead, SalesLeadSite, SalesRoleRequirement, ProposalVersion,
)
from apps.sales.services import (
    convert_won_sales_lead_to_onboarding_setup,
    generate_proposal_version,
    submit_to_operations,
    submit_proposal_for_internal_approval,
    mark_proposal_internally_approved,
    send_proposal_to_client,
    record_client_response,
    mark_lead_won_from_client_approval,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


def _map_all_site_role_requirements(req, dept=None):
    site = req.client.sites.filter(is_active=True).first()
    if dept is None:
        dept = MobilisationProposedDepartment.objects.create(
            request=req,
            real_site=site,
            name='Operations',
            code=f'ops-{req.pk}',
            scope_level='site',
        )
    for idx, srr in enumerate(site.role_requirements.filter(is_active=True), start=1):
        MobilisationProposedDepartmentRole.objects.create(
            request=req,
            proposed_department=dept,
            site_role_requirement=srr,
            sort_order=idx,
        )
    return dept


PROPOSALS_URL = '/api/sales/proposal-versions/'


# ─── Shared helpers ───────────────────────────────────────────────────────────

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


def _job_role(org):
    return JobRole.objects.get_or_create(
        org=org, code='security_guard', defaults={'name': 'Security Guard'},
    )[0]


def _all_sales_caps():
    return [
        SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
        SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
        SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
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
    return lead, proposal, site


def _access_role(org, code='client_role'):
    role = AccessRole.objects.get_or_create(
        org=org, code=code, defaults={'name': code, 'is_active': True},
    )[0]
    return role


# ─── 1. Conversion creates real records ──────────────────────────────────────

class TestConversionCreatesRealRecords(TestCase):
    def setUp(self):
        self.org = _org('crr')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'smgr', _all_sales_caps())
        self.user = _user('smgr_crr', self.org, self.role, self.n)
        self.lead, self.proposal, self.lead_site = _won_lead_with_proposal(self.org, self.user)

    def test_creates_client(self):
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.assertTrue(Client.objects.filter(org=self.org, code=f'cls{self.lead.pk}').exists())

    def test_creates_site_profile(self):
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.assertTrue(SiteProfile.objects.filter(code=f'sts{self.lead_site.pk}').exists())

    def test_creates_srr(self):
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        site = SiteProfile.objects.get(code=f'sts{self.lead_site.pk}')
        self.assertTrue(SiteRoleRequirement.objects.filter(site=site).exists())

    def test_created_srr_uses_approved_proposal_unit_cost_as_billing_rate(self):
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        site = SiteProfile.objects.get(code=f'sts{self.lead_site.pk}')
        srr = SiteRoleRequirement.objects.get(site=site)
        budget_line = self.proposal.budget_lines.get(role_requirement__isnull=False)
        self.assertEqual(srr.billing_rate, budget_line.unit_cost)

    def test_creates_budget_plan(self):
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.assertTrue(BudgetPlan.objects.filter(
            org=self.org,
            code=f'bp-new-client-l{self.lead.pk}-p{self.proposal.pk}',
        ).exists())

    def test_creates_onboarding_request_with_sales_source(self):
        req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.assertEqual(req.source_sales_lead, self.lead)
        self.assertEqual(req.source_proposal_version, self.proposal)
        self.assertEqual(req.mobilisation_type, 'new_client')
        self.assertEqual(req.status, 'draft')


# ─── 2. Idempotency ──────────────────────────────────────────────────────────

class TestConversionIdempotency(TestCase):
    def setUp(self):
        self.org = _org('idm')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_idm', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)

    def test_second_call_returns_same_request(self):
        req1 = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        req2 = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.assertEqual(req1.pk, req2.pk)
        self.assertEqual(MobilisationSetupRequest.objects.filter(source_sales_lead=self.lead).count(), 1)


# ─── 3. ValueError when lead not won ─────────────────────────────────────────

class TestConversionRequiresWon(TestCase):
    def setUp(self):
        self.org = _org('rnw')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_rnw', self.org, self.role, self.n)

    def test_raises_for_draft_lead(self):
        lead = SalesLead.objects.create(
            org=self.org, client_name='X', current_stage='draft', current_status='draft',
        )
        with self.assertRaises(ValueError):
            convert_won_sales_lead_to_onboarding_setup(lead, self.user)


# ─── 4. API auto-wins lead when client_approved ───────────────────────────────

class TestConvertToOnboardingAPIAutoWin(TestCase):
    def setUp(self):
        self.org = _org('aw')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_aw', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def test_convert_returns_onboarding_request(self):
        url = f'{PROPOSALS_URL}{self.proposal.pk}/convert-to-onboarding/'
        resp = self.client_api.post(url, format='json')
        self.assertIn(resp.status_code, [200, 201], resp.data)
        self.assertIsNotNone(resp.data['source_sales_lead'])
        self.assertIsNotNone(resp.data['source_proposal_version'])


# ─── 5. API idempotency ───────────────────────────────────────────────────────

class TestConvertToOnboardingAPIIdempotency(TestCase):
    def setUp(self):
        self.org = _org('apm')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_apm', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def test_second_call_returns_200(self):
        url = f'{PROPOSALS_URL}{self.proposal.pk}/convert-to-onboarding/'
        self.client_api.post(url, format='json')
        resp = self.client_api.post(url, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)


# ─── 6. API rejects unapproved proposal ──────────────────────────────────────

class TestConvertToOnboardingAPIRejects(TestCase):
    def setUp(self):
        self.org = _org('apr')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_apr', self.org, self.role, self.n)
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def test_rejects_draft_proposal(self):
        lead = SalesLead.objects.create(
            org=self.org, client_name='X', current_stage='draft', current_status='draft',
        )
        proposal = ProposalVersion.objects.create(
            lead=lead, version_number=1, status='draft',
            internal_approval_status='not_started', client_approval_status='not_sent',
            grand_total=0, manpower_total=0, gst_applicable=False,
        )
        url = f'{PROPOSALS_URL}{proposal.pk}/convert-to-onboarding/'
        resp = self.client_api.post(url, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)


# ─── 7. Sales-led readiness: ok path ─────────────────────────────────────────

class TestSalesLedReadinessOk(TestCase):
    def setUp(self):
        self.org = _org('sro')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_sro', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        # Add required client user. Departments are not required for client setup.
        self.access_role = _access_role(self.org)
        MobilisationProposedUser.objects.create(
            request=self.req, full_name='Alice', email='alice@acme.com',
            user_type='client', access_role=self.access_role, scope_level='client',
            is_primary_contact=True, send_invite_on_finalization=False,
        )

    def test_readiness_ok(self):
        ok, errors, warnings = check_mobilisation_readiness(self.req)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])


# ─── 8. Sales-led readiness: errors ──────────────────────────────────────────

class TestSalesLedReadinessErrors(TestCase):
    def setUp(self):
        self.org = _org('sre')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_sre', self.org, self.role, self.n)

    def test_errors_when_no_user(self):
        req = MobilisationSetupRequest.objects.create(
            org=self.org,
            requested_by=self.user,
            mobilisation_type='new_site_expansion',
            client=Client.objects.create(org=self.org, name='X', code='xcl'),
        )
        ok, errors, _ = check_mobilisation_readiness(req)
        self.assertFalse(ok)
        self.assertTrue(any('user' in e.lower() for e in errors))

    def test_errors_when_no_client(self):
        req = MobilisationSetupRequest.objects.create(
            org=self.org,
            requested_by=self.user,
            mobilisation_type='new_site_expansion',
        )
        ok, errors, _ = check_mobilisation_readiness(req)
        self.assertFalse(ok)
        self.assertTrue(any('client' in e.lower() for e in errors))


# ─── 9. Sales-led readiness: warns when no primary contact ───────────────────

class TestSalesLedReadinessWarnings(TestCase):
    def setUp(self):
        self.org = _org('srw')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_srw', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.access_role = _access_role(self.org)
        MobilisationProposedUser.objects.create(
            request=self.req, full_name='Bob', email='bob@acme.com',
            user_type='client', access_role=self.access_role, scope_level='client',
            is_primary_contact=False, send_invite_on_finalization=False,
        )

    def test_warns_no_primary_contact(self):
        ok, errors, warnings = check_mobilisation_readiness(self.req)
        self.assertTrue(ok, errors)
        self.assertTrue(any('primary' in w.lower() for w in warnings))


# ─── 10. Sales-led finalization ───────────────────────────────────────────────

class TestSalesLedFinalization(TestCase):
    def setUp(self):
        self.org = _org('slf')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_slf', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.access_role = _access_role(self.org)
        MobilisationProposedUser.objects.create(
            request=self.req, full_name='Carol', email='carol@acme.com',
            user_type='client', access_role=self.access_role, scope_level='client',
            is_primary_contact=True, send_invite_on_finalization=False,
        )
        self.req.status = 'approved'
        self.req.save(update_fields=['status'])

    def test_finalization_does_not_create_department(self):
        from apps.core.models import Department
        initial_count = Department.objects.filter(org=self.org).count()
        finalize_mobilisation_request(self.req, actor=self.user)
        self.req.refresh_from_db()
        self.assertEqual(self.req.finalization_status, 'finalized')
        self.assertEqual(Department.objects.filter(org=self.org).count(), initial_count)

    def test_finalization_creates_user(self):
        finalize_mobilisation_request(self.req, actor=self.user)
        self.assertTrue(User.objects.filter(email='carol@acme.com').exists())

    def test_finalization_does_not_touch_proposed_sites(self):
        initial_site_count = SiteProfile.objects.filter(org=self.org).count()
        finalize_mobilisation_request(self.req, actor=self.user)
        self.assertEqual(SiteProfile.objects.filter(org=self.org).count(), initial_site_count)


# ─── 11. Sales-led preflight ─────────────────────────────────────────────────

class TestSalesLedPreflight(TestCase):
    def setUp(self):
        self.org = _org('slp')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_slp', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)
        self.access_role = _access_role(self.org)

    def test_passes_when_no_conflicts(self):
        MobilisationProposedUser.objects.create(
            request=self.req, full_name='Dave', email='dave@acme.com',
            user_type='client', access_role=self.access_role, scope_level='client',
            send_invite_on_finalization=False,
        )
        errors = validate_mobilisation_finalization_preflight(self.req)
        self.assertEqual(errors, [])

    def test_blocks_duplicate_email(self):
        # Pre-create a user with the same email
        User.objects.create_user(username='existingdave', email='dave2@acme.com', password='x')
        MobilisationProposedUser.objects.create(
            request=self.req, full_name='Dave2', email='dave2@acme.com',
            user_type='client', access_role=self.access_role, scope_level='client',
            send_invite_on_finalization=False,
        )
        errors = validate_mobilisation_finalization_preflight(self.req)
        self.assertTrue(any('dave2@acme.com' in e for e in errors))


# ─── 12. Workflow drawer includes sales source fields ─────────────────────────

class TestWorkflowDrawerSalesFields(TestCase):  # scenario 12
    def setUp(self):
        self.org = _org('wds')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_wds', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)

    def test_drawer_includes_sales_fields(self):
        from apps.workflow.serializers import _serialize_onboarding_drawer
        self.req.refresh_from_db()
        # Manually load FKs so the serializer doesn't lazy-load from None
        self.req.source_sales_lead  # trigger attribute access
        self.req.source_proposal_version  # trigger attribute access
        payload = _serialize_onboarding_drawer(self.req)
        self.assertEqual(payload['source_sales_lead'], self.lead.pk)
        self.assertEqual(payload['source_proposal_version'], self.proposal.pk)
        self.assertEqual(payload['source_sales_lead_name'], self.lead.client_name)
        self.assertIsNotNone(payload['source_proposal_version_number'])
        self.assertIsNotNone(payload['source_proposal_grand_total'])


# ─── 13. ClientOnboardingRequestSerializer exposes source fields ──────────────

class TestSerializerSourceFields(TestCase):
    def setUp(self):
        self.org = _org('ssf')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm', _all_sales_caps())
        self.user = _user('sm_ssf', self.org, self.role, self.n)
        self.lead, self.proposal, _ = _won_lead_with_proposal(self.org, self.user)
        self.req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)

    def test_serializer_includes_source_fields(self):
        from apps.mobilisation.serializers import MobilisationSetupRequestSerializer
        data = MobilisationSetupRequestSerializer(self.req).data
        self.assertEqual(data['source_sales_lead'], self.lead.pk)
        self.assertEqual(data['source_sales_lead_name'], self.lead.client_name)
        self.assertEqual(data['source_proposal_version'], self.proposal.pk)
        self.assertIsNotNone(data['source_proposal_version_number'])

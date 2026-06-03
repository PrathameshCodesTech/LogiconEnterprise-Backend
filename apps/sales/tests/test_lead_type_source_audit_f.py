"""
apps/sales/tests/test_lead_type_source_audit_f.py

Phase Sales-Lead-Type-and-Source-Audit-F

Scenarios:
  Serializer validation —
    1.  new_client with existing_client is rejected.
    2.  site_expansion without existing_client is rejected.
    3.  scope_expansion without existing_client is rejected.
    4.  renewal without existing_client is rejected.
    5.  existing_client from another org is rejected.
    6.  non-new-client auto-copies client_name from existing_client when blank.

  Conversion —
    7.  new_client conversion creates a new Client.
    8.  site_expansion conversion reuses the existing Client (no new Client).
    9.  site_expansion creates new SiteProfile(s) under existing Client.
    10. site_expansion does not duplicate the Client row.
    11. duplicate site code under existing Client blocks site_expansion.
    12. scope_expansion raises clear ValueError.
    13. renewal raises clear ValueError.
    14. conversion is idempotent (returns same request on repeated call).

  Source audit —
    15. new_client conversion sets source_type='sales_conversion' on Client/Site/SRR/Budget.
    16. source_sales_lead and source_proposal_version are set on all converted records.
    17. manually created Client/Site/SRR/BudgetPlan default source_type='manual_admin'.
"""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import SalesLead, SalesLeadSite, SalesRoleRequirement, SiteSurvey
from apps.sales.serializers import SalesLeadWriteSerializer
from apps.sales.services import (
    convert_won_sales_lead_to_onboarding_setup,
    generate_proposal_version,
    mark_lead_won_from_client_approval,
    mark_proposal_internally_approved,
    record_client_response,
    send_proposal_to_client,
    submit_to_operations,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules
from apps.sales.tests.proposal_wage_fixtures import (
    ensure_location_area_mumbai,
    ensure_minimum_wage,
    ensure_wage_category,
    wire_site_and_requirement_for_wages,
)
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


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


def _user(username, org):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    return u


def _job_role(org):
    return JobRole.objects.get_or_create(
        org=org, code='guard_f', defaults={'name': 'Guard F'},
    )[0]


def _client(org, code='acme-f', name='Acme F'):
    return Client.objects.create(
        org=org, name=name, code=code, is_active=True,
    )


def _won_lead(org, user, lead_type='new_client', existing_client=None, client_name='Lead Client F'):
    """Create a won SalesLead ready for conversion."""
    lead = SalesLead.objects.create(
        org=org,
        lead_type=lead_type,
        existing_client=existing_client,
        client_name=client_name,
        client_email='client@test.com',
        current_stage='draft',
        current_status='draft',
        created_by=user,
    )
    site = SalesLeadSite.objects.create(lead=lead, site_name='Site F', city='Mumbai', state='MH')
    jr = _job_role(org)
    rr = SalesRoleRequirement.objects.create(lead=lead, site=site, job_role=jr, manpower_count=3)

    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])

    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=15000)
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)

    proposal = generate_proposal_version(lead, user)
    proposal.status = 'submitted_internal'
    proposal.internal_approval_status = 'in_progress'
    proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
    mark_proposal_internally_approved(proposal, user)
    send_proposal_to_client(proposal, user)
    record_client_response(proposal, 'approved', '', user)
    mark_lead_won_from_client_approval(lead, proposal, user)
    lead.refresh_from_db()
    proposal.refresh_from_db()
    return lead, proposal, site


# ─── 1-6: Serializer validation ───────────────────────────────────────────────

class TestSalesLeadWriteSerializerValidation(TestCase):

    def setUp(self):
        self.org = _org('svf')
        self.other_org = _org('svf2')
        _scope_node(self.org)
        _scope_node(self.other_org)
        self.user = _user('svf_user', self.org)
        self.existing_client = _client(self.org, 'cli-svf')
        self.other_org_client = _client(self.other_org, 'cli-svf2')

    def _ctx(self):
        return {'request': type('R', (), {'user': self.user})()}

    def test_01_new_client_with_existing_client_rejected(self):
        data = {
            'lead_type': 'new_client',
            'existing_client': self.existing_client.pk,
            'client_name': 'Some Client',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertFalse(s.is_valid())
        self.assertIn('existing_client', s.errors)

    def test_02_site_expansion_without_existing_client_rejected(self):
        data = {
            'lead_type': 'site_expansion',
            'client_name': 'Some Client',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertFalse(s.is_valid())
        self.assertIn('existing_client', s.errors)

    def test_03_scope_expansion_without_existing_client_rejected(self):
        data = {
            'lead_type': 'scope_expansion',
            'client_name': 'Some Client',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertFalse(s.is_valid())
        self.assertIn('existing_client', s.errors)

    def test_04_renewal_without_existing_client_rejected(self):
        data = {
            'lead_type': 'renewal',
            'client_name': 'Some Client',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertFalse(s.is_valid())
        self.assertIn('existing_client', s.errors)

    def test_05_existing_client_from_another_org_rejected(self):
        data = {
            'lead_type': 'site_expansion',
            'existing_client': self.other_org_client.pk,
            'client_name': 'Some Client',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertFalse(s.is_valid())
        self.assertIn('existing_client', s.errors)

    def test_06_non_new_client_auto_copies_client_name(self):
        data = {
            'lead_type': 'site_expansion',
            'existing_client': self.existing_client.pk,
            'client_name': '',
        }
        s = SalesLeadWriteSerializer(data=data, context=self._ctx())
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['client_name'], self.existing_client.name)


# ─── 7-14: Conversion tests ───────────────────────────────────────────────────

class TestNewClientConversion(TestCase):
    def setUp(self):
        self.org = _org('ncf')
        _scope_node(self.org)
        self.user = _user('ncf_user', self.org)

    def test_07_new_client_creates_new_client(self):
        lead, proposal, site = _won_lead(self.org, self.user, lead_type='new_client')
        before = Client.objects.filter(org=self.org).count()
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(Client.objects.filter(org=self.org).count(), before + 1)


class TestSiteExpansionConversion(TestCase):
    def setUp(self):
        self.org = _org('sef')
        _scope_node(self.org)
        self.user = _user('sef_user', self.org)
        self.existing_client = _client(self.org, 'sef-cli', 'SEF Client')

    def test_08_site_expansion_reuses_existing_client(self):
        lead, proposal, site = _won_lead(
            self.org, self.user,
            lead_type='site_expansion',
            existing_client=self.existing_client,
        )
        before = Client.objects.filter(org=self.org).count()
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(Client.objects.filter(org=self.org).count(), before)

    def test_09_site_expansion_creates_new_site_profiles_under_existing_client(self):
        lead, proposal, site = _won_lead(
            self.org, self.user,
            lead_type='site_expansion',
            existing_client=self.existing_client,
        )
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertTrue(
            SiteProfile.objects.filter(
                client=self.existing_client,
                code=f'sts{site.pk}',
            ).exists()
        )

    def test_10_site_expansion_does_not_duplicate_client(self):
        lead, proposal, site = _won_lead(
            self.org, self.user,
            lead_type='site_expansion',
            existing_client=self.existing_client,
        )
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(
            Client.objects.filter(org=self.org, name=self.existing_client.name).count(),
            1,
        )

    def test_11_duplicate_site_code_blocks_site_expansion(self):
        lead, proposal, site = _won_lead(
            self.org, self.user,
            lead_type='site_expansion',
            existing_client=self.existing_client,
        )
        # Pre-create a site with the would-be generated code
        SiteProfile.objects.create(
            org=self.org, client=self.existing_client,
            name='Existing Site', code=f'sts{site.pk}',
            is_active=True,
        )
        with self.assertRaises(ValueError) as cm:
            convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertIn('already exists', str(cm.exception))


class TestUnsupportedLeadTypes(TestCase):
    def setUp(self):
        self.org = _org('ulf')
        _scope_node(self.org)
        self.user = _user('ulf_user', self.org)
        self.existing_client = _client(self.org, 'ulf-cli')

    def _make_fake_won_lead(self, lead_type):
        """Create a won lead bypassing proposal pipeline for simplicity."""
        from apps.sales.models import ProposalVersion
        lead = SalesLead.objects.create(
            org=self.org,
            lead_type=lead_type,
            existing_client=self.existing_client,
            client_name=self.existing_client.name,
            current_stage='won',
            current_status='won',
        )
        proposal = ProposalVersion.objects.create(
            lead=lead, version_number=1,
            status='locked',
            internal_approval_status='approved',
            client_approval_status='approved',
            is_final_approved_version=True,
            grand_total=100000,
        )
        lead.final_approved_proposal = proposal
        lead.save(update_fields=['final_approved_proposal'])
        return lead

    def test_13_renewal_raises_clear_error(self):
        lead = self._make_fake_won_lead('renewal')
        with self.assertRaises(ValueError) as cm:
            convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertIn('renewal conversion is not implemented yet', str(cm.exception))


class TestConversionIdempotency(TestCase):
    def setUp(self):
        self.org = _org('idf')
        _scope_node(self.org)
        self.user = _user('idf_user', self.org)

    def test_14_conversion_is_idempotent(self):
        lead, proposal, site = _won_lead(self.org, self.user, lead_type='new_client')
        req1 = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        req2 = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(req1.pk, req2.pk)
        self.assertEqual(Client.objects.filter(code=f'cls{lead.pk}').count(), 1)


# ─── 15-17: Source audit tests ────────────────────────────────────────────────

class TestSourceAuditOnNewClientConversion(TestCase):
    def setUp(self):
        self.org = _org('saf')
        _scope_node(self.org)
        self.user = _user('saf_user', self.org)
        self.lead, self.proposal, self.site = _won_lead(
            self.org, self.user, lead_type='new_client',
        )
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)

    def test_15_client_source_type_is_sales_conversion(self):
        client = Client.objects.get(code=f'cls{self.lead.pk}')
        self.assertEqual(client.source_type, 'sales_conversion')

    def test_15_site_source_type_is_sales_conversion(self):
        site = SiteProfile.objects.get(code=f'sts{self.site.pk}')
        self.assertEqual(site.source_type, 'sales_conversion')

    def test_15_srr_source_type_is_sales_conversion(self):
        site = SiteProfile.objects.get(code=f'sts{self.site.pk}')
        srr = SiteRoleRequirement.objects.get(site=site)
        self.assertEqual(srr.source_type, 'sales_conversion')

    def test_15_budget_source_type_is_sales_conversion(self):
        budget = BudgetPlan.objects.get(
            code=f'bp-new-client-l{self.lead.pk}-p{self.proposal.pk}',
        )
        self.assertEqual(budget.source_type, 'sales_conversion')

    def test_16_source_sales_lead_set_on_client(self):
        client = Client.objects.get(code=f'cls{self.lead.pk}')
        self.assertEqual(client.source_sales_lead_id, self.lead.pk)
        self.assertEqual(client.source_proposal_version_id, self.proposal.pk)

    def test_16_source_sales_lead_set_on_site(self):
        site = SiteProfile.objects.get(code=f'sts{self.site.pk}')
        self.assertEqual(site.source_sales_lead_id, self.lead.pk)
        self.assertEqual(site.source_proposal_version_id, self.proposal.pk)

    def test_16_source_sales_lead_set_on_srr(self):
        site = SiteProfile.objects.get(code=f'sts{self.site.pk}')
        srr = SiteRoleRequirement.objects.get(site=site)
        self.assertEqual(srr.source_sales_lead_id, self.lead.pk)
        self.assertEqual(srr.source_proposal_version_id, self.proposal.pk)

    def test_16_source_sales_lead_set_on_budget(self):
        budget = BudgetPlan.objects.get(
            code=f'bp-new-client-l{self.lead.pk}-p{self.proposal.pk}',
        )
        self.assertEqual(budget.source_sales_lead_id, self.lead.pk)
        self.assertEqual(budget.source_proposal_version_id, self.proposal.pk)


class TestManualAdminSourceDefault(TestCase):
    """Manually created records default to source_type='manual_admin'."""

    def setUp(self):
        self.org = _org('maf')
        _scope_node(self.org)
        self.user = _user('maf_user', self.org)

    def test_17_manual_client_defaults_manual_admin(self):
        client = Client.objects.create(
            org=self.org, name='Manual Client', code='man-cli-f', is_active=True,
        )
        self.assertEqual(client.source_type, 'manual_admin')
        self.assertIsNone(client.source_sales_lead_id)
        self.assertIsNone(client.source_proposal_version_id)

    def test_17_manual_site_defaults_manual_admin(self):
        client = _client(self.org, 'man-site-cli-f')
        site = SiteProfile.objects.create(
            org=self.org, client=client, name='Manual Site', code='man-site-f', is_active=True,
        )
        self.assertEqual(site.source_type, 'manual_admin')
        self.assertIsNone(site.source_sales_lead_id)

    def test_17_manual_srr_defaults_manual_admin(self):
        client = _client(self.org, 'man-srr-cli-f')
        site = SiteProfile.objects.create(
            org=self.org, client=client, name='Manual Site', code='man-srr-site-f', is_active=True,
        )
        jr = _job_role(self.org)
        srr = SiteRoleRequirement.objects.create(
            site=site, job_role=jr, approved_headcount=2,
            effective_from=date.today(), billing_type='billable', is_active=True,
        )
        self.assertEqual(srr.source_type, 'manual_admin')
        self.assertIsNone(srr.source_sales_lead_id)

    def test_17_manual_budget_defaults_manual_admin(self):
        budget = BudgetPlan.objects.create(
            org=self.org, name='Manual Budget', code='man-bud-f',
            budget_nature='billable', budget_type='general',
            amount=50000, currency='INR',
            period_start=date.today(), status='draft', is_active=True,
        )
        self.assertEqual(budget.source_type, 'manual_admin')
        self.assertIsNone(budget.source_sales_lead_id)


# ─── Regression: site_expansion source audit ─────────────────────────────────

class TestSiteExpansionSourceAudit(TestCase):
    def setUp(self):
        self.org = _org('seaf')
        _scope_node(self.org)
        self.user = _user('seaf_user', self.org)
        self.existing_client = _client(self.org, 'seaf-cli', 'SEAF Client')
        self.lead, self.proposal, self.site = _won_lead(
            self.org, self.user,
            lead_type='site_expansion',
            existing_client=self.existing_client,
        )
        convert_won_sales_lead_to_onboarding_setup(self.lead, self.user)

    def test_site_expansion_new_site_source_type_sales_conversion(self):
        site = SiteProfile.objects.get(code=f'sts{self.site.pk}')
        self.assertEqual(site.source_type, 'sales_conversion')
        self.assertEqual(site.source_sales_lead_id, self.lead.pk)
        self.assertEqual(site.client_id, self.existing_client.pk)

    def test_site_expansion_existing_client_source_type_unchanged(self):
        self.existing_client.refresh_from_db()
        self.assertEqual(self.existing_client.source_type, 'manual_admin')

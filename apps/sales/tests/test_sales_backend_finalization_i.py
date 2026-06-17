"""
apps/sales/tests/test_sales_backend_finalization_i.py

Phase Sales-Backend-Finalization-I:
1. Lead-type completion: scope_expansion conversion + renewal hardening.
2. Manual admin creation safety: Department + User creation still works.
3. Proposal locking rules: direct mutation of locked proposals is blocked.
4. Survey → SalesRoleRequirement helper endpoint.
5. Sales dashboard summary endpoint.
6. End-to-end seed_sales_demo command.
"""

from decimal import Decimal
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mobilisation.models import MobilisationSetupRequest
from apps.sales.models import (
    ProposalBudgetLine,
    ProposalBreakupLine,
    ProposalVersion,
    SalesLead,
    SalesLeadActivity,
    SalesLeadSite,
    SalesRoleRequirement,
    SiteSurvey,
    SiteSurveyEquipmentLine,
    SiteSurveyIssueLine,
    SiteSurveyLocationLine,
    SiteSurveyScopeAnswer,
    SiteSurveyShiftDeployment,
    SurveyRoleMapping,
)
from apps.sales.proposal_calculation import is_proposal_locked
from apps.sales.services import (
    convert_won_sales_lead_to_onboarding_setup,
    generate_proposal_version,
    generate_role_requirements_from_survey,
    mark_lead_won_from_client_approval,
    mark_proposal_internally_approved,
    record_client_response,
    seed_default_survey_lines,
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


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _org(code):
    org = Organization.objects.create(name=f'Org {code}', code=code)
    seed_default_proposal_component_rules(org=org)
    return org


def _scope_node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )


def _user(username, org, caps=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.is_superuser = is_superuser
    u.is_staff = is_superuser
    u.save()
    if caps:
        role, _ = AccessRole.objects.get_or_create(
            org=org, code=f'role_{username}', defaults={'name': f'role_{username}'},
        )
        bootstrap_role_permissions(role, caps=caps)
        if scope_node is None:
            scope_node = ScopeNode.objects.filter(
                org=org, node_type='company',
            ).first() or _scope_node(org)
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _job_role(org, code='guard_i', name='Guard I'):
    return JobRole.objects.get_or_create(
        org=org, code=code, defaults={'name': name, 'skill_category': 'unskilled'},
    )[0]


def _client(org, code='acme-i', name='Acme I', org_node=None):
    cli = Client.objects.create(org=org, name=name, code=code, is_active=True)
    if org_node is not None:
        cnode = ScopeNode.objects.create(
            org=org, parent=org_node, name=name, code=code, node_type='client',
            path=f'{org_node.path}/{code}', depth=org_node.depth + 1, is_active=True,
        )
        cli.scope_node = cnode
        cli.save(update_fields=['scope_node'])
    return cli


def _won_lead(org, user, lead_type='new_client', existing_client=None,
              client_name='Lead Client I'):
    """Create a won SalesLead with a final-approved proposal ready for conversion."""
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
    site = SalesLeadSite.objects.create(
        lead=lead, site_name='Site I', city='Mumbai', state='MH',
    )
    jr = _job_role(org)
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=jr, manpower_count=3,
    )

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


# ─────────────────────────────────────────────────────────────────────────────
# 1. Lead Type Completion — scope_expansion + renewal
# ─────────────────────────────────────────────────────────────────────────────

class TestScopeExpansionConversion(TestCase):
    def setUp(self):
        self.org = _org('scxi')
        self.org_node = _scope_node(self.org)
        self.user = _user('scxi_user', self.org)
        self.existing_client = _client(
            self.org, 'scxi-cli', 'Existing Acme', org_node=self.org_node,
        )

    def test_scope_expansion_requires_existing_client(self):
        """scope_expansion conversion fails when existing_client is missing."""
        from apps.sales.services import _validate_lead_type_constraints
        lead = SalesLead.objects.create(
            org=self.org, lead_type='scope_expansion',
            client_name='No Client Set',
            current_stage='won', current_status='won',
            existing_client=None,
        )
        with self.assertRaises(ValueError) as cm:
            _validate_lead_type_constraints(lead)
        self.assertIn('scope_expansion lead requires existing_client', str(cm.exception))

    def test_scope_expansion_reuses_existing_client(self):
        """scope_expansion creates no new Client; existing_client is bound."""
        lead, proposal, _ = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        before = Client.objects.filter(org=self.org).count()
        req = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(Client.objects.filter(org=self.org).count(), before)
        self.assertEqual(req.client_id, self.existing_client.pk)
        self.assertEqual(req.mobilisation_type, 'scope_expansion')

    def test_scope_expansion_does_not_require_matching_client_name(self):
        """The lead's client_name may differ from existing_client.name."""
        lead, proposal, _ = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
            client_name='A Totally Different Display Label',
        )
        # Should not raise: name mismatch is allowed.
        req = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(req.client_id, self.existing_client.pk)

    def test_scope_expansion_reuses_existing_site_by_name(self):
        """Existing SiteProfile under the client (matched by name) is reused."""
        # Pre-create a site under existing_client with name matching lead-site name
        existing_site = SiteProfile.objects.create(
            org=self.org, client=self.existing_client,
            name='Site I', code='existing-site-1', is_active=True,
        )
        lead, proposal, lead_site = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        before_count = SiteProfile.objects.filter(client=self.existing_client).count()
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        # No new SiteProfile was created
        self.assertEqual(
            SiteProfile.objects.filter(client=self.existing_client).count(),
            before_count,
        )
        # SRR was created and linked to the EXISTING SiteProfile
        srr = SiteRoleRequirement.objects.get(
            site=existing_site, source_sales_lead=lead,
        )
        self.assertEqual(srr.source_type, 'sales_conversion')

    def test_scope_expansion_creates_new_site_when_no_match(self):
        """If no SiteProfile name matches the lead-site, a new SiteProfile is created."""
        lead, proposal, lead_site = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        new_site = SiteProfile.objects.get(
            client=self.existing_client, code=f'sts{lead_site.pk}',
        )
        self.assertEqual(new_site.source_type, 'sales_conversion')
        self.assertEqual(new_site.source_sales_lead_id, lead.pk)

    def test_scope_expansion_creates_new_srr_and_budget(self):
        """New SRRs and BudgetPlan are created with full source audit."""
        lead, proposal, _ = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)

        srr_qs = SiteRoleRequirement.objects.filter(source_sales_lead=lead)
        self.assertTrue(srr_qs.exists())
        for srr in srr_qs:
            self.assertEqual(srr.source_type, 'sales_conversion')
            self.assertEqual(srr.source_proposal_version_id, proposal.pk)

        budget = BudgetPlan.objects.get(
            code=f'bp-scope-exp-l{lead.pk}-p{proposal.pk}',
        )
        self.assertEqual(budget.source_type, 'sales_conversion')
        self.assertEqual(budget.source_sales_lead_id, lead.pk)
        self.assertEqual(budget.source_proposal_version_id, proposal.pk)
        self.assertIn('Scope Expansion Budget', budget.name)

    def test_scope_expansion_creates_mobilisation_request(self):
        lead, proposal, _ = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        req = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(req.mobilisation_type, 'scope_expansion')
        self.assertEqual(req.source_sales_lead_id, lead.pk)
        self.assertEqual(req.source_proposal_version_id, proposal.pk)

    def test_scope_expansion_idempotent(self):
        lead, proposal, _ = _won_lead(
            self.org, self.user,
            lead_type='scope_expansion',
            existing_client=self.existing_client,
        )
        req1 = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        req2 = convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(req1.pk, req2.pk)


class TestRenewalAndInvalidLeadType(TestCase):
    def setUp(self):
        self.org = _org('rnli')
        _scope_node(self.org)
        self.user = _user('rnli_user', self.org)
        self.existing_client = _client(self.org, 'rnli-cli')

    def test_renewal_raises_specific_error(self):
        lead = SalesLead.objects.create(
            org=self.org, lead_type='renewal',
            existing_client=self.existing_client,
            client_name=self.existing_client.name,
            current_stage='won', current_status='won',
        )
        # Need a final-approved proposal so we pass earlier guards
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

        with self.assertRaises(ValueError) as cm:
            convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertIn('renewal conversion is not implemented yet', str(cm.exception))
        self.assertIn('Clone or extend existing contract workflow required',
                      str(cm.exception))

    def test_invalid_lead_type_fails_cleanly(self):
        from apps.sales.services import _validate_lead_type_constraints
        # Construct an in-memory lead with a bogus lead_type (don't save to DB
        # since CharField choice validation only fires on full_clean()).
        lead = SalesLead(
            org=self.org,
            lead_type='__bogus__',
            client_name='Bogus',
        )
        with self.assertRaises(ValueError) as cm:
            _validate_lead_type_constraints(lead)
        self.assertIn('Unsupported lead_type', str(cm.exception))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Manual Admin Creation Safety: Department + User
# (Client/SiteProfile/SRR/BudgetPlan source_type defaults are already covered
#  in test_lead_type_source_audit_f.py.)
# ─────────────────────────────────────────────────────────────────────────────

class TestManualAdminCreationSafety(TestCase):
    def setUp(self):
        self.org = _org('madi')
        _scope_node(self.org)

    def test_department_can_be_created_directly(self):
        dept = Department.objects.create(
            org=self.org, name='Manual Department', code='manual-dept',
            is_active=True,
        )
        self.assertIsNotNone(dept.pk)
        self.assertEqual(dept.org_id, self.org.pk)

    def test_user_can_be_created_directly_in_org(self):
        u = User.objects.create_user(username='manual_user_i', password='pwd')
        u.org = self.org
        u.save()
        self.assertEqual(u.org_id, self.org.pk)
        self.assertTrue(u.has_usable_password())


# ─────────────────────────────────────────────────────────────────────────────
# 3. Proposal Locking Rules
# ─────────────────────────────────────────────────────────────────────────────

LOCKED_STATUSES = [
    'submitted_internal',
    'internally_approved',
    'sent_to_client',
    'client_approved',
    'client_rejected',
    'client_revision_required',
    'client_negotiation',
    'locked',
]


class TestProposalLocking(TestCase):
    def setUp(self):
        self.org = _org('lcki')
        self.org_node = _scope_node(self.org)
        self.user = _user(
            'lcki_user', self.org,
            caps=['sales_proposal.read', 'sales_proposal.update', 'sales_proposal.create'],
            scope_node=self.org_node,
        )
        lead = SalesLead.objects.create(
            org=self.org, lead_type='new_client', client_name='Lock Co',
            created_by=self.user,
        )
        self.lead = lead
        self.proposal = ProposalVersion.objects.create(
            lead=lead, version_number=1, status='draft', created_by=self.user,
            grand_total=50000,
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.user)

    def _set_status(self, status_value):
        self.proposal.status = status_value
        self.proposal.save(update_fields=['status'])

    def test_is_proposal_locked_for_each_status(self):
        for s in LOCKED_STATUSES:
            self._set_status(s)
            self.proposal.refresh_from_db()
            self.assertTrue(
                is_proposal_locked(self.proposal),
                f'Expected proposal in status={s!r} to be locked',
            )

    def test_draft_proposal_is_not_locked(self):
        self._set_status('draft')
        self.proposal.refresh_from_db()
        self.assertFalse(is_proposal_locked(self.proposal))

    def test_implicit_converted_via_mobilisation_request(self):
        """Even with status='draft', a referencing MobilisationSetupRequest locks it."""
        # Stand up a minimal MobilisationSetupRequest pointing at this proposal
        cli = _client(self.org, 'lcki-cli')
        MobilisationSetupRequest.objects.create(
            org=self.org, client=cli, requested_by=self.user,
            mobilisation_type='new_client', source_sales_lead=self.lead,
            source_proposal_version=self.proposal, status='draft',
        )
        self.proposal.refresh_from_db()
        self.assertTrue(is_proposal_locked(self.proposal))

    def test_patch_proposal_draft_succeeds(self):
        """Drafts are editable via the API."""
        url = reverse('proposal-version-detail', args=[self.proposal.pk])
        resp = self.client_api.patch(url, {'sales_remarks': 'draft notes'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.sales_remarks, 'draft notes')

    def test_patch_proposal_locked_returns_400(self):
        url = reverse('proposal-version-detail', args=[self.proposal.pk])
        for s in LOCKED_STATUSES:
            self._set_status(s)
            resp = self.client_api.patch(
                url, {'sales_remarks': f'attempt @ {s}'}, format='json',
            )
            self.assertIn(resp.status_code, (400,),
                          f'Expected 400 for status={s!r}, got {resp.status_code}')

    def test_patch_budget_line_locked_returns_400(self):
        line = ProposalBudgetLine.objects.create(
            proposal_version=self.proposal, description='Line A',
            manpower_count=1, unit_cost=Decimal('100'), total_cost=Decimal('100'),
            sort_order=1,
        )
        self._set_status('sent_to_client')
        url = reverse('proposal-budget-line-detail', args=[line.pk])
        resp = self.client_api.patch(url, {'description': 'edited'}, format='json')
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_patch_breakup_line_locked_returns_400(self):
        line = ProposalBreakupLine.objects.create(
            proposal_version=self.proposal, component_name='Basic',
            component_type='earning', amount=Decimal('100'),
            sort_order=1,
        )
        self._set_status('client_approved')
        url = reverse('proposal-breakup-line-detail', args=[line.pk])
        resp = self.client_api.patch(url, {'component_name': 'Edited'}, format='json')
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_delete_proposal_locked_returns_400(self):
        self._set_status('internally_approved')
        url = reverse('proposal-version-detail', args=[self.proposal.pk])
        resp = self.client_api.delete(url)
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_clone_revision_produces_editable_draft(self):
        from apps.sales.services import clone_proposal_for_revision
        self._set_status('client_revision_required')
        revision = clone_proposal_for_revision(self.proposal, self.user)
        self.assertEqual(revision.status, 'draft')
        self.assertFalse(is_proposal_locked(revision))
        self.proposal.refresh_from_db()
        self.assertTrue(is_proposal_locked(self.proposal))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Survey → Role Requirement helper
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateRoleRequirementsFromSurvey(TestCase):
    def setUp(self):
        self.org = _org('grri')
        self.org_node = _scope_node(self.org)
        self.user = _user(
            'grri_user', self.org,
            caps=['sales_survey.read', 'sales_survey.update'],
            scope_node=self.org_node,
        )
        self.lead = SalesLead.objects.create(
            org=self.org, lead_type='new_client', client_name='Survey-Origin Co',
            created_by=self.user,
        )
        self.site = SalesLeadSite.objects.create(
            lead=self.lead, site_name='HQ', city='Pune', state='MH',
        )
        self.survey = SiteSurvey.objects.create(lead=self.lead, site=self.site)

        # JobRoles with names matching desired shift descriptions
        self.electrician = JobRole.objects.create(
            org=self.org, name='Electrician', code='electrician',
            skill_category='skilled', is_active=True,
        )
        self.plumber = JobRole.objects.create(
            org=self.org, name='Plumber', code='plumber',
            skill_category='skilled', is_active=True,
        )
        self.wage_category = ensure_wage_category('skilled', 'Skilled')
        SurveyRoleMapping.objects.create(
            org=self.org,
            description_text='Electrician',
            job_role=self.electrician,
            wage_category=self.wage_category,
            service_category='Technical',
            shift_hours=Decimal('8.0'),
            working_days=Decimal('26.0'),
        )
        SurveyRoleMapping.objects.create(
            org=self.org,
            description_text='Plumber',
            job_role=self.plumber,
            wage_category=self.wage_category,
            service_category='Technical',
            shift_hours=Decimal('8.0'),
            working_days=Decimal('26.0'),
        )

        # Survey shift-deployment rows
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Electrician',
            general_count=1, first_shift_count=1, second_shift_count=0,
            total_count=2, line_type='item', sort_order=1,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Plumber',
            general_count=0, first_shift_count=1, second_shift_count=1,
            total_count=2, line_type='item', sort_order=2,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Machinery',
            line_type='item', sort_order=3,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='HK Consumables',
            line_type='item', sort_order=4,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Sub Total',
            line_type='item', sort_order=5,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='TOTAL',
            line_type='item', sort_order=6,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Carpenter',
            line_type='item', sort_order=7,
            total_count=1,
        )

    def test_creates_srrs_for_known_job_roles(self):
        result = generate_role_requirements_from_survey(self.survey, self.user)
        descriptions = [c['description'] for c in result['created']]
        self.assertIn('Electrician', descriptions)
        self.assertIn('Plumber', descriptions)
        # SRR persisted
        self.assertTrue(SalesRoleRequirement.objects.filter(
            survey=self.survey, job_role=self.electrician,
        ).exists())

    def test_ignores_machinery_and_total_rows(self):
        result = generate_role_requirements_from_survey(self.survey, self.user)
        skipped_reasons = {
            (s['description'], s['reason']) for s in result['skipped']
        }
        for ignored in ('Machinery', 'HK Consumables', 'Sub Total', 'TOTAL'):
            self.assertIn(
                (ignored, 'ignored_template_row'), skipped_reasons,
                f'Row "{ignored}" should be skipped as a template row',
            )

    def test_missing_job_role_returns_row_error(self):
        result = generate_role_requirements_from_survey(self.survey, self.user)
        errors = [
            (e['description'], e['reason']) for e in result['errors']
        ]
        self.assertIn(('Carpenter', 'role_mapping_not_found'), errors)

    def test_idempotent_repeat(self):
        first = generate_role_requirements_from_survey(self.survey, self.user)
        created_first = len(first['created'])
        self.assertGreater(created_first, 0)
        second = generate_role_requirements_from_survey(self.survey, self.user)
        self.assertEqual(len(second['created']), 0)
        already = [s for s in second['skipped'] if s['reason'] == 'already_exists']
        self.assertEqual(len(already), created_first)

    def test_regenerate_updates_existing_requirement_when_headcount_changes(self):
        generate_role_requirements_from_survey(self.survey, self.user)
        row = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey,
            description='Electrician',
        )
        row.total_count = 6
        row.save(update_fields=['total_count', 'updated_at'])

        result = generate_role_requirements_from_survey(self.survey, self.user)

        self.assertEqual(len(result['created']), 0)
        updated = [u for u in result['updated'] if u['description'] == 'Electrician']
        self.assertEqual(len(updated), 1)
        srr = SalesRoleRequirement.objects.get(
            survey=self.survey,
            job_role=self.electrician,
        )
        self.assertEqual(srr.manpower_count, 6)

    def test_role_linked_deployment_row_generates_without_description_mapping(self):
        hvac = JobRole.objects.create(
            org=self.org, name='HVAC', code='hvac',
            skill_category='skilled', is_active=True,
        )
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey,
            job_role=hvac,
            description='HVAC',
            general_count=1,
            first_shift_count=1,
            second_shift_count=1,
            night_shift_count=1,
            total_count=0,
            line_type='item',
            sort_order=8,
        )

        result = generate_role_requirements_from_survey(self.survey, self.user)

        hvac_created = [c for c in result['created'] if c['description'] == 'HVAC']
        self.assertEqual(len(hvac_created), 1)
        self.assertEqual(hvac_created[0]['manpower_count'], 4)
        srr = SalesRoleRequirement.objects.get(survey=self.survey, job_role=hvac)
        self.assertEqual(srr.wage_category_id, self.wage_category.pk)
        self.assertEqual(srr.manpower_count, 4)

    def test_generated_srrs_link_to_lead_site_survey(self):
        generate_role_requirements_from_survey(self.survey, self.user)
        srr = SalesRoleRequirement.objects.get(
            survey=self.survey, job_role=self.electrician,
        )
        self.assertEqual(srr.lead_id, self.lead.pk)
        self.assertEqual(srr.site_id, self.site.pk)
        self.assertEqual(srr.manpower_count, 2)
        self.assertEqual(srr.wage_category_id, self.wage_category.pk)
        self.assertEqual(srr.service_category, 'Technical')
        self.assertEqual(srr.shift_hours, Decimal('8.0'))
        self.assertEqual(srr.working_days, Decimal('26.0'))
        self.assertTrue(srr.created_from_survey)

    def test_generated_srr_carries_operations_commercial_snapshot(self):
        row = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey,
            description='Electrician',
        )
        row.reliever_count = Decimal('1.00')
        row.base_wage = Decimal('19000.00')
        row.base_wage_overridden = True
        row.base_wage_source = 'manual'
        row.base_wage_override_reason = 'Remote site allowance.'
        row.total_count = Decimal('0.00')
        row.save(update_fields=[
            'reliever_count',
            'base_wage',
            'base_wage_overridden',
            'base_wage_source',
            'base_wage_override_reason',
            'total_count',
            'updated_at',
        ])

        generate_role_requirements_from_survey(self.survey, self.user)

        srr = SalesRoleRequirement.objects.get(
            survey=self.survey, job_role=self.electrician,
        )
        row.refresh_from_db()
        self.assertEqual(row.total_count, Decimal('3.00'))
        self.assertEqual(row.monthly_amount, Decimal('57000.00'))
        self.assertEqual(srr.source_shift_deployment_id, row.pk)
        self.assertEqual(srr.manpower_count, 3)
        self.assertEqual(srr.reliever_count, Decimal('1.00'))
        self.assertEqual(srr.operational_base_wage, Decimal('19000.00'))
        self.assertTrue(srr.operational_base_wage_overridden)
        self.assertEqual(srr.operational_monthly_amount, Decimal('57000.00'))

    def test_falls_back_to_shift_sum_when_total_count_zero(self):
        """When total_count == 0, headcount uses general+first+second."""
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Helper',
            general_count=2, first_shift_count=1, second_shift_count=0,
            total_count=0, line_type='item', sort_order=8,
        )
        JobRole.objects.create(
            org=self.org, name='Helper', code='helper',
            skill_category='unskilled', is_active=True,
        )
        SurveyRoleMapping.objects.create(
            org=self.org,
            description_text='Helper',
            job_role=JobRole.objects.get(org=self.org, code='helper'),
            wage_category=self.wage_category,
            service_category='Housekeeping',
            shift_hours=Decimal('8.0'),
            working_days=Decimal('26.0'),
        )
        result = generate_role_requirements_from_survey(self.survey, self.user)
        helper_created = [c for c in result['created'] if c['description'] == 'Helper']
        self.assertEqual(len(helper_created), 1)
        self.assertEqual(helper_created[0]['manpower_count'], 3)

    def test_inactive_mapping_is_ignored(self):
        SurveyRoleMapping.objects.filter(
            org=self.org, description_text='Electrician',
        ).update(is_active=False)

        result = generate_role_requirements_from_survey(self.survey, self.user)
        created_descriptions = {c['description'] for c in result['created']}
        errors = {(e['description'], e['reason']) for e in result['errors']}

        self.assertNotIn('Electrician', created_descriptions)
        self.assertIn(('Electrician', 'role_mapping_not_found'), errors)

    def test_not_applicable_shift_rows_are_skipped(self):
        row = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey, description='Electrician',
        )
        row.is_applicable = False
        row.not_applicable_reason = 'No electrician required for this site.'
        row.save(update_fields=['is_applicable', 'not_applicable_reason', 'updated_at'])

        result = generate_role_requirements_from_survey(self.survey, self.user)
        created_descriptions = {c['description'] for c in result['created']}
        skipped_reasons = {
            (s['description'], s['reason']) for s in result['skipped']
        }

        self.assertNotIn('Electrician', created_descriptions)
        self.assertIn(('Electrician', 'not_applicable'), skipped_reasons)
        self.assertFalse(SalesRoleRequirement.objects.filter(
            survey=self.survey, job_role=self.electrician,
        ).exists())

    def test_submit_to_operations_auto_creates_ready_survey_form_rows(self):
        lead = SalesLead.objects.create(
            org=self.org, lead_type='new_client', client_name='Auto Survey Co',
            created_by=self.user,
        )
        site = SalesLeadSite.objects.create(
            lead=lead, site_name='Auto Site', city='Pune', state='MH',
        )
        operations_department = Department.objects.create(
            org=self.org, name='Operations', code='operations',
        )
        self.user.department = operations_department
        self.user.save(update_fields=['department', 'updated_at'])

        submit_to_operations(lead, self.user, operations_owner=self.user)
        survey = SiteSurvey.objects.get(lead=lead, site=site)

        self.assertGreater(SiteSurveyScopeAnswer.objects.filter(survey=survey).count(), 0)
        self.assertGreater(SiteSurveyShiftDeployment.objects.filter(survey=survey).count(), 0)
        self.assertGreater(SiteSurveyLocationLine.objects.filter(survey=survey).count(), 0)
        self.assertGreater(SiteSurveyEquipmentLine.objects.filter(survey=survey).count(), 0)
        self.assertGreater(SiteSurveyIssueLine.objects.filter(survey=survey).count(), 0)
        self.assertFalse(SiteSurveyShiftDeployment.objects.filter(
            survey=survey, is_applicable=False,
        ).exists())

    def test_endpoint_requires_capability(self):
        no_cap_user = _user('grri_nocap', self.org)
        client = APIClient()
        client.force_authenticate(user=no_cap_user)
        url = reverse('site-survey-generate-role-requirements', args=[self.survey.pk])
        resp = client.post(url)
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_endpoint_returns_summary(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = reverse('site-survey-generate-role-requirements', args=[self.survey.pk])
        resp = client.post(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn('created', body)
        self.assertIn('skipped', body)
        self.assertIn('errors', body)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Sales Dashboard Summary
# ─────────────────────────────────────────────────────────────────────────────

class TestSalesDashboardSummary(TestCase):
    def setUp(self):
        self.org_a = _org('dshi-a')
        self.org_b = _org('dshi-b')
        self.node_a = _scope_node(self.org_a)
        self.node_b = _scope_node(self.org_b)
        self.reader_a = _user(
            'dshi_reader_a', self.org_a,
            caps=['sales_lead.read'], scope_node=self.node_a,
        )
        self.reader_b = _user(
            'dshi_reader_b', self.org_b,
            caps=['sales_lead.read'], scope_node=self.node_b,
        )
        self.no_cap_user = _user('dshi_nocap', self.org_a)

        # Org A leads
        for stage, lt, count in [
            ('draft', 'new_client', 2),
            ('site_survey_completed', 'new_client', 1),
            ('won', 'site_expansion', 2),
            ('won', 'new_client', 1),
            ('lost', 'renewal', 1),
        ]:
            for i in range(count):
                SalesLead.objects.create(
                    org=self.org_a, lead_type=lt,
                    client_name=f'A-{stage}-{lt}-{i}',
                    current_stage=stage, current_status='draft',
                )

        # Org B has one lead that must NOT bleed into A
        SalesLead.objects.create(
            org=self.org_b, lead_type='new_client',
            client_name='B-only', current_stage='won', current_status='draft',
        )

        # Org A survey buckets
        lead_for_surveys = SalesLead.objects.create(
            org=self.org_a, lead_type='new_client', client_name='S-host',
            current_stage='draft',
        )
        sites = []
        for i in range(4):
            sites.append(SalesLeadSite.objects.create(
                lead=lead_for_surveys, site_name=f'S{i}',
            ))
        # 2 pending+unassigned, 1 in_progress, 1 completed
        SiteSurvey.objects.create(lead=lead_for_surveys, site=sites[0], status='pending')
        SiteSurvey.objects.create(lead=lead_for_surveys, site=sites[1], status='pending')
        SiteSurvey.objects.create(lead=lead_for_surveys, site=sites[2], status='in_progress')
        SiteSurvey.objects.create(lead=lead_for_surveys, site=sites[3], status='completed')

        # Org A proposals across statuses
        proposal_lead = SalesLead.objects.create(
            org=self.org_a, lead_type='new_client', client_name='P-host',
        )
        for ver, st in enumerate([
            'draft', 'submitted_internal', 'internally_approved',
            'sent_to_client', 'client_approved', 'client_rejected',
            'client_revision_required',
        ], start=1):
            ProposalVersion.objects.create(
                lead=proposal_lead, version_number=ver, status=st,
                grand_total=1000,
            )

        # Conversion: one won lead has a MobilisationSetupRequest (converted),
        # the rest are won_pending_mobilisation.
        won_a = SalesLead.objects.filter(org=self.org_a, current_stage='won').first()
        cli = _client(self.org_a, 'dshi-cli')
        MobilisationSetupRequest.objects.create(
            org=self.org_a, client=cli, requested_by=self.reader_a,
            mobilisation_type='new_client', source_sales_lead=won_a, status='draft',
        )

        # Activity entries (org A)
        for i in range(12):
            SalesLeadActivity.objects.create(
                org=self.org_a, lead=lead_for_surveys,
                activity_type='lead_created',
                title=f'Activity #{i}', actor=self.reader_a,
            )

        self.url = '/api/sales/dashboard/summary/'

    def test_requires_sales_lead_read_capability(self):
        client = APIClient()
        client.force_authenticate(user=self.no_cap_user)
        resp = client.get(self.url)
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_authorized_user_gets_org_scoped_counts(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_a)
        resp = client.get(self.url)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        # Org A total leads = 2+1+2+1+1 + 1 (lead_for_surveys) + 1 (proposal_lead) = 9
        self.assertEqual(body['leads']['total'], 9)
        stage_lookup = {row['stage']: row['count'] for row in body['leads']['by_stage']}
        self.assertEqual(stage_lookup.get('draft', 0), 4)  # 2 + lead_for_surveys + proposal_lead
        self.assertEqual(stage_lookup.get('won', 0), 3)

    def test_other_org_data_does_not_leak(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_b)
        resp = client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['leads']['total'], 1)

    def test_survey_buckets(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_a)
        body = client.get(self.url).json()
        self.assertEqual(body['surveys']['pending_assignment'], 2)
        self.assertEqual(body['surveys']['in_progress'], 1)
        self.assertEqual(body['surveys']['completed'], 1)

    def test_proposal_buckets(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_a)
        body = client.get(self.url).json()
        self.assertEqual(body['proposals']['draft'], 1)
        self.assertEqual(body['proposals']['pending_internal_approval'], 1)
        self.assertEqual(body['proposals']['internally_approved'], 1)
        self.assertEqual(body['proposals']['sent_to_client'], 1)
        self.assertEqual(body['proposals']['client_approved'], 1)
        self.assertEqual(body['proposals']['client_rejected'], 1)
        self.assertEqual(body['proposals']['revision_requested'], 1)

    def test_conversion_buckets(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_a)
        body = client.get(self.url).json()
        # 3 won leads in org A; 1 converted (has a MobilisationSetupRequest)
        self.assertEqual(body['conversion']['converted'], 1)
        self.assertEqual(body['conversion']['won_pending_mobilisation'], 2)

    def test_recent_activity_limited_to_10_and_scoped(self):
        client = APIClient()
        client.force_authenticate(user=self.reader_a)
        body = client.get(self.url).json()
        self.assertEqual(len(body['recent_activity']), 10)


# ─────────────────────────────────────────────────────────────────────────────
# 6. End-to-End Seed Command
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedSalesDemoCommand(TestCase):
    def _run(self):
        out = StringIO()
        call_command('seed_sales_demo', stdout=out)
        return out.getvalue()

    def test_command_is_idempotent(self):
        self._run()
        org_count = Organization.objects.filter(code='logicon-demo').count()
        lead_count_first = SalesLead.objects.filter(org__code='logicon-demo').count()
        # Second run must not raise and must not duplicate
        self._run()
        self.assertEqual(
            Organization.objects.filter(code='logicon-demo').count(),
            org_count,
        )
        self.assertEqual(
            SalesLead.objects.filter(org__code='logicon-demo').count(),
            lead_count_first,
        )

    def test_workflow_template_exists(self):
        from apps.workflow.models import WorkflowTemplate
        self._run()
        self.assertTrue(
            WorkflowTemplate.objects.filter(
                org__code='logicon-demo',
                code='sales_proposal_default',
                trigger_type='sales_proposal',
            ).exists()
        )

    def test_demo_leads_exist(self):
        self._run()
        org = Organization.objects.get(code='logicon-demo')
        self.assertTrue(
            SalesLead.objects.filter(org=org, lead_type='new_client').exists(),
        )
        self.assertTrue(
            SalesLead.objects.filter(org=org, lead_type='site_expansion').exists(),
        )

    def test_demo_survey_has_seeded_rows(self):
        self._run()
        org = Organization.objects.get(code='logicon-demo')
        lead = SalesLead.objects.filter(org=org, lead_type='new_client').first()
        survey = SiteSurvey.objects.filter(lead=lead).first()
        self.assertIsNotNone(survey)
        self.assertGreater(survey.shift_deployments.count(), 0)
        self.assertGreater(survey.scope_answers.count(), 0)

    def test_demo_proposal_can_be_generated(self):
        """Proposal calculation runs end-to-end on the seeded data."""
        from apps.sales.services import generate_proposal_version
        self._run()
        org = Organization.objects.get(code='logicon-demo')
        sales_user = User.objects.get(username='demo.sales')
        lead = SalesLead.objects.filter(org=org, lead_type='new_client').first()
        # Push lead past the survey gate so proposal generation is allowed.
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, sales_user)
        self.assertIsNotNone(proposal.pk)
        self.assertGreater(proposal.budget_lines.count(), 0)

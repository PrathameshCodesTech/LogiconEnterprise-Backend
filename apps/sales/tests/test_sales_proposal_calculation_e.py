"""
Phase Sales-Proposal-Calculation-E tests.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine,
)
from apps.sales.proposal_calculation import (
    get_wage_rate_for_requirement,
    build_salary_breakup,
    calculate_role_unit_cost,
    calculate_management_fee,
    calculate_gst,
    calculate_proposal_for_lead,
    assert_proposal_regeneratable,
    DEFAULT_GST_PERCENT,
    DEFAULT_MANAGEMENT_FEE_PERCENT,
    seed_default_proposal_component_rules,
)
from apps.sales.services import (
    generate_proposal_version,
    submit_to_operations,
    submit_proposal_for_internal_approval,
    mark_proposal_internally_approved,
    clone_proposal_for_revision,
)
from apps.sales.tests.proposal_wage_fixtures import (
    ensure_wage_category,
    ensure_location_area_mumbai,
    ensure_minimum_wage,
    wire_site_and_requirement_for_wages,
)
from apps.wages.models import MinimumWageRate


def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _user(org):
    u = User.objects.create_user(username=f'u_{org.code}', password='pass')
    u.org = org
    u.save()
    return u


def _lead_with_requirements(org, user, sites_roles):
    """
    sites_roles: list of (site_name, job_role_code, manpower, monthly_wage)
    """
    lead = SalesLead.objects.create(org=org, client_name='Calc Client')
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()

    for site_name, role_code, manpower, monthly_wage in sites_roles:
        site = SalesLeadSite.objects.create(
            lead=lead, site_name=site_name, city='Mumbai', state='MH',
        )
        jr = JobRole.objects.get_or_create(
            org=org, code=role_code, defaults={'name': role_code.title()},
        )[0]
        ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=monthly_wage)
        rr = SalesRoleRequirement.objects.create(
            lead=lead, site=site, job_role=jr,
            manpower_count=manpower, is_active=True,
        )
        wire_site_and_requirement_for_wages(site, rr, location, wage_cat)

    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    seed_default_proposal_component_rules(org=org)
    return lead


class TestWageLookup(TestCase):
    def setUp(self):
        self.org = _org('wlk')
        self.user = _user(self.org)
        self.lead = _lead_with_requirements(
            self.org, self.user, [('Site A', 'guard_a', 5, 12000)],
        )
        self.rr = SalesRoleRequirement.objects.filter(lead=self.lead).first()

    def test_wage_lookup_uses_location_and_category(self):
        rate = get_wage_rate_for_requirement(self.rr)
        self.assertEqual(rate.monthly_wage, Decimal('12000.00'))
        self.assertEqual(rate.location_id, self.rr.site.location_area_id)
        self.assertEqual(rate.wage_category_id, self.rr.wage_category_id)

    def test_salary_breakup_prefers_monthly_wage_as_basic(self):
        rate = get_wage_rate_for_requirement(self.rr)
        rate.daily_wage = Decimal('999.00')
        rate.save(update_fields=['daily_wage', 'updated_at'])
        lines = build_salary_breakup(self.rr, rate)
        basic = next(line for line in lines if line['component_name'] == 'Basic')
        self.assertEqual(basic['amount'], Decimal('12000.00'))

    def test_salary_breakup_falls_back_to_daily_wage_times_working_days(self):
        rate = get_wage_rate_for_requirement(self.rr)
        rate.monthly_wage = Decimal('0.00')
        rate.daily_wage = Decimal('800.00')
        rate.save(update_fields=['monthly_wage', 'daily_wage', 'updated_at'])
        self.rr.working_days = Decimal('24.0')
        self.rr.save(update_fields=['working_days', 'updated_at'])
        lines = build_salary_breakup(self.rr, rate)
        basic = next(line for line in lines if line['component_name'] == 'Basic')
        self.assertEqual(basic['amount'], Decimal('19200.00'))

    def test_salary_breakup_daily_wage_fallback_defaults_to_26_days(self):
        rate = get_wage_rate_for_requirement(self.rr)
        rate.monthly_wage = Decimal('0.00')
        rate.daily_wage = Decimal('700.00')
        rate.save(update_fields=['monthly_wage', 'daily_wage', 'updated_at'])
        self.rr.working_days = None
        self.rr.save(update_fields=['working_days', 'updated_at'])
        lines = build_salary_breakup(self.rr, rate)
        basic = next(line for line in lines if line['component_name'] == 'Basic')
        self.assertEqual(basic['amount'], Decimal('18200.00'))

    def test_missing_wage_config_raises_clear_error(self):
        MinimumWageRate.objects.all().delete()
        with self.assertRaises(ValueError) as ctx:
            get_wage_rate_for_requirement(self.rr)
        self.assertIn('No wage rate configured', str(ctx.exception))
        self.assertIn(self.rr.wage_category.name, str(ctx.exception))


class TestProposalGeneration(TestCase):
    def setUp(self):
        self.org = _org('pgen')
        self.user = _user(self.org)
        self.lead = _lead_with_requirements(
            self.org, self.user, [('HQ', 'hk', 10, 10000)],
        )

    def test_creates_budget_lines_linked_to_role_requirement(self):
        proposal = generate_proposal_version(self.lead, self.user)
        line = proposal.budget_lines.first()
        self.assertIsNotNone(line.role_requirement_id)
        self.assertGreater(line.unit_cost, 0)
        self.assertEqual(line.total_cost, line.unit_cost * line.manpower_count)

    def test_creates_breakup_lines_for_required_components(self):
        proposal = generate_proposal_version(self.lead, self.user)
        names = set(proposal.breakup_lines.values_list('component_name', flat=True))
        self.assertIn('Basic', names)
        self.assertIn('Role Monthly Total', names)
        self.assertIn('Employer PF', names)
        self.assertFalse(proposal.breakup_lines.filter(role_requirement__isnull=True).exists())

    def test_submit_rejects_unmapped_breakup_lines(self):
        proposal = generate_proposal_version(self.lead, self.user)
        line = proposal.breakup_lines.first()
        line.role_requirement = None
        line.save(update_fields=['role_requirement', 'updated_at'])

        with self.assertRaises(ValueError) as ctx:
            submit_proposal_for_internal_approval(proposal, self.user)
        self.assertIn('Salary breakup is missing role mapping', str(ctx.exception))

    def test_internal_approval_rejects_unmapped_breakup_lines(self):
        proposal = generate_proposal_version(self.lead, self.user)
        proposal.status = 'submitted_internal'
        proposal.save(update_fields=['status', 'updated_at'])
        line = proposal.breakup_lines.first()
        line.role_requirement = None
        line.save(update_fields=['role_requirement', 'updated_at'])

        with self.assertRaises(ValueError) as ctx:
            mark_proposal_internally_approved(proposal, self.user)
        self.assertIn('Salary breakup is missing role mapping', str(ctx.exception))

    def test_manpower_total_equals_sum_requirements(self):
        proposal = generate_proposal_version(self.lead, self.user)
        self.assertEqual(proposal.manpower_total, 10)

    def test_management_fee_calculated(self):
        proposal = generate_proposal_version(self.lead, self.user)
        expected = calculate_management_fee(
            proposal.subtotal_amount, proposal.management_fee_percent,
        )
        self.assertEqual(proposal.management_fee_amount, expected)

    def test_gst_when_enabled(self):
        proposal = generate_proposal_version(self.lead, self.user)
        proposal.gst_applicable = True
        proposal.save(update_fields=['gst_applicable'])
        calculate_proposal_for_lead(self.lead, proposal, force=True)
        proposal.refresh_from_db()
        taxable = proposal.subtotal_amount + proposal.management_fee_amount
        self.assertEqual(
            proposal.gst_amount,
            calculate_gst(taxable, True, DEFAULT_GST_PERCENT),
        )

    def test_gst_not_applied_when_disabled(self):
        proposal = generate_proposal_version(self.lead, self.user)
        self.assertFalse(proposal.gst_applicable)
        self.assertEqual(proposal.gst_amount, Decimal('0.00'))

    def test_grand_total_formula(self):
        proposal = generate_proposal_version(self.lead, self.user)
        proposal.gst_applicable = True
        proposal.save(update_fields=['gst_applicable'])
        calculate_proposal_for_lead(self.lead, proposal, force=True)
        proposal.refresh_from_db()
        expected = (
            proposal.subtotal_amount
            + proposal.management_fee_amount
            + proposal.gst_amount
        )
        self.assertEqual(proposal.grand_total, expected)

    def test_multiple_sites_calculate_independently(self):
        lead = _lead_with_requirements(
            self.org, self.user,
            [('Site A', 'role_a', 2, 10000), ('Site B', 'role_b', 3, 15000)],
        )
        proposal = generate_proposal_version(lead, self.user)
        self.assertEqual(proposal.budget_lines.count(), 2)
        self.assertEqual(proposal.manpower_total, 5)
        totals = sorted(proposal.budget_lines.values_list('total_cost', flat=True))
        self.assertNotEqual(totals[0], totals[1])


class TestVersionSafety(TestCase):
    def setUp(self):
        self.org = _org('vsafe')
        self.user = _user(self.org)
        self.lead = _lead_with_requirements(
            self.org, self.user, [('HQ', 'hk', 4, 12000)],
        )
        self.proposal = generate_proposal_version(self.lead, self.user)

    def test_locked_proposal_cannot_recalculate(self):
        self.proposal.status = 'sent_to_client'
        self.proposal.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            calculate_proposal_for_lead(self.lead, self.proposal, force=True)

    def test_clone_revision_does_not_mutate_source(self):
        self.proposal.status = 'sent_to_client'
        self.proposal.grand_total = Decimal('99999.00')
        self.proposal.save(update_fields=['status', 'grand_total'])
        old_total = self.proposal.grand_total
        clone = clone_proposal_for_revision(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.grand_total, old_total)
        self.assertEqual(clone.status, 'draft')
        self.assertNotEqual(clone.pk, self.proposal.pk)


class TestSerializerAmountFields(TestCase):
    def test_api_exposes_subtotal_management_gst(self):
        org = _org('ser')
        user = _user(org)
        lead = _lead_with_requirements(org, user, [('HQ', 'hk', 2, 12000)])
        proposal = generate_proposal_version(lead, user)
        from apps.access.capabilities import SALES_LEAD_READ, SALES_PROPOSAL_READ
        from apps.access.models import AccessRole, UserRoleAssignment
        from apps.access.tests.utils import bootstrap_role_permissions
        role = AccessRole.objects.create(org=org, code='sm', name='sm')
        bootstrap_role_permissions(role, [SALES_LEAD_READ, SALES_PROPOSAL_READ])
        node = ScopeNode.objects.create(
            org=org, code=org.code, name=org.code, node_type='company',
            parent=None, depth=0, path=org.code, is_active=True,
        )
        UserRoleAssignment.objects.create(user=user, role=role, scope_node=node)
        c = APIClient()
        c.force_authenticate(user)
        resp = c.get(f'/api/sales/proposal-versions/{proposal.pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('subtotal_amount', resp.data)
        self.assertIn('management_fee_amount', resp.data)
        self.assertIn('gst_amount', resp.data)
        self.assertGreater(Decimal(resp.data['subtotal_amount']), 0)


class TestManualOverridePreserved(TestCase):
    def test_non_force_skips_overridden_budget_line(self):
        org = _org('ovr')
        user = _user(org)
        lead = _lead_with_requirements(org, user, [('HQ', 'hk', 2, 12000)])
        proposal = generate_proposal_version(lead, user)
        line = proposal.budget_lines.first()
        line.is_manual_override = True
        line.unit_cost = Decimal('1.00')
        line.total_cost = Decimal('2.00')
        line.save()
        old_pk = line.pk
        calculate_proposal_for_lead(lead, proposal, force=False)
        self.assertTrue(ProposalBudgetLine.objects.filter(pk=old_pk).exists())

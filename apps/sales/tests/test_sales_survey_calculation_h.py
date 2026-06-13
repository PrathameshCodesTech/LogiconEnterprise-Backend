"""
apps/sales/tests/test_sales_survey_calculation_h.py

Phase Sales-Survey-and-Calculation-H tests:
  - Default survey seed creates every exact Excel row
  - Seed is idempotent and non-destructive on overwrite=True
  - structured/ endpoint returns grouped data
  - Child rows are org-scoped
  - Equipment item totals auto-compute
  - Proposal generation requires complete DB component rules
  - Complete DB ruleset overrides constants
  - Partial / inactive / out-of-date rules are ignored
  - percent_of_other resolves via base_component_code
  - API: ProposalComponentRule create + validation
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ,
    SALES_PROPOSAL_READ,
    SALES_PROPOSAL_UPDATE,
    SALES_SURVEY_READ,
    SALES_SURVEY_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import (
    ProposalComponentRule,
    SalesLead,
    SalesLeadSite,
    SalesRoleRequirement,
    SiteSurvey,
    SiteSurveyEquipmentLine,
    SiteSurveyIssueLine,
    SiteSurveyLocationLine,
    SiteSurveyScopeAnswer,
    SiteSurveyShiftDeployment,
)
from apps.sales.proposal_calculation import (
    RULE_KINDS,
    ProposalComponentRulesNotConfigured,
    _load_org_component_ruleset,
    _load_required_org_component_ruleset,
    build_salary_breakup,
    get_wage_rate_for_requirement,
    seed_default_proposal_component_rules,
)
from apps.sales.services import (
    generate_proposal_version,
    seed_default_survey_lines,
    submit_to_operations,
)
from apps.sales.survey_templates import (
    ISSUE_ROWS,
    LOCATION_ROWS,
    MAJOR_EQUIPMENT_ROWS,
    MINOR_EQUIPMENT_ROWS,
    SCOPE_FIELDS,
    SHIFT_DEPLOYMENT_ROWS,
)
from apps.sales.tests.proposal_wage_fixtures import (
    ensure_location_area_mumbai,
    ensure_minimum_wage,
    ensure_wage_category,
    wire_site_and_requirement_for_wages,
)


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope(org):
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
            scope_node = _scope(org)
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _basic_survey(org):
    lead = SalesLead.objects.create(org=org, client_name='Calc Client Co.')
    site = SalesLeadSite.objects.create(
        lead=lead, site_name='HQ', city='Mumbai', state='MH',
    )
    return SiteSurvey.objects.create(lead=lead, site=site)


def _lead_with_one_requirement(org, user, monthly_wage=10000):
    """Phase E-style minimal lead ready for proposal generation."""
    lead = SalesLead.objects.create(org=org, client_name='Rule Client')
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    site = SalesLeadSite.objects.create(
        lead=lead, site_name='HQ', city='Mumbai', state='MH',
    )
    jr, _ = JobRole.objects.get_or_create(
        org=org, code='hk_role', defaults={'name': 'HK Role'},
    )
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=monthly_wage)
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=jr, manpower_count=4, is_active=True,
    )
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    return lead, rr


# ─── Group 1: Seed correctness ────────────────────────────────────────────────

class TestSeedCorrectness(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('seed')
        cls.survey = _basic_survey(cls.org)

    def test_seed_creates_every_scope_answer_row(self):
        seed_default_survey_lines(self.survey)
        for category, fields in SCOPE_FIELDS.items():
            existing = SiteSurveyScopeAnswer.objects.filter(
                survey=self.survey, category=category,
            )
            existing_keys = set(existing.values_list('field_key', flat=True))
            template_keys = {k for k, _ in fields}
            self.assertEqual(existing_keys, template_keys)

    def test_seed_creates_exact_shift_deployment_rows(self):
        seed_default_survey_lines(self.survey)
        rows = list(
            SiteSurveyShiftDeployment.objects
            .filter(survey=self.survey).order_by('sort_order')
            .values_list('description', 'line_type')
        )
        expected = [(d, t) for d, t in SHIFT_DEPLOYMENT_ROWS]
        self.assertEqual(rows, expected)

    def test_seed_creates_exact_location_rows(self):
        seed_default_survey_lines(self.survey)
        rows = list(
            SiteSurveyLocationLine.objects
            .filter(survey=self.survey).order_by('sort_order')
            .values_list('location_name', 'line_type')
        )
        self.assertEqual(rows, [(n, t) for n, t in LOCATION_ROWS])

    def test_seed_creates_exact_major_and_minor_equipment_rows(self):
        seed_default_survey_lines(self.survey)

        major = list(
            SiteSurveyEquipmentLine.objects
            .filter(survey=self.survey, equipment_category='major')
            .order_by('sort_order')
            .values_list('description', 'line_type', 'amortisation_months')
        )
        self.assertEqual(
            major,
            [(d, t, m) for d, t, m in MAJOR_EQUIPMENT_ROWS],
        )

        minor = list(
            SiteSurveyEquipmentLine.objects
            .filter(survey=self.survey, equipment_category='minor')
            .order_by('sort_order')
            .values_list('description', 'line_type', 'amortisation_months')
        )
        self.assertEqual(
            minor,
            [(d, t, m) for d, t, m in MINOR_EQUIPMENT_ROWS],
        )

    def test_seed_creates_exact_issue_rows_including_blank_improvement(self):
        seed_default_survey_lines(self.survey)
        rows = list(
            SiteSurveyIssueLine.objects
            .filter(survey=self.survey).order_by('sort_order')
            .values_list('issue', 'improvement_details')
        )
        expected = list(ISSUE_ROWS)
        self.assertEqual(rows, expected)
        # The last template issue has blank improvement_details — verify.
        self.assertTrue(any(imp == '' for _, imp in rows))


# ─── Group 2: Idempotency + non-destructive overwrite ─────────────────────────

class TestSeedIdempotency(TestCase):

    def setUp(self):
        self.org = _org('seed_idem')
        self.survey = _basic_survey(self.org)
        seed_default_survey_lines(self.survey)

    def _row_counts(self):
        return {
            'scope': SiteSurveyScopeAnswer.objects.filter(survey=self.survey).count(),
            'shift': SiteSurveyShiftDeployment.objects.filter(survey=self.survey).count(),
            'loc':   SiteSurveyLocationLine.objects.filter(survey=self.survey).count(),
            'eqp':   SiteSurveyEquipmentLine.objects.filter(survey=self.survey).count(),
            'iss':   SiteSurveyIssueLine.objects.filter(survey=self.survey).count(),
        }

    def test_seed_is_idempotent_no_duplicates(self):
        before = self._row_counts()
        seed_default_survey_lines(self.survey)
        seed_default_survey_lines(self.survey)
        self.assertEqual(self._row_counts(), before)

    def test_seed_does_not_overwrite_user_entered_values_by_default(self):
        # User enters data in template rows.
        scope = SiteSurveyScopeAnswer.objects.get(
            survey=self.survey, field_key='site_name',
        )
        scope.value_text = 'My Custom Site'
        scope.save()

        shift = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey, description='Helper',
        )
        shift.general_count = Decimal('5.00')
        shift.remarks = 'custom note'
        shift.save()

        eqp = SiteSurveyEquipmentLine.objects.get(
            survey=self.survey, equipment_category='major',
            description='Dry /wet Vacuum Cleaner',
        )
        eqp.unit_count = Decimal('3.00')
        eqp.amount = Decimal('15000.00')
        eqp.save()  # save() auto-computes total

        issue = SiteSurveyIssueLine.objects.get(
            survey=self.survey, issue='Garden area huge to maintain within staff',
        )
        issue.improvement_details = 'My override of the improvement'
        issue.save()

        seed_default_survey_lines(self.survey)  # default overwrite=False

        scope.refresh_from_db()
        shift.refresh_from_db()
        eqp.refresh_from_db()
        issue.refresh_from_db()

        self.assertEqual(scope.value_text, 'My Custom Site')
        self.assertEqual(shift.general_count, Decimal('5.00'))
        self.assertEqual(shift.remarks, 'custom note')
        self.assertEqual(eqp.unit_count, Decimal('3.00'))
        self.assertEqual(eqp.total, Decimal('45000.00'))
        self.assertEqual(issue.improvement_details, 'My override of the improvement')

    def test_seed_overwrite_true_refreshes_metadata_but_preserves_user_data(self):
        # User overrides metadata fields (field_label/sort_order/line_type) AND
        # user data fields (value_text/counts/remarks/improvement_details).
        scope = SiteSurveyScopeAnswer.objects.get(
            survey=self.survey, field_key='site_name',
        )
        scope.value_text = 'My Site Value'
        scope.field_label = 'TEMP HACK LABEL'
        scope.sort_order = 99
        scope.save()

        shift = SiteSurveyShiftDeployment.objects.get(
            survey=self.survey, description='Electrician',
        )
        shift.line_type = 'header'  # hacked
        shift.general_count = Decimal('7.00')
        shift.remarks = 'my note'
        shift.save()

        issue = SiteSurveyIssueLine.objects.get(
            survey=self.survey, issue='Common area cleaning and utility cleaning',
        )
        issue.improvement_details = 'User-entered improvement'
        issue.sort_order = 999
        issue.save()

        # User-added non-template row must survive.
        SiteSurveyShiftDeployment.objects.create(
            survey=self.survey, description='Custom Extra Row',
            sort_order=500, general_count=Decimal('2.00'),
        )

        seed_default_survey_lines(self.survey, overwrite=True)

        scope.refresh_from_db()
        shift.refresh_from_db()
        issue.refresh_from_db()

        # Metadata refreshed to template values.
        self.assertEqual(scope.field_label, 'Site Name')
        self.assertEqual(scope.sort_order, 1)
        self.assertEqual(shift.line_type, 'item')
        # User-entered values preserved.
        self.assertEqual(scope.value_text, 'My Site Value')
        self.assertEqual(shift.general_count, Decimal('7.00'))
        self.assertEqual(shift.remarks, 'my note')
        # improvement_details is user data → preserved.
        self.assertEqual(issue.improvement_details, 'User-entered improvement')
        # sort_order is template metadata → refreshed.
        self.assertEqual(issue.sort_order, len(ISSUE_ROWS))
        # User-added extra row not deleted.
        self.assertTrue(
            SiteSurveyShiftDeployment.objects
            .filter(survey=self.survey, description='Custom Extra Row').exists()
        )

    def test_seed_returns_counts(self):
        # Re-running on an already-seeded survey should report all-existing.
        counts = seed_default_survey_lines(self.survey)
        self.assertEqual(counts['scope_answers']['created'], 0)
        self.assertEqual(counts['shift_deployments']['created'], 0)
        self.assertEqual(counts['location_lines']['created'], 0)
        self.assertEqual(counts['equipment_lines']['created'], 0)
        self.assertEqual(counts['issue_lines']['created'], 0)


# ─── Group 3: structured + seed-default-lines API ─────────────────────────────

class TestStructuredApi(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('seed_api')
        cls.scope_node = _scope(cls.org)
        cls.user = _user(
            'sapi_user', cls.org,
            caps=[SALES_SURVEY_READ, SALES_SURVEY_UPDATE, SALES_LEAD_READ],
            scope_node=cls.scope_node,
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.survey = _basic_survey(self.org)

    def test_structured_endpoint_returns_grouped_payload(self):
        seed_default_survey_lines(self.survey)
        r = self.api.get(f'/api/sales/site-surveys/{self.survey.pk}/structured/')
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        for key in ('survey', 'scope_answers', 'shift_deployments',
                    'location_lines', 'equipment_lines', 'issue_lines'):
            self.assertIn(key, r.data)
        self.assertEqual(len(r.data['shift_deployments']), len(SHIFT_DEPLOYMENT_ROWS))
        self.assertEqual(len(r.data['location_lines']), len(LOCATION_ROWS))
        self.assertEqual(
            len(r.data['equipment_lines']),
            len(MAJOR_EQUIPMENT_ROWS) + len(MINOR_EQUIPMENT_ROWS),
        )

    def test_seed_default_lines_endpoint_creates_rows(self):
        r = self.api.post(
            f'/api/sales/site-surveys/{self.survey.pk}/seed-default-lines/',
            data={}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        self.assertEqual(r.data['detail'], 'seeded')
        self.assertEqual(
            SiteSurveyShiftDeployment.objects.filter(survey=self.survey).count(),
            len(SHIFT_DEPLOYMENT_ROWS),
        )

    def test_structured_requires_sales_survey_read(self):
        # User with no sales_survey.read should get 403.
        other_user = _user('no_caps_user', self.org, caps=[], scope_node=self.scope_node)
        client = APIClient()
        client.force_authenticate(other_user)
        r = client.get(f'/api/sales/site-surveys/{self.survey.pk}/structured/')
        self.assertEqual(r.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_seed_default_lines_requires_sales_survey_update(self):
        read_only_user = _user(
            'read_only_user', self.org, caps=[SALES_SURVEY_READ],
            scope_node=self.scope_node,
        )
        client = APIClient()
        client.force_authenticate(read_only_user)
        r = client.post(
            f'/api/sales/site-surveys/{self.survey.pk}/seed-default-lines/',
            data={}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_403_FORBIDDEN)


# ─── Group 4: org scoping ─────────────────────────────────────────────────────

class TestSurveyChildScoping(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org_a = _org('scope_a')
        cls.org_b = _org('scope_b')
        cls.scope_a = _scope(cls.org_a)
        cls.scope_b = _scope(cls.org_b)

        cls.user_a = _user(
            'scope_a_user', cls.org_a,
            caps=[SALES_SURVEY_READ, SALES_SURVEY_UPDATE],
            scope_node=cls.scope_a,
        )

        cls.survey_a = _basic_survey(cls.org_a)
        cls.survey_b = _basic_survey(cls.org_b)
        seed_default_survey_lines(cls.survey_a)
        seed_default_survey_lines(cls.survey_b)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user_a)

    def test_listing_returns_only_own_org_children(self):
        r = self.api.get('/api/sales/site-survey-shift-deployments/')
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        survey_ids = {row['survey'] for row in r.data.get('results', r.data)}
        self.assertNotIn(self.survey_b.pk, survey_ids)
        self.assertEqual(survey_ids, {self.survey_a.pk})

    def test_retrieving_other_org_child_returns_404(self):
        other_row = SiteSurveyShiftDeployment.objects.filter(
            survey=self.survey_b,
        ).first()
        r = self.api.get(f'/api/sales/site-survey-shift-deployments/{other_row.pk}/')
        self.assertEqual(r.status_code, http_status.HTTP_404_NOT_FOUND)


# ─── Group 5: equipment totals auto-compute ───────────────────────────────────

    def test_shift_deployment_api_calculates_total_and_defaults_description_from_role(self):
        role = JobRole.objects.create(
            org=self.org_a,
            name='Lift Operator',
            code='lift_operator',
            skill_category='skilled',
            is_active=True,
        )
        r = self.api.post(
            '/api/sales/site-survey-shift-deployments/',
            data={
                'survey': self.survey_a.pk,
                'job_role': role.pk,
                'general_count': '1.00',
                'first_shift_count': '2.00',
                'second_shift_count': '3.00',
                'night_shift_count': '4.00',
                'total_count': '999.00',
                'line_type': 'item',
                'sort_order': 500,
            },
            format='json',
        )

        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED, r.data)
        self.assertEqual(r.data['description'], 'Lift Operator')
        self.assertEqual(Decimal(r.data['total_count']), Decimal('10.00'))

        patch = self.api.patch(
            f"/api/sales/site-survey-shift-deployments/{r.data['id']}/",
            data={'night_shift_count': '5.00', 'total_count': '999.00'},
            format='json',
        )

        self.assertEqual(patch.status_code, http_status.HTTP_200_OK)
        self.assertEqual(Decimal(patch.data['total_count']), Decimal('11.00'))


class TestEquipmentTotals(TestCase):

    def setUp(self):
        self.org = _org('eqt')
        self.survey = _basic_survey(self.org)

    def test_item_total_auto_computes_on_save(self):
        line = SiteSurveyEquipmentLine.objects.create(
            survey=self.survey, equipment_category='major',
            description='Test Vacuum', line_type='item',
            unit_count=Decimal('3.00'), amount=Decimal('1250.00'),
        )
        self.assertEqual(line.total, Decimal('3750.00'))

        line.unit_count = Decimal('5')
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.total, Decimal('6250.00'))

    def test_aggregate_row_total_preserved(self):
        line = SiteSurveyEquipmentLine.objects.create(
            survey=self.survey, equipment_category='major',
            description='Cost of Amortisation', line_type='amortisation_cost',
            unit_count=Decimal('0'), amount=Decimal('0'),
            total=Decimal('99999.99'),
        )
        self.assertEqual(line.total, Decimal('99999.99'))
        line.total = Decimal('12345.00')
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.total, Decimal('12345.00'))


# ─── Group 6: Proposal calculation rule loading ───────────────────────────────

def _build_complete_ruleset(org, *, overrides=None, global_only=False):
    """
    Create a complete active ruleset (covering every RULE_KIND). Defaults mirror
    the existing constants so totals stay sensible. `overrides` is a
    {code: {field: value}} mapping to tweak specific rules.

    `global_only=True` creates the entire ruleset with org=None.
    """
    overrides = overrides or {}
    defaults = {
        'da': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('50.0000')},
        'hra': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('40.0000')},
        'washing': {'calculation_type': 'fixed', 'fixed_amount': Decimal('500.00')},
        'other_allowance': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('10.0000')},
        'ee_pf': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('12.0000')},
        'ee_esic': {'calculation_type': 'percent_of_gross', 'percentage': Decimal('0.7500')},
        'ee_pt': {'calculation_type': 'fixed', 'fixed_amount': Decimal('200.00')},
        'er_pf': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('13.0000')},
        'er_esic': {'calculation_type': 'percent_of_gross', 'percentage': Decimal('3.2500')},
        'bonus': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('8.3300')},
        'leave': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('4.8100')},
        'gratuity': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('4.8100')},
        'nh_fh': {'calculation_type': 'percent_of_basic', 'percentage': Decimal('2.0000')},
        'lwf': {'calculation_type': 'fixed', 'fixed_amount': Decimal('50.00')},
        'uniform': {'calculation_type': 'fixed', 'fixed_amount': Decimal('300.00')},
        'bgc': {'calculation_type': 'fixed', 'fixed_amount': Decimal('150.00')},
        'payroll_compliance': {'calculation_type': 'fixed', 'fixed_amount': Decimal('400.00')},
        'tools': {'calculation_type': 'fixed', 'fixed_amount': Decimal('250.00')},
    }

    target_org = None if global_only else org
    created = []
    for code, spec in defaults.items():
        spec = {**spec, **overrides.get(code, {})}
        rule = ProposalComponentRule.objects.create(
            org=target_org,
            code=code,
            component_name=code.upper(),
            component_type='earning',
            calculation_type=spec['calculation_type'],
            percentage=spec.get('percentage'),
            fixed_amount=spec.get('fixed_amount'),
            base_component_code=spec.get('base_component_code', ''),
            is_active=spec.get('is_active', True),
            effective_from=spec.get('effective_from'),
            effective_to=spec.get('effective_to'),
            sort_order=0,
        )
        created.append(rule)
    return created


class TestRuleLoading(TestCase):

    def setUp(self):
        self.org = _org('rules')

    def test_no_rules_returns_none(self):
        self.assertIsNone(_load_org_component_ruleset(self.org, date.today()))

    def test_no_rules_required_loader_raises(self):
        with self.assertRaises(ProposalComponentRulesNotConfigured) as ctx:
            _load_required_org_component_ruleset(self.org, date.today())
        self.assertIn('Missing active rules', str(ctx.exception))

    def test_partial_ruleset_returns_none(self):
        ProposalComponentRule.objects.create(
            org=self.org, code='da', component_name='DA',
            component_type='earning', calculation_type='percent_of_basic',
            percentage=Decimal('55.0000'), is_active=True,
        )
        self.assertIsNone(_load_org_component_ruleset(self.org, date.today()))

    def test_partial_ruleset_required_loader_raises_missing_codes(self):
        ProposalComponentRule.objects.create(
            org=self.org, code='da', component_name='DA',
            component_type='earning', calculation_type='percent_of_basic',
            percentage=Decimal('55.0000'), is_active=True,
        )
        with self.assertRaises(ProposalComponentRulesNotConfigured) as ctx:
            _load_required_org_component_ruleset(self.org, date.today())
        self.assertIn('hra', ctx.exception.missing_codes)
        self.assertNotIn('da', ctx.exception.missing_codes)

    def test_complete_ruleset_returns_dict(self):
        _build_complete_ruleset(self.org)
        ruleset = _load_org_component_ruleset(self.org, date.today())
        self.assertIsNotNone(ruleset)
        self.assertTrue(RULE_KINDS.issubset(ruleset.keys()))

    def test_inactive_rule_does_not_count_toward_completeness(self):
        rules = _build_complete_ruleset(self.org)
        # Deactivate one of them; ruleset must drop to None.
        deactivated = next(r for r in rules if r.code == 'bonus')
        deactivated.is_active = False
        deactivated.save(update_fields=['is_active', 'updated_at'])
        self.assertIsNone(_load_org_component_ruleset(self.org, date.today()))

    def test_out_of_date_rule_does_not_count(self):
        rules = _build_complete_ruleset(self.org)
        expired = next(r for r in rules if r.code == 'bonus')
        expired.effective_to = date.today() - timedelta(days=1)
        expired.save(update_fields=['effective_to', 'updated_at'])
        self.assertIsNone(_load_org_component_ruleset(self.org, date.today()))

    def test_global_rules_merge_with_org_rules(self):
        # Build a complete *global* ruleset (org=None).
        _build_complete_ruleset(self.org, global_only=True)
        ruleset = _load_org_component_ruleset(self.org, date.today())
        self.assertIsNotNone(ruleset)
        # All entries should be global.
        for rule in ruleset.values():
            self.assertIsNone(rule.org_id)

    def test_org_specific_rule_wins_over_global(self):
        _build_complete_ruleset(self.org, global_only=True)
        # Override 'da' with an org-specific rule.
        ProposalComponentRule.objects.create(
            org=self.org, code='da', component_name='DA-Local',
            component_type='earning', calculation_type='percent_of_basic',
            percentage=Decimal('99.0000'), is_active=True,
        )
        ruleset = _load_org_component_ruleset(self.org, date.today())
        self.assertEqual(ruleset['da'].percentage, Decimal('99.0000'))
        self.assertEqual(ruleset['da'].org_id, self.org.pk)

    def test_seed_default_component_rules_creates_complete_ruleset(self):
        counts = seed_default_proposal_component_rules(org=self.org)
        self.assertEqual(counts['created'], len(RULE_KINDS))
        ruleset = _load_required_org_component_ruleset(self.org, date.today())
        self.assertTrue(RULE_KINDS.issubset(ruleset.keys()))


# ─── Group 7: Proposal calculation behavior ───────────────────────────────────

class TestCalculationRules(TestCase):

    def setUp(self):
        self.org = _org('calc_h')
        self.user = User.objects.create_user(username='calc_h_user', password='pass')
        self.user.org = self.org
        self.user.save()

    def test_no_rules_proposal_generation_fails(self):
        lead, _ = _lead_with_one_requirement(self.org, self.user, monthly_wage=10000)
        with self.assertRaises(ProposalComponentRulesNotConfigured) as ctx:
            generate_proposal_version(lead, self.user)
        self.assertIn('Proposal component rules are not configured', str(ctx.exception))

    def test_complete_ruleset_overrides_constants(self):
        # Drop DA to 25% via a complete org ruleset.
        _build_complete_ruleset(self.org, overrides={
            'da': {'percentage': Decimal('25.0000')},
        })
        lead, _ = _lead_with_one_requirement(self.org, self.user, monthly_wage=10000)
        proposal = generate_proposal_version(lead, self.user)
        line = proposal.breakup_lines.get(component_name='DA')
        self.assertEqual(line.amount, Decimal('2500.00'))
        # Percentage column reflects the rule, not the default.
        self.assertEqual(line.percentage, Decimal('25.00'))

    def test_partial_ruleset_generation_fails(self):
        # Only 'da' configured — should NOT take effect.
        ProposalComponentRule.objects.create(
            org=self.org, code='da', component_name='DA',
            component_type='earning', calculation_type='percent_of_basic',
            percentage=Decimal('99.0000'), is_active=True,
        )
        lead, _ = _lead_with_one_requirement(self.org, self.user, monthly_wage=10000)
        with self.assertRaises(ProposalComponentRulesNotConfigured) as ctx:
            generate_proposal_version(lead, self.user)
        self.assertIn('hra', ctx.exception.missing_codes)

    def test_percent_of_other_uses_base_component(self):
        # Build complete ruleset with bonus = 10% of `da` (which is 50% of basic).
        _build_complete_ruleset(self.org, overrides={
            'bonus': {
                'calculation_type': 'percent_of_other',
                'percentage': Decimal('10.0000'),
                'base_component_code': 'da',
            },
        })
        lead, _ = _lead_with_one_requirement(self.org, self.user, monthly_wage=10000)
        proposal = generate_proposal_version(lead, self.user)
        # da = 5000 → bonus = 10% of 5000 = 500.
        bonus = proposal.breakup_lines.get(component_name='Bonus')
        self.assertEqual(bonus.amount, Decimal('500.00'))


# ─── Group 8: ProposalComponentRule API + validation ──────────────────────────

class TestComponentRuleApi(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('crule_api')
        cls.scope_node = _scope(cls.org)
        cls.user = _user(
            'crule_user', cls.org,
            caps=[SALES_PROPOSAL_READ, SALES_PROPOSAL_UPDATE],
            scope_node=cls.scope_node,
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_create_percent_of_basic_rule(self):
        r = self.api.post(
            '/api/sales/proposal-component-rules/',
            data={
                'org': self.org.pk,
                'code': 'da',
                'component_name': 'DA',
                'component_type': 'earning',
                'calculation_type': 'percent_of_basic',
                'percentage': '55.0000',
                'is_active': True,
            },
            format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED, r.data)
        self.assertEqual(r.data['code'], 'da')

    def test_create_fixed_rule(self):
        r = self.api.post(
            '/api/sales/proposal-component-rules/',
            data={
                'org': self.org.pk,
                'code': 'washing',
                'component_name': 'Washing Allowance',
                'component_type': 'earning',
                'calculation_type': 'fixed',
                'fixed_amount': '600.00',
                'is_active': True,
            },
            format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED, r.data)

    def test_validation_rejects_fixed_with_percentage_set(self):
        r = self.api.post(
            '/api/sales/proposal-component-rules/',
            data={
                'org': self.org.pk,
                'code': 'washing',
                'component_name': 'Washing',
                'component_type': 'earning',
                'calculation_type': 'fixed',
                'fixed_amount': '500.00',
                'percentage': '5.0000',
            },
            format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn('percentage', r.data)

    def test_validation_rejects_percent_of_other_without_base(self):
        r = self.api.post(
            '/api/sales/proposal-component-rules/',
            data={
                'org': self.org.pk,
                'code': 'bonus',
                'component_name': 'Bonus',
                'component_type': 'employer_contribution',
                'calculation_type': 'percent_of_other',
                'percentage': '10.0000',
            },
            format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn('base_component_code', r.data)

"""
apps/dashboard/tests/test_dashboard_charts_b.py

Phase Dashboard-Backend-B — Chart and drilldown data tests (15 tests).

B01. Backward compatibility — existing response fields still present + new chart keys added
B02. MRF charts.by_status counts match top-level counts
B03. MRF charts.by_site respects client scope (no cross-org sites)
B04. MRF charts.monthly_trend returns exactly 6 periods
B05. Onboarding charts.by_status and by_finalization counts correct
B06. Budget charts.utilization amounts match existing summary amounts
B07. Budget charts.by_nature matches existing by_nature field
B08. Hiring charts empty/all-zero for client role (no hiring_application.read)
B09. Hiring charts.by_stage and by_job_role keys present for internal user
B10. Talent charts empty for client role
B11. Talent charts.top_skills key is a list (scope respected)
B12. recent_activity items include url, target_type, target_id, subtitle
B13. Drilldown URL keys present and stable across mrf, onboarding, budget, hiring, talent
B14. Unauthenticated GET still returns 401 with chart-extended endpoint
B15. All chart/drilldown keys present even when section has no data (field_user)
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan, BudgetReservation
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest
from apps.mobilisation.models import MobilisationSetupRequest
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate, CandidateSkill

# Import the base class and helpers from the existing test module
from apps.dashboard.tests.test_dashboard_summary import DashboardBase

SUMMARY_URL = '/api/dashboard/summary/'


# ─── Test B01: Backward compatibility ────────────────────────────────────────

class TestBackwardCompatibility(DashboardBase):

    def test_B01_existing_fields_still_present_and_new_chart_keys_added(self):
        """Existing response fields unchanged; charts and drilldowns keys added to each section."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.data

        # Top-level keys unchanged
        for key in ('audience', 'user', 'scope', 'sections'):
            self.assertIn(key, data)

        sections = data['sections']
        for key in ('my_work', 'client_overview', 'onboarding', 'mrf',
                    'budget', 'hiring', 'talent', 'recent_activity'):
            self.assertIn(key, sections)

        # Existing MRF fields still present
        mrf = sections['mrf']
        for key in ('total', 'draft', 'in_review', 'approved', 'rejected', 'request_changes', 'recent'):
            self.assertIn(key, mrf, f"MRF missing existing key: {key}")
        # New keys
        self.assertIn('charts', mrf)
        self.assertIn('drilldowns', mrf)

        # Existing onboarding fields still present
        ob = sections['onboarding']
        for key in ('total', 'draft', 'in_review', 'approved', 'rejected', 'finalized', 'finalization_failed', 'recent'):
            self.assertIn(key, ob, f"Onboarding missing existing key: {key}")
        self.assertIn('charts', ob)
        self.assertIn('drilldowns', ob)

        # Existing budget fields still present
        budget = sections['budget']
        for key in ('plan_count', 'total_amount', 'reserved_amount', 'committed_amount', 'available_amount', 'by_nature'):
            self.assertIn(key, budget, f"Budget missing existing key: {key}")
        self.assertIn('charts', budget)
        self.assertIn('drilldowns', budget)

        # Existing hiring fields still present
        hiring = sections['hiring']
        for key in ('application_count', 'open_count', 'selected_count', 'joined_count', 'rejected_count', 'demand_count', 'latest_applications'):
            self.assertIn(key, hiring, f"Hiring missing existing key: {key}")
        self.assertIn('charts', hiring)
        self.assertIn('drilldowns', hiring)

        # Existing talent fields still present
        talent = sections['talent']
        for key in ('candidate_count', 'active_candidate_count', 'resume_count', 'manual_review_count', 'uploaded_count'):
            self.assertIn(key, talent, f"Talent missing existing key: {key}")
        self.assertIn('charts', talent)
        self.assertIn('drilldowns', talent)

        # client_overview has charts
        self.assertIn('charts', sections['client_overview'])


# ─── Tests B02–B04: MRF charts ───────────────────────────────────────────────

class TestMRFCharts(DashboardBase):

    def test_B02_mrf_by_status_counts_match_top_level(self):
        """charts.by_status counts align with top-level draft/in_review/approved/rejected."""
        self._make_mrf(status='draft')
        self._make_mrf(status='draft')
        self._make_mrf(status='hr_review')
        self._make_mrf(status='approved')

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        mrf = resp.data['sections']['mrf']

        by_status = {item['key']: item['count'] for item in mrf['charts']['by_status']}
        self.assertEqual(by_status['draft'], mrf['draft'])
        self.assertEqual(by_status['in_review'], mrf['in_review'])
        self.assertEqual(by_status['approved'], mrf['approved'])
        self.assertEqual(by_status['rejected'], mrf['rejected'])

    def test_B03_mrf_by_site_respects_scope(self):
        """charts.by_site only includes sites within the user's scope."""
        self._make_mrf(status='submitted')  # nova_site is in scope

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        by_site = resp.data['sections']['mrf']['charts']['by_site']

        # by_site must be a list
        self.assertIsInstance(by_site, list)
        # All site keys must be in scope (nova_site)
        site_keys = [item['key'] for item in by_site]
        self.assertIn(f'site:{self.nova_site.pk}', site_keys)
        # other_org sites must never appear
        for item in by_site:
            self.assertNotIn('other', item['label'].lower())

    def test_B04_mrf_monthly_trend_returns_6_periods(self):
        """charts.monthly_trend always returns exactly 6 period entries."""
        self._make_mrf(status='draft')

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        trend = resp.data['sections']['mrf']['charts']['monthly_trend']
        self.assertIsInstance(trend, list)
        self.assertEqual(len(trend), 6)
        # Each entry has period, label, count
        for entry in trend:
            self.assertIn('period', entry)
            self.assertIn('label', entry)
            self.assertIn('count', entry)
        # Current month must have count >= 1
        from django.utils import timezone
        now = timezone.now()
        current_period = f'{now.year:04d}-{now.month:02d}'
        current_entry = next((e for e in trend if e['period'] == current_period), None)
        self.assertIsNotNone(current_entry)
        self.assertGreaterEqual(current_entry['count'], 1)


# ─── Test B05: Onboarding charts ─────────────────────────────────────────────

class TestOnboardingCharts(DashboardBase):

    def test_B05_onboarding_by_status_and_by_finalization_counts_correct(self):
        """charts.by_status and by_finalization match expected counts."""
        # 2 in_review, 1 approved, 1 finalized, 1 failed finalization
        for _ in range(2):
            MobilisationSetupRequest.objects.create(
                org=self.org, requested_by=self.admin_user,
                mobilisation_type='new_client',
                status='in_review',
            )
        ob_approved = MobilisationSetupRequest.objects.create(
            org=self.org, requested_by=self.admin_user,
            mobilisation_type='new_client',
            status='approved', finalization_status='finalized',
        )
        ob_failed = MobilisationSetupRequest.objects.create(
            org=self.org, requested_by=self.admin_user,
            mobilisation_type='new_client',
            status='approved', finalization_status='failed',
        )

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        ob = resp.data['sections']['onboarding']

        by_status = {item['key']: item['count'] for item in ob['charts']['by_status']}
        self.assertGreaterEqual(by_status['in_review'], 2)
        self.assertGreaterEqual(by_status['approved'], 2)  # ob_approved + ob_failed both approved

        by_fin = {item['key']: item['count'] for item in ob['charts']['by_finalization']}
        self.assertIn('finalized', by_fin)
        self.assertIn('failed', by_fin)
        self.assertGreaterEqual(by_fin['finalized'], 1)
        self.assertGreaterEqual(by_fin['failed'], 1)

        # monthly_trend must have 6 periods
        self.assertEqual(len(ob['charts']['monthly_trend']), 6)


# ─── Tests B06–B07: Budget charts ────────────────────────────────────────────

class TestBudgetCharts(DashboardBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.bp_billable = BudgetPlan.objects.create(
            org=cls.org, name='Billable Plan', code='bp-bill-b',
            budget_nature='billable', budget_type='manpower',
            client=cls.nova_client,
            period_start=datetime.date(2026, 1, 1),
            amount='200000.00', currency='INR', status='active', is_active=True,
        )
        cls.bp_nonbill = BudgetPlan.objects.create(
            org=cls.org, name='Non-Bill Plan', code='bp-nonbill-b',
            budget_nature='non_billable', budget_type='general',
            period_start=datetime.date(2026, 1, 1),
            amount='50000.00', currency='INR', status='active', is_active=True,
        )
        cls.reservation = BudgetReservation.objects.create(
            org=cls.org, budget_plan=cls.bp_billable, mrf=ManpowerRequest.objects.create(
                org=cls.org, site=cls.nova_site, requested_by=cls.admin_user,
                mrf_type='new_hiring', billing_type='billable', status='submitted',
            ),
            amount='30000.00', status='reserved',
        )

    def test_B06_budget_utilization_amounts_match_summary(self):
        """charts.utilization committed+reserved+available match top-level amounts."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        budget = resp.data['sections']['budget']

        utilization = {item['key']: item['amount'] for item in budget['charts']['utilization']}
        self.assertIn('committed', utilization)
        self.assertIn('reserved', utilization)
        self.assertIn('available', utilization)

        self.assertEqual(utilization['reserved'], budget['reserved_amount'])
        self.assertEqual(utilization['committed'], budget['committed_amount'])
        self.assertEqual(utilization['available'], budget['available_amount'])

    def test_B07_budget_by_nature_chart_matches_existing_by_nature(self):
        """charts.by_nature amounts match the existing by_nature field."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        budget = resp.data['sections']['budget']

        chart_by_nature = {item['key']: item['amount'] for item in budget['charts']['by_nature']}
        self.assertEqual(chart_by_nature['billable'], budget['by_nature']['billable'])
        self.assertEqual(chart_by_nature['non_billable'], budget['by_nature']['non_billable'])


# ─── Tests B08–B09: Hiring charts ────────────────────────────────────────────

class TestHiringCharts(DashboardBase):

    def test_B08_hiring_charts_empty_for_client_role(self):
        """Client user (no hiring_application.read) gets empty hiring charts."""
        resp = self._api(self.client_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        hiring = resp.data['sections']['hiring']
        self.assertEqual(hiring['application_count'], 0)
        charts = hiring['charts']
        self.assertIn('by_status', charts)
        self.assertIn('by_stage', charts)
        self.assertIn('by_job_role', charts)
        # All chart lists must be empty
        self.assertEqual(charts['by_status'], [])
        self.assertEqual(charts['by_stage'], [])
        self.assertEqual(charts['by_job_role'], [])

    def test_B09_hiring_chart_keys_present_for_internal_user(self):
        """Admin user (hiring_application.read) gets chart keys as lists."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        charts = resp.data['sections']['hiring']['charts']
        self.assertIsInstance(charts['by_status'], list)
        self.assertIsInstance(charts['by_stage'], list)
        self.assertIsInstance(charts['by_job_role'], list)
        # by_status always has 4 items (open/selected/joined/rejected)
        self.assertEqual(len(charts['by_status']), 4)
        status_keys = {item['key'] for item in charts['by_status']}
        self.assertSetEqual(status_keys, {'open', 'selected', 'joined', 'rejected'})


# ─── Tests B10–B11: Talent charts ────────────────────────────────────────────

class TestTalentCharts(DashboardBase):

    def test_B10_talent_charts_empty_for_client_role(self):
        """Client user gets all-zero talent section with empty chart lists."""
        resp = self._api(self.client_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        talent = resp.data['sections']['talent']
        self.assertEqual(talent['candidate_count'], 0)
        charts = talent['charts']
        self.assertIn('by_resume_status', charts)
        self.assertIn('by_availability', charts)
        self.assertIn('top_skills', charts)
        self.assertEqual(charts['by_resume_status'], [])
        self.assertEqual(charts['top_skills'], [])

    def test_B11_talent_top_skills_is_list_and_scoped(self):
        """Admin user gets top_skills as a list; skills are from scoped candidates."""
        # Create a candidate + skill in admin's org
        candidate = Candidate.objects.create(
            org=self.org,
            phone='+919900001111',
            phone_normalized='919900001111',
            first_name='Test', last_name='Candidate',
        )
        CandidateSkill.objects.create(
            candidate=candidate,
            skill_name='python', normalized_skill_name='python',
        )

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        top_skills = resp.data['sections']['talent']['charts']['top_skills']
        self.assertIsInstance(top_skills, list)
        # The python skill we created should appear
        skill_labels = [s['label'] for s in top_skills]
        self.assertIn('python', skill_labels)


# ─── Test B12: recent_activity enhanced fields ────────────────────────────────

class TestRecentActivityEnhanced(DashboardBase):

    def test_B12_recent_activity_includes_url_target_type_target_id_subtitle(self):
        """Every recent_activity item has url, target_type, target_id, subtitle."""
        self._make_mrf(status='submitted')
        MobilisationSetupRequest.objects.create(
            org=self.org, requested_by=self.admin_user,
            mobilisation_type='new_client',
            status='draft',
        )

        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        activity = resp.data['sections']['recent_activity']
        self.assertGreater(len(activity), 0)

        for item in activity:
            self.assertIn('url', item, "Activity item missing 'url'")
            self.assertIn('target_type', item, "Activity item missing 'target_type'")
            self.assertIn('target_id', item, "Activity item missing 'target_id'")
            self.assertIn('subtitle', item, "Activity item missing 'subtitle'")
            # Existing fields still present
            self.assertIn('type', item)
            self.assertIn('id', item)
            self.assertIn('title', item)
            self.assertIn('status', item)
            self.assertIn('created_at', item)

        # MRF items: target_type = 'mrf', url starts with /mrf/
        mrf_items = [i for i in activity if i['type'] == 'mrf']
        for item in mrf_items:
            self.assertEqual(item['target_type'], 'mrf')
            self.assertTrue(item['url'].startswith('/mrf/'))

        # Mobilisation items: target_type = 'mobilisation', url starts with /mobilisation/
        ob_items = [i for i in activity if i['type'] == 'mobilisation']
        for item in ob_items:
            self.assertEqual(item['target_type'], 'mobilisation')
            self.assertTrue(item['url'].startswith('/mobilisation/'))


# ─── Test B13: Drilldown URL stability ───────────────────────────────────────

class TestDrilldownURLs(DashboardBase):

    def test_B13_drilldown_urls_present_and_stable(self):
        """drilldowns sub-object present with expected keys in mrf, onboarding, budget, hiring, talent."""
        resp = self._api(self.admin_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        sections = resp.data['sections']

        # MRF drilldowns
        mrf_dl = sections['mrf']['drilldowns']
        for key in ('all', 'draft', 'in_review', 'approved', 'rejected', 'request_changes'):
            self.assertIn(key, mrf_dl, f"MRF drilldowns missing key: {key}")
        self.assertEqual(mrf_dl['all'], '/mrf')
        self.assertIn('status=draft', mrf_dl['draft'])

        # Onboarding drilldowns
        ob_dl = sections['onboarding']['drilldowns']
        for key in ('all', 'draft', 'in_review', 'approved', 'rejected', 'finalization_failed'):
            self.assertIn(key, ob_dl, f"Onboarding drilldowns missing key: {key}")
        self.assertEqual(ob_dl['all'], '/mobilisation')

        # Budget drilldowns
        budget_dl = sections['budget']['drilldowns']
        for key in ('all', 'billable', 'non_billable'):
            self.assertIn(key, budget_dl, f"Budget drilldowns missing key: {key}")
        self.assertEqual(budget_dl['all'], '/budgets')

        # Hiring drilldowns
        hiring_dl = sections['hiring']['drilldowns']
        self.assertIn('all', hiring_dl)
        self.assertEqual(hiring_dl['all'], '/hiring-applications')

        # Talent drilldowns
        talent_dl = sections['talent']['drilldowns']
        for key in ('all', 'uploaded', 'manual_review'):
            self.assertIn(key, talent_dl, f"Talent drilldowns missing key: {key}")
        self.assertEqual(talent_dl['all'], '/candidates')


# ─── Test B14: Unauthenticated still 401 ─────────────────────────────────────

class TestUnauthenticatedCharts(DashboardBase):

    def test_B14_unauthenticated_still_401_with_chart_endpoint(self):
        """GET without authentication still returns 401 after chart additions."""
        resp = self._api().get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 401)


# ─── Test B15: Chart keys always present even with no data ───────────────────

class TestChartKeysAlwaysPresent(DashboardBase):

    def test_B15_chart_and_drilldown_keys_present_even_with_no_data(self):
        """
        field_user has minimal permissions (no mrf.read, budget.read, etc.).
        All section chart and drilldown keys must still be present even when all
        counts are zero and lists are empty.
        """
        resp = self._api(self.field_user).get(SUMMARY_URL)
        self.assertEqual(resp.status_code, 200)
        sections = resp.data['sections']

        # MRF: zero access → charts/drilldowns keys still present
        mrf = sections['mrf']
        self.assertIn('charts', mrf)
        self.assertIn('drilldowns', mrf)
        mrf_charts = mrf['charts']
        for key in ('by_status', 'by_site', 'by_department', 'monthly_trend'):
            self.assertIn(key, mrf_charts, f"MRF charts missing key: {key}")
            self.assertIsInstance(mrf_charts[key], list)

        # Onboarding: field_user has no client_onboarding.read
        ob = sections['onboarding']
        self.assertIn('charts', ob)
        self.assertIn('drilldowns', ob)
        ob_charts = ob['charts']
        for key in ('by_status', 'by_finalization', 'monthly_trend'):
            self.assertIn(key, ob_charts, f"Onboarding charts missing key: {key}")

        # Budget: field_user has no budget.read
        budget = sections['budget']
        self.assertIn('charts', budget)
        self.assertIn('drilldowns', budget)
        budget_charts = budget['charts']
        for key in ('utilization', 'by_nature', 'by_scope', 'top_plans'):
            self.assertIn(key, budget_charts, f"Budget charts missing key: {key}")

        # Hiring: field_user has no hiring_application.read
        hiring = sections['hiring']
        self.assertIn('charts', hiring)
        self.assertIn('drilldowns', hiring)

        # Talent: field_user has no candidate.read
        talent = sections['talent']
        self.assertIn('charts', talent)
        self.assertIn('drilldowns', talent)

        # client_overview: charts key present
        self.assertIn('charts', sections['client_overview'])

"""
apps/wages/tests/test_wage_apis.py

Tests for Phase 4F:
  - LocationArea CRUD
  - WageCategory CRUD (writable)
  - MinimumWageRate CRUD (writable, soft-delete, capability gates)
  - Wage lookup service (priority order, expiry, fallback)
  - Lookup endpoint
  - SiteRoleRequirement wage auto-fill + snapshot
  - MRF line item snapshot still works after SRR changes
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Organization, ScopeNode
from apps.access.models import AccessRole, UserRoleAssignment
from apps.jobs.models import JobRole
from apps.wages.models import LocationArea, WageCategory, MinimumWageRate
from apps.wages.services import get_applicable_minimum_wage
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.access.capabilities import WAGE_READ, WAGE_CREATE, WAGE_UPDATE, WAGE_DELETE
from apps.access.tests.utils import bootstrap_role_permissions


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_user(org, role_code, scope_node, username=None):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    uname = username or f'user_{role_code}_{org.code}'
    user, _ = User.objects.get_or_create(
        username=uname,
        defaults={'org': org, 'is_active': True},
    )
    role, _ = AccessRole.objects.get_or_create(
        org=org, code=role_code,
        defaults={'name': role_code.replace('_', ' ').title()},
    )
    UserRoleAssignment.objects.get_or_create(user=user, role=role, scope_node=scope_node)
    bootstrap_role_permissions(role)
    return user


def make_superuser(username='superuser_wages'):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    u, _ = User.objects.get_or_create(
        username=username,
        defaults={'is_superuser': True, 'is_staff': True},
    )
    return u


# ─── Base setup ───────────────────────────────────────────────────────────────

class WageTestBase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(code='wtest', name='Wage Test Org', is_active=True)
        self.company_node = ScopeNode.objects.create(
            org=self.org, code='wtest', name='Wage Test Org', node_type='company',
            depth=0, path='wtest', is_active=True,
        )
        self.finance_user = make_user(self.org, 'finance', self.company_node)
        self.hr_user = make_user(self.org, 'hr_admin', self.company_node)
        self.superuser = make_superuser()

        self.unskilled = WageCategory.objects.create(name='Unskilled', code='unskilled_w')
        self.skilled = WageCategory.objects.create(name='Skilled', code='skilled_w')

        self.housekeeping = JobRole.objects.create(
            org=self.org, name='Housekeeping', code='hk_w', skill_category='unskilled',
        )

        self.maharashtra = LocationArea.objects.create(
            name='Maharashtra', code='mh', area_type='state', state_name='Maharashtra',
        )
        self.west_region = LocationArea.objects.create(
            name='West Region', code='west', area_type='region',
            parent=self.maharashtra, state_name='Maharashtra',
        )
        self.mumbai = LocationArea.objects.create(
            name='Mumbai', code='mumbai', area_type='city',
            parent=self.west_region, state_name='Maharashtra',
        )

        self.client_obj = Client.objects.create(
            org=self.org, name='Test Client', code='tc-w',
        )
        self.site = SiteProfile.objects.create(
            org=self.org, client=self.client_obj,
            name='Mumbai Site', code='ms-w',
            city='Mumbai', state='Maharashtra',
        )

        self.client = APIClient()


# ─── A. LocationArea API ──────────────────────────────────────────────────────

class TestLocationAreaAPI(WageTestBase):
    def test_list_requires_wage_read(self):
        # hr_admin has wage.read
        self.client.force_authenticate(self.hr_user)
        r = self.client.get('/api/wages/locations/')
        self.assertEqual(r.status_code, 200)

    def test_list_no_auth_returns_401(self):
        r = self.client.get('/api/wages/locations/')
        self.assertEqual(r.status_code, 401)

    def test_create_location_finance_user(self):
        self.client.force_authenticate(self.finance_user)
        r = self.client.post('/api/wages/locations/', {
            'name': 'Pune', 'code': 'pune-t', 'area_type': 'city',
            'parent': self.west_region.pk, 'state_name': 'Maharashtra',
        })
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['name'], 'Pune')

    def test_create_location_duplicate_code_under_same_parent_returns_400(self):
        self.client.force_authenticate(self.finance_user)
        self.client.post('/api/wages/locations/', {
            'name': 'Dup', 'code': 'dup-t', 'area_type': 'city',
            'parent': self.west_region.pk,
        })
        r = self.client.post('/api/wages/locations/', {
            'name': 'Dup2', 'code': 'dup-t', 'area_type': 'city',
            'parent': self.west_region.pk,
        })
        self.assertEqual(r.status_code, 400)

    def test_soft_delete_location(self):
        loc = LocationArea.objects.create(
            name='Del', code='del-t', area_type='city', parent=self.west_region,
        )
        self.client.force_authenticate(self.finance_user)
        r = self.client.delete(f'/api/wages/locations/{loc.pk}/')
        self.assertEqual(r.status_code, 204)
        loc.refresh_from_db()
        self.assertFalse(loc.is_active)

    def test_create_without_wage_create_returns_403(self):
        # client_admin has no wage.create
        user = make_user(self.org, 'client_admin', self.company_node, 'ca_loc')
        self.client.force_authenticate(user)
        r = self.client.post('/api/wages/locations/', {
            'name': 'X', 'code': 'x-t', 'area_type': 'city',
        })
        self.assertEqual(r.status_code, 403)


# ─── B. WageCategory API ─────────────────────────────────────────────────────

class TestWageCategoryAPI(WageTestBase):
    def test_list_returns_categories(self):
        self.client.force_authenticate(self.hr_user)
        r = self.client.get('/api/wages/categories/')
        self.assertEqual(r.status_code, 200)
        codes = [c['code'] for c in r.data['results']]
        self.assertIn('unskilled_w', codes)

    def test_create_category_with_wage_create(self):
        self.client.force_authenticate(self.finance_user)
        r = self.client.post('/api/wages/categories/', {
            'name': 'Highly Skilled', 'code': 'highly_skilled_w',
        })
        self.assertEqual(r.status_code, 201)

    def test_create_duplicate_code_returns_400(self):
        self.client.force_authenticate(self.finance_user)
        r = self.client.post('/api/wages/categories/', {
            'name': 'Dup', 'code': 'unskilled_w',
        })
        self.assertEqual(r.status_code, 400)

    def test_patch_category(self):
        self.client.force_authenticate(self.finance_user)
        r = self.client.patch(f'/api/wages/categories/{self.unskilled.pk}/', {
            'description': 'Updated',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['description'], 'Updated')

    def test_missing_wage_read_returns_403(self):
        # field_supervisor has no wage.read
        user = make_user(self.org, 'field_supervisor', self.company_node, 'fs_cat')
        self.client.force_authenticate(user)
        r = self.client.get('/api/wages/categories/')
        self.assertEqual(r.status_code, 403)


# ─── C. MinimumWageRate API ───────────────────────────────────────────────────

class TestMinimumWageRateAPI(WageTestBase):
    def _create_rate(self, user=None, **kwargs):
        user = user or self.finance_user
        self.client.force_authenticate(user)
        payload = {
            'org': self.org.pk,
            'location': self.mumbai.pk,
            'wage_category': self.unskilled.pk,
            'role': self.housekeeping.pk,
            'monthly_wage': '10000.00',
            'effective_from': '2025-01-01',
            'is_active': True,
        }
        payload.update(kwargs)
        return self.client.post('/api/wages/rates/', payload)

    def test_create_rate_with_wage_create(self):
        r = self._create_rate()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['role'], self.housekeeping.pk)
        self.assertIn('role_name', r.data)

    def test_role_field_in_serializer(self):
        r = self._create_rate()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['role_name'], self.housekeeping.name)
        self.assertEqual(r.data['role_code'], self.housekeeping.code)

    def test_create_without_wage_create_returns_403(self):
        r = self._create_rate(user=self.hr_user)
        # hr_admin has wage.read but not wage.create
        self.assertEqual(r.status_code, 403)

    def test_update_rate_with_wage_update(self):
        r = self._create_rate()
        pk = r.data['id']
        self.client.force_authenticate(self.finance_user)
        r2 = self.client.patch(f'/api/wages/rates/{pk}/', {'monthly_wage': '11000.00'})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data['monthly_wage'], '11000.00')

    def test_soft_delete_sets_is_active_false(self):
        r = self._create_rate()
        pk = r.data['id']
        self.client.force_authenticate(self.finance_user)
        r2 = self.client.delete(f'/api/wages/rates/{pk}/')
        self.assertEqual(r2.status_code, 204)
        rate = MinimumWageRate.objects.get(pk=pk)
        self.assertFalse(rate.is_active)

    def test_effective_to_before_effective_from_returns_400(self):
        r = self._create_rate(effective_from='2025-06-01', effective_to='2025-01-01')
        self.assertEqual(r.status_code, 400)
        self.assertIn('effective_to', str(r.data))

    def test_duplicate_active_rate_blocked(self):
        self._create_rate()
        r = self._create_rate()
        self.assertEqual(r.status_code, 400)
        self.assertIn('already exists', str(r.data))

    def test_zero_monthly_wage_returns_400(self):
        r = self._create_rate(monthly_wage='0.00')
        self.assertEqual(r.status_code, 400)

    def test_missing_wage_read_returns_403(self):
        user = make_user(self.org, 'field_supervisor', self.company_node, 'fs_rate')
        self.client.force_authenticate(user)
        r = self.client.get('/api/wages/rates/')
        self.assertEqual(r.status_code, 403)

    def test_delete_without_wage_delete_returns_403(self):
        r = self._create_rate()
        pk = r.data['id']
        # hr_admin has no wage.delete
        self.client.force_authenticate(self.hr_user)
        r2 = self.client.delete(f'/api/wages/rates/{pk}/')
        self.assertEqual(r2.status_code, 403)

    def test_superuser_can_create_and_delete(self):
        r = self._create_rate(user=self.superuser)
        self.assertEqual(r.status_code, 201)
        pk = r.data['id']
        self.client.force_authenticate(self.superuser)
        r2 = self.client.delete(f'/api/wages/rates/{pk}/')
        self.assertEqual(r2.status_code, 204)


# ─── D. Wage Lookup Service ───────────────────────────────────────────────────

class TestWageLookupService(WageTestBase):
    def _make_rate(self, location=None, state='', city=None, role=None, monthly=10000,
                   effective_from=None, effective_to=None, is_active=True):
        return MinimumWageRate.objects.create(
            org=self.org,
            location=location,
            state=state,
            city=city,
            wage_category=self.unskilled,
            role=role,
            monthly_wage=monthly,
            daily_wage=None,
            effective_from=effective_from or datetime.date(2025, 1, 1),
            effective_to=effective_to,
            is_active=is_active,
        )

    def test_role_city_beats_generic_city(self):
        self._make_rate(location=self.mumbai, monthly=10000)             # generic
        self._make_rate(location=self.mumbai, role=self.housekeeping, monthly=12000)  # role-specific
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, role=self.housekeeping, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 12000)

    def test_role_parent_fallback(self):
        # Rate at state (parent of west_region which is parent of mumbai)
        self._make_rate(location=self.maharashtra, role=self.housekeeping, monthly=9000)
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, role=self.housekeeping, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 9000)

    def test_generic_city_fallback(self):
        self._make_rate(location=self.mumbai, monthly=8000)  # no role
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 8000)

    def test_generic_state_fallback(self):
        self._make_rate(location=self.maharashtra, monthly=7000)
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 7000)

    def test_expired_rate_ignored(self):
        self._make_rate(location=self.mumbai, monthly=9999,
                        effective_from=datetime.date(2024, 1, 1),
                        effective_to=datetime.date(2024, 12, 31))
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, org=self.org,
        )
        self.assertIsNone(result)

    def test_latest_effective_from_wins(self):
        self._make_rate(location=self.mumbai, monthly=9000,
                        effective_from=datetime.date(2024, 1, 1))
        self._make_rate(location=self.mumbai, monthly=10500,
                        effective_from=datetime.date(2025, 1, 1))
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            location=self.mumbai, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 10500)

    def test_legacy_state_city_fallback(self):
        self._make_rate(state='Maharashtra', city='Mumbai', monthly=9500)
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            state='Maharashtra', city='Mumbai', org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 9500)

    def test_no_match_returns_none(self):
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            state='UnknownState', org=self.org,
        )
        self.assertIsNone(result)

    def test_site_derives_state_city(self):
        self._make_rate(state='Maharashtra', city='Mumbai', monthly=9600)
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            site=self.site, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 9600)

    def test_site_resolves_location_area_before_legacy_state_city(self):
        self._make_rate(state='Maharashtra', city='Mumbai', monthly=9600)
        self._make_rate(location=self.mumbai, role=self.housekeeping, monthly=12500)
        result = get_applicable_minimum_wage(
            self.unskilled, datetime.date(2025, 6, 1),
            site=self.site, role=self.housekeeping, org=self.org,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_wage, 12500)


# ─── E. Lookup Endpoint ───────────────────────────────────────────────────────

class TestWageLookupEndpoint(WageTestBase):
    def setUp(self):
        super().setUp()
        self.rate = MinimumWageRate.objects.create(
            org=self.org,
            location=self.mumbai,
            wage_category=self.unskilled,
            role=self.housekeeping,
            monthly_wage=11000,
            effective_from=datetime.date(2025, 1, 1),
            is_active=True,
        )

    def test_lookup_returns_rate(self):
        self.client.force_authenticate(self.hr_user)
        r = self.client.get('/api/wages/rates/lookup/', {
            'wage_category': self.unskilled.pk,
            'job_role': self.housekeeping.pk,
            'location': self.mumbai.pk,
            'date': '2025-06-01',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['monthly_wage'], '11000.00')

    def test_lookup_missing_wage_category_returns_400(self):
        self.client.force_authenticate(self.hr_user)
        r = self.client.get('/api/wages/rates/lookup/')
        self.assertEqual(r.status_code, 400)

    def test_lookup_no_match_returns_404(self):
        self.client.force_authenticate(self.hr_user)
        r = self.client.get('/api/wages/rates/lookup/', {
            'wage_category': self.skilled.pk,
            'date': '2025-06-01',
        })
        self.assertEqual(r.status_code, 404)

    def test_lookup_without_wage_read_returns_403(self):
        user = make_user(self.org, 'field_supervisor', self.company_node, 'fs_lookup')
        self.client.force_authenticate(user)
        r = self.client.get('/api/wages/rates/lookup/', {
            'wage_category': self.unskilled.pk,
        })
        self.assertEqual(r.status_code, 403)


# ─── F. SiteRoleRequirement wage auto-fill ────────────────────────────────────

class TestSiteRoleRequirementWageIntegration(WageTestBase):
    def setUp(self):
        super().setUp()
        # Scope nodes for site hierarchy
        client_node = ScopeNode.objects.create(
            org=self.org, code='cl-w', name='Client', node_type='client',
            parent=self.company_node, depth=1, path='wtest/cl-w', is_active=True,
        )
        site_node = ScopeNode.objects.create(
            org=self.org, code='ms-w', name='Site', node_type='site',
            parent=client_node, depth=2, path='wtest/cl-w/ms-w', is_active=True,
        )
        self.site.scope_node = site_node
        self.site.save(update_fields=['scope_node'])

        self.rate = MinimumWageRate.objects.create(
            org=self.org,
            location=self.mumbai,
            wage_category=self.unskilled,
            role=self.housekeeping,
            monthly_wage=10000,
            effective_from=datetime.date(2025, 1, 1),
            is_active=True,
            source_note='Test rate',
        )

        # Sales manager has site_role_requirement.create; scoped to company node
        self.sm_user = make_user(self.org, 'sales_manager', self.company_node, 'sm_srr_w')

    def _create_srr(self, extra=None):
        payload = {
            'site': self.site.pk,
            'job_role': self.housekeeping.pk,
            'approved_headcount': 5,
            'billing_type': 'billable',
            'wage_category': self.unskilled.pk,
            'effective_from': '2025-01-01',
        }
        if extra:
            payload.update(extra)
        self.client.force_authenticate(self.sm_user)
        return self.client.post('/api/sites/role-requirements/', payload)

    def test_create_srr_auto_fills_wage_min_from_master(self):
        r = self._create_srr()
        self.assertEqual(r.status_code, 201, r.data)
        srr = SiteRoleRequirement.objects.get(pk=r.data['id'])
        self.assertEqual(srr.wage_rate_id, self.rate.id)
        self.assertEqual(srr.wage_min, self.rate.monthly_wage)
        self.assertEqual(srr.wage_rate_monthly_snapshot, self.rate.monthly_wage)
        self.assertEqual(srr.wage_rate_source_snapshot, 'Test rate')

    def test_create_srr_manual_wage_min_not_overwritten(self):
        r = self._create_srr({'wage_min': '15000.00'})
        self.assertEqual(r.status_code, 201, r.data)
        srr = SiteRoleRequirement.objects.get(pk=r.data['id'])
        self.assertEqual(srr.wage_min, 15000)   # manual value preserved

    def test_create_srr_stores_wage_rate_snapshot(self):
        # Create rate using legacy state/city so the lookup can find it via site
        from apps.wages.models import MinimumWageRate as MWR
        MWR.objects.create(
            org=self.org,
            state='Maharashtra', city='Mumbai',
            wage_category=self.unskilled,
            role=self.housekeeping,
            monthly_wage=10000,
            effective_from=datetime.date(2025, 1, 1),
            is_active=True,
            source_note='Legacy rate',
        )
        r = self._create_srr()
        self.assertEqual(r.status_code, 201, r.data)
        srr = SiteRoleRequirement.objects.get(pk=r.data['id'])
        self.assertIsNotNone(srr.wage_rate_id)
        self.assertEqual(srr.wage_rate_effective_from_snapshot, datetime.date(2025, 1, 1))
        self.assertEqual(srr.wage_min, 10000)  # auto-filled

    def test_create_srr_no_wage_master_still_creates_if_wage_min_provided(self):
        # Use skilled category for which no rate exists
        r = self._create_srr({
            'wage_category': self.skilled.pk,
            'wage_min': '18000.00',
        })
        self.assertEqual(r.status_code, 201, r.data)
        srr = SiteRoleRequirement.objects.get(pk=r.data['id'])
        self.assertEqual(srr.wage_min, 18000)
        self.assertIsNone(srr.wage_rate_id)

    def test_patch_unrelated_field_does_not_overwrite_manual_wage_min(self):
        r = self._create_srr({'wage_min': '15000.00'})
        self.assertEqual(r.status_code, 201, r.data)
        pk = r.data['id']
        self.client.force_authenticate(self.sm_user)
        r2 = self.client.patch(f'/api/sites/role-requirements/{pk}/', {
            'approved_headcount': 8,
        })
        self.assertEqual(r2.status_code, 200, r2.data)
        srr = SiteRoleRequirement.objects.get(pk=pk)
        self.assertEqual(srr.wage_min, 15000)  # unchanged


# ─── G. MRF Line Item Snapshot Compatibility ─────────────────────────────────

class TestMRFLineItemSnapshotCompat(WageTestBase):
    def setUp(self):
        super().setUp()
        self.srr = SiteRoleRequirement.objects.create(
            site=self.site,
            job_role=self.housekeeping,
            approved_headcount=10,
            billing_type='billable',
            wage_category=self.unskilled,
            effective_from=datetime.date(2025, 1, 1),
            wage_min=10500,
            wage_max=13000,
        )

        # hr_admin scoped to company_node (already created in WageTestBase.setUp)
        self.mrf_user = make_user(self.org, 'hr_admin', self.company_node, 'hr_mrf_snap')

        client_node = ScopeNode.objects.create(
            org=self.org, code='cl-mrf-w', name='Client', node_type='client',
            parent=self.company_node, depth=1, path='wtest/cl-mrf-w', is_active=True,
        )
        site_node = ScopeNode.objects.create(
            org=self.org, code='ms-mrf-w', name='Site', node_type='site',
            parent=client_node, depth=2, path='wtest/cl-mrf-w/ms-mrf-w', is_active=True,
        )
        self.site.scope_node = site_node
        self.site.save(update_fields=['scope_node'])

    def test_mrf_line_item_snapshots_srr_wage_min(self):
        self.client.force_authenticate(self.mrf_user)
        mrf_r = self.client.post('/api/mrf/requests/', {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'requested_by_type': 'internal',
        })
        self.assertEqual(mrf_r.status_code, 201)
        mrf_id = mrf_r.data['id']

        li_r = self.client.post('/api/mrf/line-items/', {
            'mrf': mrf_id,
            'site_role_requirement': self.srr.pk,
            'job_role': self.housekeeping.pk,
            'headcount': 3,
            'wage_category': self.unskilled.pk,
        })
        self.assertEqual(li_r.status_code, 201)
        self.assertEqual(li_r.data['min_wage_snapshot'], '10500.00')

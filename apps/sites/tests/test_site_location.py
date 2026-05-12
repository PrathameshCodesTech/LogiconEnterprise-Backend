"""
apps/sites/tests/test_site_location.py

Phase 4H — SiteProfile.location_area FK integration tests.

Scenarios:
  1. Create site with location_area → FK set, city/state auto-filled
  2. Create site with location_area + explicit city/state → explicit values kept
  3. Create site without location_area → city/state untouched
  4. PATCH site to set location_area → blank city/state auto-filled
  5. PATCH site to clear location_area → existing city/state retained
  6. Inactive location_area on create → 400
  7. Inactive location_area on PATCH → 400
  8. Wage lookup: site.location_area FK used directly (no text resolution)
  9. Wage lookup: site without location_area falls back to text resolution
  10. apply_location_snapshots helper unit test (city + state auto-fill)
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.core.models import Organization, ScopeNode
from apps.sites.models import Client, SiteProfile
from apps.sites.services import apply_location_snapshots
from apps.wages.models import LocationArea, WageCategory, MinimumWageRate


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass',
                                 is_superuser=is_superuser, is_staff=is_superuser)
    if org:
        u.org = org
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


# ─── Base ─────────────────────────────────────────────────────────────────────

class SiteLocationTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Test Org SL', code='sl-org')

        # Scope tree
        cls.n_company = _node(cls.org, 'sl-org', 'company', None, 0, 'sl-org')
        cls.n_client = _node(cls.org, 'sl-client', 'client', cls.n_company, 1, 'sl-org/sl-client')

        # Roles & users
        cls.role_admin = _role(cls.org, 'admin')
        cls.admin = _user('sl_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)
        cls.superuser = _user('sl_super', is_superuser=True)

        # Client (named sl_client to avoid shadowing Django TestCase.client)
        cls.sl_client = Client.objects.create(
            org=cls.org, name='SL Client', code='sl-client',
            scope_node=cls.n_client, is_active=True,
        )

        # LocationArea tree: Maharashtra → West Region → Mumbai
        cls.maharashtra = LocationArea.objects.create(
            parent=None, code='sl-mh', name='Maharashtra',
            area_type='state', state_name='Maharashtra', is_active=True,
        )
        cls.west = LocationArea.objects.create(
            parent=cls.maharashtra, code='sl-west', name='West Region',
            area_type='region', state_name='Maharashtra', is_active=True,
        )
        cls.mumbai = LocationArea.objects.create(
            parent=cls.west, code='sl-mumbai', name='Mumbai',
            area_type='city', state_name='Maharashtra', is_active=True,
        )
        cls.inactive_loc = LocationArea.objects.create(
            parent=None, code='sl-inactive', name='Inactive City',
            area_type='city', state_name='', is_active=False,
        )

        # Wage category + rate anchored at Mumbai
        cls.wage_cat = WageCategory.objects.create(name='SL Unskilled', code='sl-unskilled')
        cls.wage_rate = MinimumWageRate.objects.create(
            org=cls.org,
            location=cls.mumbai,
            wage_category=cls.wage_cat,
            monthly_wage=10000,
            daily_wage=385,
            effective_from=datetime.date(2025, 1, 1),
            is_active=True,
        )

    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _site_url(self, pk=None):
        if pk:
            return f'/api/sites/profiles/{pk}/'
        return '/api/sites/profiles/'


# ─── API Tests ────────────────────────────────────────────────────────────────

class SiteCreateWithLocationAreaTest(SiteLocationTestBase):
    """1. Create with location_area → FK set, city/state auto-filled from LocationArea."""

    def test_create_sets_location_area_and_autofills_city_state(self):
        api = self._api(self.admin)
        resp = api.post(self._site_url(), {
            'client': self.sl_client.pk,
            'name': 'Test Site 1',
            'code': 'sl-ts1',
            'location_area': self.mumbai.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        data = resp.data
        self.assertEqual(data['location_area'], self.mumbai.pk)
        self.assertEqual(data['location_area_name'], 'Mumbai')
        self.assertEqual(data['location_area_type'], 'city')
        self.assertEqual(data['city'], 'Mumbai')
        self.assertEqual(data['state'], 'Maharashtra')


class SiteCreateExplicitCityStateTest(SiteLocationTestBase):
    """2. Explicit city/state + location_area → explicit values kept (not overwritten)."""

    def test_explicit_city_state_not_overwritten(self):
        api = self._api(self.admin)
        resp = api.post(self._site_url(), {
            'client': self.sl_client.pk,
            'name': 'Test Site 2',
            'code': 'sl-ts2',
            'location_area': self.mumbai.pk,
            'city': 'Thane',
            'state': 'Maharashtra',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        # city was explicitly provided → apply_location_snapshots skips it
        self.assertEqual(resp.data['city'], 'Thane')
        self.assertEqual(resp.data['state'], 'Maharashtra')
        self.assertEqual(resp.data['location_area'], self.mumbai.pk)


class SiteCreateNoLocationAreaTest(SiteLocationTestBase):
    """3. Create without location_area → city/state fields untouched."""

    def test_no_location_area_city_state_untouched(self):
        api = self._api(self.admin)
        resp = api.post(self._site_url(), {
            'client': self.sl_client.pk,
            'name': 'Test Site 3',
            'code': 'sl-ts3',
            'city': 'Pune',
            'state': 'Maharashtra',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['location_area'])
        self.assertEqual(resp.data['city'], 'Pune')
        self.assertEqual(resp.data['state'], 'Maharashtra')


class SitePatchSetLocationAreaTest(SiteLocationTestBase):
    """4. PATCH to set location_area → blank city/state auto-filled."""

    def setUp(self):
        self.site = SiteProfile.objects.create(
            org=self.org, client=self.sl_client, scope_node=self.n_client,
            name='Patch Site', code='sl-patch1', city='', state='', is_active=True,
        )

    def test_patch_sets_location_and_autofills_city_state(self):
        api = self._api(self.admin)
        resp = api.patch(self._site_url(self.site.pk), {
            'location_area': self.mumbai.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['location_area'], self.mumbai.pk)
        self.assertEqual(resp.data['city'], 'Mumbai')
        self.assertEqual(resp.data['state'], 'Maharashtra')


class SitePatchClearLocationAreaTest(SiteLocationTestBase):
    """5. PATCH to clear location_area → existing city/state retained."""

    def setUp(self):
        self.site = SiteProfile.objects.create(
            org=self.org, client=self.sl_client, scope_node=self.n_client,
            name='Clear Site', code='sl-clear1', location_area=self.mumbai,
            city='Mumbai', state='Maharashtra', is_active=True,
        )

    def test_clear_location_area_retains_city_state(self):
        api = self._api(self.admin)
        resp = api.patch(self._site_url(self.site.pk), {
            'location_area': None,
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['location_area'])
        # city/state were already set — not cleared by the patch
        self.assertEqual(resp.data['city'], 'Mumbai')
        self.assertEqual(resp.data['state'], 'Maharashtra')


class SiteInactiveLocationAreaCreateTest(SiteLocationTestBase):
    """6. Inactive location_area on create → 400."""

    def test_inactive_location_area_rejected_on_create(self):
        api = self._api(self.admin)
        resp = api.post(self._site_url(), {
            'client': self.sl_client.pk,
            'name': 'Bad Site',
            'code': 'sl-bad1',
            'location_area': self.inactive_loc.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('location_area', resp.data)


class SiteInactiveLocationAreaPatchTest(SiteLocationTestBase):
    """7. Inactive location_area on PATCH → 400."""

    def setUp(self):
        self.site = SiteProfile.objects.create(
            org=self.org, client=self.sl_client, scope_node=self.n_client,
            name='Patch Bad', code='sl-pbad1', is_active=True,
        )

    def test_inactive_location_area_rejected_on_patch(self):
        api = self._api(self.admin)
        resp = api.patch(self._site_url(self.site.pk), {
            'location_area': self.inactive_loc.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('location_area', resp.data)


# ─── Wage Lookup Integration ──────────────────────────────────────────────────

class WageLookupViaLocationAreaFKTest(SiteLocationTestBase):
    """8. Wage lookup uses site.location_area FK directly (not text resolution)."""

    def test_lookup_uses_location_area_fk(self):
        from apps.wages.services import get_applicable_minimum_wage

        # Site has location_area FK pointing to Mumbai, but city text is something else
        site = SiteProfile.objects.create(
            org=self.org, client=self.sl_client, scope_node=self.n_client,
            name='FK Site', code='sl-fk1',
            location_area=self.mumbai,
            city='DifferentCity',  # text that would NOT match Mumbai via text resolution
            state='Maharashtra', is_active=True,
        )
        rate = get_applicable_minimum_wage(
            self.wage_cat,
            datetime.date(2025, 6, 1),
            site=site,
            org=self.org,
        )
        self.assertIsNotNone(rate)
        self.assertEqual(rate.pk, self.wage_rate.pk)


class WageLookupTextFallbackTest(SiteLocationTestBase):
    """9. Site without location_area FK falls back to text-based resolution."""

    def setUp(self):
        # Add a rate via legacy state/city text fields
        self.legacy_rate = MinimumWageRate.objects.create(
            org=self.org,
            state='Maharashtra',
            city='Mumbai',
            wage_category=self.wage_cat,
            monthly_wage=9500,
            daily_wage=365,
            effective_from=datetime.date(2025, 1, 1),
            is_active=True,
        )

    def test_text_fallback_when_no_fk(self):
        from apps.wages.services import get_applicable_minimum_wage

        site = SiteProfile.objects.create(
            org=self.org, client=self.sl_client, scope_node=self.n_client,
            name='Text Site', code='sl-txt1',
            location_area=None,
            city='Mumbai', state='Maharashtra', is_active=True,
        )
        rate = get_applicable_minimum_wage(
            self.wage_cat,
            datetime.date(2025, 6, 1),
            site=site,
            org=self.org,
        )
        self.assertIsNotNone(rate)
        # Should match something — either the LocationArea-resolved rate or the legacy rate
        self.assertIn(rate.pk, [self.wage_rate.pk, self.legacy_rate.pk])


# ─── Helper Unit Tests ────────────────────────────────────────────────────────

class ApplyLocationSnapshotsTest(SiteLocationTestBase):
    """10. apply_location_snapshots: city and state auto-filled from LocationArea."""

    def test_fills_blank_city_and_state(self):
        site = SiteProfile(
            org=self.org, client=self.sl_client,
            name='Helper Site', code='sl-helper',
            location_area=self.mumbai, city='', state='',
        )
        changed = apply_location_snapshots(site)
        self.assertTrue(changed)
        self.assertEqual(site.city, 'Mumbai')
        self.assertEqual(site.state, 'Maharashtra')

    def test_skips_when_city_already_set(self):
        site = SiteProfile(
            org=self.org, client=self.sl_client,
            name='Helper Site 2', code='sl-helper2',
            location_area=self.mumbai, city='Thane', state='',
        )
        apply_location_snapshots(site)
        self.assertEqual(site.city, 'Thane')  # not overwritten
        self.assertEqual(site.state, 'Maharashtra')  # state still filled

    def test_no_change_when_no_location_area(self):
        site = SiteProfile(
            org=self.org, client=self.sl_client,
            name='Helper Site 3', code='sl-helper3',
            location_area=None, city='', state='',
        )
        changed = apply_location_snapshots(site)
        self.assertFalse(changed)
        self.assertEqual(site.city, '')
        self.assertEqual(site.state, '')

    def test_state_area_type_fills_state_not_city(self):
        site = SiteProfile(
            org=self.org, client=self.sl_client,
            name='Helper Site 4', code='sl-helper4',
            location_area=self.maharashtra, city='', state='',
        )
        apply_location_snapshots(site)
        # maharashtra is area_type='state' → city not filled
        self.assertEqual(site.city, '')
        self.assertEqual(site.state, 'Maharashtra')

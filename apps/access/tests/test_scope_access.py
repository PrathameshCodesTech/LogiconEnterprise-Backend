"""
apps/access/tests/test_scope_access.py

Tests for scope resolution, queryset filters, capability checks, and API enforcement.

Fixture layout:
  org: logicon
  scope nodes:
    logicon                      (company)
    logicon/client-a             (client)
    logicon/client-a/site-1      (site)
    logicon/client-a/site-2      (site)
    logicon/client-b             (client)
    logicon/client-b/site-1      (site)

  clients:
    Client A  (scope_node = logicon/client-a)
    Client B  (scope_node = logicon/client-b)

  sites:
    Site A1   (client=A, scope_node = logicon/client-a/site-1)
    Site A2   (client=A, scope_node = logicon/client-a/site-2)
    Site B1   (client=B, scope_node = logicon/client-b/site-1)

  roles: admin, client_admin, client_site_user, hr_admin

  users:
    superuser          — Django superuser, no role assignment
    client_admin_user  — role=client_admin @ logicon/client-a
    site_user          — role=client_site_user @ logicon/client-a/site-1
    hr_user            — role=hr_admin @ logicon (company root)
    unassigned_user    — no role assignment
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.access.scope import (
    get_accessible_scope_paths,
    user_has_capability,
    user_has_any_capability,
)
from apps.access.capabilities import ALL_CAPABILITIES, get_user_capabilities
from apps.access.querysets import (
    filter_clients_for_user,
    filter_sites_for_user,
    filter_mrfs_for_user,
    filter_hiring_applications_for_user,
    filter_site_deployments_for_user,
)
from apps.sites.models import Client, SiteProfile
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.hiring.models import HiringApplication
from apps.talent.models import Candidate
from apps.deployment.models import Employee, SiteDeployment
from apps.wages.models import WageCategory


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _make_node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _make_user(username, is_superuser=False):
    return User.objects.create_user(
        username=username,
        password='testpass123',
        is_superuser=is_superuser,
        is_staff=is_superuser,
    )


def _assign_role(user, role, scope_node):
    return UserRoleAssignment.objects.create(
        user=user, role=role, scope_node=scope_node,
    )


# ─── Base fixture class ────────────────────────────────────────────────────────

class ScopeTestBase(TestCase):
    """Sets up the full fixture shared by all test classes."""

    @classmethod
    def setUpTestData(cls):
        # ── Organization ──
        cls.org = Organization.objects.create(
            name='Test Logicon', code='test-logicon',
        )

        # ── Scope nodes ──
        cls.n_company = _make_node(cls.org, 'test-logicon', 'company', None, 0, 'test-logicon')
        cls.n_client_a = _make_node(cls.org, 'client-a', 'client', cls.n_company, 1, 'test-logicon/client-a')
        cls.n_site_a1 = _make_node(cls.org, 'site-1', 'site', cls.n_client_a, 2, 'test-logicon/client-a/site-1')
        cls.n_site_a2 = _make_node(cls.org, 'site-2', 'site', cls.n_client_a, 2, 'test-logicon/client-a/site-2')
        cls.n_client_b = _make_node(cls.org, 'client-b', 'client', cls.n_company, 1, 'test-logicon/client-b')
        cls.n_site_b1 = _make_node(cls.org, 'site-1-b', 'site', cls.n_client_b, 2, 'test-logicon/client-b/site-1')

        # ── Access roles (use well-known codes for capabilities.py lookup) ──
        cls.role_admin = AccessRole.objects.create(
            org=cls.org, name='Admin', code='admin',
        )
        cls.role_client_admin = AccessRole.objects.create(
            org=cls.org, name='Client Admin', code='client_admin',
        )
        cls.role_site_user = AccessRole.objects.create(
            org=cls.org, name='Client Site User', code='client_site_user',
        )
        cls.role_hr_admin = AccessRole.objects.create(
            org=cls.org, name='HR Admin', code='hr_admin',
        )

        # ── Users ──
        cls.superuser = _make_user('test_superuser', is_superuser=True)
        cls.client_admin_user = _make_user('test_client_admin')
        cls.site_user = _make_user('test_site_user')
        cls.hr_user = _make_user('test_hr_user')
        cls.unassigned_user = _make_user('test_unassigned')

        # ── Role assignments ──
        _assign_role(cls.client_admin_user, cls.role_client_admin, cls.n_client_a)
        _assign_role(cls.site_user, cls.role_site_user, cls.n_site_a1)
        _assign_role(cls.hr_user, cls.role_hr_admin, cls.n_company)

        # ── DB permissions (runtime source of truth after RBAC-Runtime-A) ──
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_client_admin)
        bootstrap_role_permissions(cls.role_site_user)
        bootstrap_role_permissions(cls.role_hr_admin)

        # ── Clients ──
        cls.client_a = Client.objects.create(
            org=cls.org, name='Client A', code='client-a-test',
            scope_node=cls.n_client_a, is_active=True,
        )
        cls.client_b = Client.objects.create(
            org=cls.org, name='Client B', code='client-b-test',
            scope_node=cls.n_client_b, is_active=True,
        )

        # ── Sites ──
        cls.site_a1 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, scope_node=cls.n_site_a1,
            name='Site A1', code='site-a1-test', is_active=True,
        )
        cls.site_a2 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, scope_node=cls.n_site_a2,
            name='Site A2', code='site-a2-test', is_active=True,
        )
        cls.site_b1 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_b, scope_node=cls.n_site_b1,
            name='Site B1', code='site-b1-test', is_active=True,
        )

        # ── Job role + wage category for MRF/hiring fixtures ──
        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Test Worker', code='test-worker',
            skill_category='unskilled', is_active=True,
        )
        cls.wage_cat = WageCategory.objects.create(
            name='Test Unskilled', code='test-unskilled',
        )

        # ── MRF for site_a1 ──
        cls.mrf_a1 = ManpowerRequest.objects.create(
            org=cls.org,
            site=cls.site_a1,
            requested_by=cls.hr_user,
            mrf_type='new_hiring',
            status='submitted',
            billing_type='billable',
        )
        cls.mrf_line_a1 = MRFLineItem.objects.create(
            mrf=cls.mrf_a1,
            job_role=cls.job_role,
            headcount=5,
        )

        # ── MRF for site_b1 ──
        cls.mrf_b1 = ManpowerRequest.objects.create(
            org=cls.org,
            site=cls.site_b1,
            requested_by=cls.hr_user,
            mrf_type='new_hiring',
            status='submitted',
            billing_type='billable',
        )
        cls.mrf_line_b1 = MRFLineItem.objects.create(
            mrf=cls.mrf_b1,
            job_role=cls.job_role,
            headcount=3,
        )

        # ── Candidate + HiringApplication for site_a1 ──
        cls.candidate = Candidate.objects.create(
            org=cls.org,
            phone='9999900001',
            phone_normalized='9999900001',
            first_name='Test',
            last_name='Candidate',
        )
        cls.app_a1 = HiringApplication.objects.create(
            org=cls.org,
            candidate=cls.candidate,
            mrf=cls.mrf_a1,
            mrf_line_item=cls.mrf_line_a1,
            site=cls.site_a1,
            job_role=cls.job_role,
            status='shortlisted',
        )

        # ── Employee + SiteDeployment for site_a1 ──
        cls.employee = Employee.objects.create(
            org=cls.org,
            employee_code='EMP-TEST-001',
            first_name='Test',
            last_name='Employee',
        )
        cls.deployment_a1 = SiteDeployment.objects.create(
            org=cls.org,
            employee=cls.employee,
            site=cls.site_a1,
            job_role=cls.job_role,
            status='active',
            start_date=datetime.date.today(),
            billing_type='billable',
        )


# ─── Scope path tests ─────────────────────────────────────────────────────────

class TestAccessibleScopePaths(ScopeTestBase):

    def test_superuser_gets_wildcard(self):
        paths = get_accessible_scope_paths(self.superuser)
        self.assertEqual(paths, {'*'})

    def test_client_admin_gets_client_a_path(self):
        paths = get_accessible_scope_paths(self.client_admin_user)
        self.assertIn('test-logicon/client-a', paths)
        self.assertNotIn('test-logicon/client-b', paths)

    def test_site_user_gets_site_a1_path(self):
        paths = get_accessible_scope_paths(self.site_user)
        self.assertIn('test-logicon/client-a/site-1', paths)
        self.assertNotIn('test-logicon/client-a', paths)

    def test_hr_user_gets_company_path(self):
        paths = get_accessible_scope_paths(self.hr_user)
        self.assertIn('test-logicon', paths)

    def test_unassigned_user_gets_empty_paths(self):
        paths = get_accessible_scope_paths(self.unassigned_user)
        self.assertEqual(paths, set())


# ─── Capability tests ─────────────────────────────────────────────────────────

class TestCapabilityChecks(ScopeTestBase):

    def test_superuser_has_any_capability(self):
        self.assertTrue(user_has_capability(self.superuser, 'mrf.read'))
        self.assertTrue(user_has_capability(self.superuser, 'organization.delete'))

    def test_superuser_get_user_capabilities_returns_all(self):
        self.assertEqual(get_user_capabilities(self.superuser), sorted(ALL_CAPABILITIES))

    def test_hr_admin_has_mrf_read(self):
        self.assertTrue(user_has_capability(self.hr_user, 'mrf.read'))

    def test_hr_admin_has_candidate_read(self):
        self.assertTrue(user_has_capability(self.hr_user, 'candidate.read'))

    def test_hr_admin_has_hiring_application_read(self):
        self.assertTrue(user_has_capability(self.hr_user, 'hiring_application.read'))

    def test_client_admin_has_site_read(self):
        self.assertTrue(user_has_capability(self.client_admin_user, 'site.read'))

    def test_client_admin_does_not_have_wage_manage(self):
        self.assertFalse(user_has_capability(self.client_admin_user, 'wage.manage'))

    def test_unassigned_user_has_no_capabilities(self):
        self.assertFalse(user_has_capability(self.unassigned_user, 'site.read'))
        self.assertFalse(user_has_capability(self.unassigned_user, 'mrf.read'))

    def test_user_has_any_capability(self):
        self.assertTrue(
            user_has_any_capability(self.hr_user, ['mrf.read', 'organization.delete'])
        )

    def test_user_has_any_capability_fails_when_none_match(self):
        self.assertFalse(
            user_has_any_capability(self.unassigned_user, ['mrf.read', 'site.read'])
        )


# ─── Queryset filter tests ─────────────────────────────────────────────────────

class TestFilterSites(ScopeTestBase):

    def _qs(self):
        return SiteProfile.objects.filter(org=self.org).select_related(
            'scope_node', 'client__scope_node',
        )

    def test_superuser_sees_all_sites(self):
        result = filter_sites_for_user(self._qs(), self.superuser)
        codes = set(result.values_list('code', flat=True))
        self.assertIn('site-a1-test', codes)
        self.assertIn('site-a2-test', codes)
        self.assertIn('site-b1-test', codes)

    def test_client_admin_sees_all_sites_under_client_a(self):
        result = filter_sites_for_user(self._qs(), self.client_admin_user)
        codes = set(result.values_list('code', flat=True))
        self.assertIn('site-a1-test', codes)
        self.assertIn('site-a2-test', codes)
        self.assertNotIn('site-b1-test', codes)

    def test_client_admin_cannot_see_client_b_site(self):
        result = filter_sites_for_user(self._qs(), self.client_admin_user)
        self.assertFalse(result.filter(code='site-b1-test').exists())

    def test_site_user_sees_only_assigned_site(self):
        result = filter_sites_for_user(self._qs(), self.site_user)
        codes = set(result.values_list('code', flat=True))
        self.assertIn('site-a1-test', codes)
        self.assertNotIn('site-a2-test', codes)
        self.assertNotIn('site-b1-test', codes)

    def test_unassigned_user_sees_no_sites(self):
        result = filter_sites_for_user(self._qs(), self.unassigned_user)
        self.assertEqual(result.count(), 0)

    def test_hr_user_at_company_sees_all_sites(self):
        result = filter_sites_for_user(self._qs(), self.hr_user)
        codes = set(result.values_list('code', flat=True))
        self.assertIn('site-a1-test', codes)
        self.assertIn('site-b1-test', codes)


class TestFilterClients(ScopeTestBase):

    def _qs(self):
        return Client.objects.filter(org=self.org).select_related('scope_node')

    def test_client_admin_sees_only_client_a(self):
        result = filter_clients_for_user(self._qs(), self.client_admin_user)
        codes = set(result.values_list('code', flat=True))
        self.assertIn('client-a-test', codes)
        self.assertNotIn('client-b-test', codes)

    def test_superuser_sees_all_clients(self):
        result = filter_clients_for_user(self._qs(), self.superuser)
        self.assertEqual(result.count(), 2)

    def test_unassigned_user_sees_no_clients(self):
        result = filter_clients_for_user(self._qs(), self.unassigned_user)
        self.assertEqual(result.count(), 0)


class TestFilterMRFs(ScopeTestBase):

    def _qs(self):
        return ManpowerRequest.objects.filter(org=self.org).select_related(
            'site__scope_node', 'site__client__scope_node',
        )

    def test_superuser_sees_all_mrfs(self):
        result = filter_mrfs_for_user(self._qs(), self.superuser)
        self.assertEqual(result.count(), 2)

    def test_client_admin_sees_mrf_for_site_a1_not_b1(self):
        result = filter_mrfs_for_user(self._qs(), self.client_admin_user)
        pks = set(result.values_list('pk', flat=True))
        self.assertIn(self.mrf_a1.pk, pks)
        self.assertNotIn(self.mrf_b1.pk, pks)

    def test_site_user_sees_mrf_for_site_a1_only(self):
        result = filter_mrfs_for_user(self._qs(), self.site_user)
        pks = set(result.values_list('pk', flat=True))
        self.assertIn(self.mrf_a1.pk, pks)
        self.assertNotIn(self.mrf_b1.pk, pks)

    def test_unassigned_user_sees_no_mrfs(self):
        result = filter_mrfs_for_user(self._qs(), self.unassigned_user)
        self.assertEqual(result.count(), 0)


class TestFilterHiringApplications(ScopeTestBase):

    def _qs(self):
        return HiringApplication.objects.filter(org=self.org).select_related(
            'site__scope_node', 'site__client__scope_node',
        )

    def test_superuser_sees_all_applications(self):
        result = filter_hiring_applications_for_user(self._qs(), self.superuser)
        self.assertIn(self.app_a1.pk, result.values_list('pk', flat=True))

    def test_client_admin_sees_application_for_site_a1(self):
        result = filter_hiring_applications_for_user(self._qs(), self.client_admin_user)
        self.assertIn(self.app_a1.pk, result.values_list('pk', flat=True))

    def test_site_user_sees_application_for_site_a1(self):
        result = filter_hiring_applications_for_user(self._qs(), self.site_user)
        self.assertIn(self.app_a1.pk, result.values_list('pk', flat=True))

    def test_unassigned_user_sees_no_applications(self):
        result = filter_hiring_applications_for_user(self._qs(), self.unassigned_user)
        self.assertEqual(result.count(), 0)


class TestFilterSiteDeployments(ScopeTestBase):

    def _qs(self):
        return SiteDeployment.objects.filter(org=self.org).select_related(
            'site__scope_node', 'site__client__scope_node',
        )

    def test_superuser_sees_all_deployments(self):
        result = filter_site_deployments_for_user(self._qs(), self.superuser)
        self.assertIn(self.deployment_a1.pk, result.values_list('pk', flat=True))

    def test_client_admin_sees_deployment_for_site_a1(self):
        result = filter_site_deployments_for_user(self._qs(), self.client_admin_user)
        self.assertIn(self.deployment_a1.pk, result.values_list('pk', flat=True))

    def test_unassigned_user_sees_no_deployments(self):
        result = filter_site_deployments_for_user(self._qs(), self.unassigned_user)
        self.assertEqual(result.count(), 0)


# ─── API enforcement tests ─────────────────────────────────────────────────────

class TestSiteAPIEnforcement(ScopeTestBase):

    def setUp(self):
        self.client_api = APIClient()

    def _login(self, user):
        self.client_api.force_authenticate(user=user)

    def test_client_admin_api_sees_only_client_a_sites(self):
        self._login(self.client_admin_user)
        response = self.client_api.get('/api/sites/profiles/')
        self.assertEqual(response.status_code, 200)
        codes = {r['code'] for r in response.data['results']}
        self.assertIn('site-a1-test', codes)
        self.assertIn('site-a2-test', codes)
        self.assertNotIn('site-b1-test', codes)

    def test_site_user_api_sees_only_site_a1(self):
        self._login(self.site_user)
        response = self.client_api.get('/api/sites/profiles/')
        self.assertEqual(response.status_code, 200)
        codes = {r['code'] for r in response.data['results']}
        self.assertIn('site-a1-test', codes)
        self.assertNotIn('site-a2-test', codes)
        self.assertNotIn('site-b1-test', codes)

    def test_unassigned_user_api_sites_returns_403(self):
        self._login(self.unassigned_user)
        response = self.client_api.get('/api/sites/profiles/')
        self.assertEqual(response.status_code, 403)

    def test_superuser_api_sees_all_sites(self):
        self._login(self.superuser)
        response = self.client_api.get('/api/sites/profiles/')
        self.assertEqual(response.status_code, 200)
        codes = {r['code'] for r in response.data['results']}
        self.assertIn('site-a1-test', codes)
        self.assertIn('site-b1-test', codes)

    def test_unauthenticated_returns_401(self):
        response = self.client_api.get('/api/sites/profiles/')
        self.assertEqual(response.status_code, 401)

    def test_client_admin_api_clients_sees_only_client_a(self):
        self._login(self.client_admin_user)
        response = self.client_api.get('/api/sites/clients/')
        self.assertEqual(response.status_code, 200)
        codes = {r['code'] for r in response.data['results']}
        self.assertIn('client-a-test', codes)
        self.assertNotIn('client-b-test', codes)

    def test_me_endpoint_returns_capabilities(self):
        self._login(self.hr_user)
        response = self.client_api.get('/api/core/me/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('capabilities', response.data)
        self.assertIn('mrf.read', response.data['capabilities'])

    def test_me_endpoint_superuser_returns_all_capabilities(self):
        self._login(self.superuser)
        response = self.client_api.get('/api/core/me/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_superuser'])
        self.assertEqual(response.data['capabilities'], sorted(ALL_CAPABILITIES))

"""
apps/sites/tests/test_master_write_apis.py

Tests for Client, SiteProfile, SiteRoleRequirement write APIs.

Fixture layout:
  org: logicon
  scope tree:
    logicon (company)
    logicon/client-a (client)
    logicon/client-a/site-1 (site)
    logicon/client-b (client)

  users:
    admin_user         — role=admin @ logicon (company root)
    sales_exec_user    — role=sales_executive @ logicon
    client_user_a      — role=client_admin @ logicon/client-a
    unscoped_user      — role=client_user @ logicon/client-a (no client.create cap)
    superuser          — Django superuser
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.audit.models import AuditLog
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.wages.models import WageCategory


def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, is_superuser=False):
    return User.objects.create_user(
        username=username, password='pass123',
        is_superuser=is_superuser, is_staff=is_superuser,
    )


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


class SitesWriteTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Test Logicon', code='tl-sw')

        cls.n_company = _node(cls.org, 'tl-sw', 'company', None, 0, 'tl-sw')
        cls.n_client_a = _node(cls.org, 'client-a', 'client', cls.n_company, 1, 'tl-sw/client-a')
        cls.n_site_a1 = _node(cls.org, 'site-1', 'site', cls.n_client_a, 2, 'tl-sw/client-a/site-1')
        cls.n_client_b = _node(cls.org, 'client-b', 'client', cls.n_company, 1, 'tl-sw/client-b')

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_sales_exec = _role(cls.org, 'sales_executive')
        cls.role_client_admin = _role(cls.org, 'client_admin')
        cls.role_client_user = _role(cls.org, 'client_user')

        cls.superuser = _user('sw_superuser', is_superuser=True)
        cls.admin_user = _user('sw_admin')
        cls.admin_user.org = cls.org
        cls.admin_user.save()
        cls.sales_exec_user = _user('sw_sales_exec')
        cls.sales_exec_user.org = cls.org
        cls.sales_exec_user.save()
        cls.client_user_a = _user('sw_client_a')
        cls.client_user_a.org = cls.org
        cls.client_user_a.save()
        cls.unscoped_user = _user('sw_unscoped')
        cls.unscoped_user.org = cls.org
        cls.unscoped_user.save()

        _assign(cls.admin_user, cls.role_admin, cls.n_company)
        _assign(cls.sales_exec_user, cls.role_sales_exec, cls.n_company)
        _assign(cls.client_user_a, cls.role_client_admin, cls.n_client_a)
        _assign(cls.unscoped_user, cls.role_client_user, cls.n_client_a)

        cls.client_a = Client.objects.create(
            org=cls.org, name='Client A', code='client-a-sw',
            scope_node=cls.n_client_a, is_active=True,
        )
        cls.client_b = Client.objects.create(
            org=cls.org, name='Client B', code='client-b-sw',
            scope_node=cls.n_client_b, is_active=True,
        )
        cls.site_a1 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, scope_node=cls.n_site_a1,
            name='Site A1', code='site-a1-sw', is_active=True,
        )
        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard', code='guard-sw')
        cls.wage_cat = WageCategory.objects.create(name='Unskilled SW', code='unskilled-sw')

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── Client Tests ─────────────────────────────────────────────────────────────

class TestClientWriteAPI(SitesWriteTestBase):

    def test_admin_can_create_client_and_scope_node_is_created(self):
        self._login(self.admin_user)
        payload = {
            'name': 'New Client',
            'code': 'new-client-sw',
            'contact_name': 'Alice',
            'is_active': True,
        }
        response = self.api.post('/api/sites/clients/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn('id', response.data)
        # Scope node must have been created
        self.assertTrue(
            ScopeNode.objects.filter(
                path='tl-sw/new-client-sw', node_type='client',
            ).exists()
        )
        # Audit logged
        self.assertTrue(
            AuditLog.objects.filter(
                action='client.create',
                object_type='Client',
            ).exists()
        )

    def test_sales_exec_with_client_create_can_create_client(self):
        self._login(self.sales_exec_user)
        payload = {'name': 'Sales Client', 'code': 'sales-client-sw', 'is_active': True}
        response = self.api.post('/api/sites/clients/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            ScopeNode.objects.filter(path='tl-sw/sales-client-sw').exists()
        )

    def test_client_code_duplicate_blocked(self):
        self._login(self.admin_user)
        # client-a-sw already exists
        payload = {'name': 'Duplicate', 'code': 'client-a-sw', 'is_active': True}
        response = self.api.post('/api/sites/clients/', payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_client_delete_soft_deactivates(self):
        self._login(self.superuser)
        client = Client.objects.create(
            org=self.org, name='To Delete', code='to-delete-sw',
            scope_node=self.n_client_a, is_active=True,
        )
        response = self.api.delete(f'/api/sites/clients/{client.pk}/')
        self.assertEqual(response.status_code, 204)
        client.refresh_from_db()
        self.assertFalse(client.is_active)

    def test_client_update_is_audited(self):
        self._login(self.admin_user)
        initial_count = AuditLog.objects.filter(action='client.update').count()
        response = self.api.patch(
            f'/api/sites/clients/{self.client_a.pk}/',
            {'contact_name': 'Bob'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            AuditLog.objects.filter(action='client.update').count(),
            initial_count + 1,
        )

    def test_client_user_without_create_capability_gets_403(self):
        self._login(self.unscoped_user)  # role=client_user — no client.create
        payload = {'name': 'No Access', 'code': 'no-access-sw', 'is_active': True}
        response = self.api.post('/api/sites/clients/', payload, format='json')
        self.assertEqual(response.status_code, 403)


# ─── Site Tests ───────────────────────────────────────────────────────────────

class TestSiteProfileWriteAPI(SitesWriteTestBase):

    def test_admin_can_create_site_and_scope_node_is_created(self):
        self._login(self.admin_user)
        payload = {
            'client': self.client_a.pk,
            'name': 'New Site',
            'code': 'new-site-sw',
            'city': 'Mumbai',
            'is_active': True,
        }
        response = self.api.post('/api/sites/profiles/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            ScopeNode.objects.filter(
                path='tl-sw/client-a/new-site-sw', node_type='site',
            ).exists()
        )

    def test_site_path_is_client_path_slash_site_code(self):
        self._login(self.admin_user)
        payload = {
            'client': self.client_b.pk,
            'name': 'Path Test Site',
            'code': 'path-test-sw',
            'is_active': True,
        }
        response = self.api.post('/api/sites/profiles/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        node = ScopeNode.objects.get(path='tl-sw/client-b/path-test-sw')
        self.assertEqual(node.node_type, 'site')
        self.assertEqual(node.parent, self.n_client_b)

    def test_superuser_without_org_can_create_site_under_client_org(self):
        self._login(self.superuser)
        payload = {
            'client': self.client_a.pk,
            'name': 'Superuser Site',
            'code': 'superuser-site-sw',
            'city': 'Mumbai',
            'is_active': True,
        }
        response = self.api.post('/api/sites/profiles/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        site = SiteProfile.objects.get(code='superuser-site-sw')
        self.assertEqual(site.org, self.client_a.org)
        self.assertTrue(
            ScopeNode.objects.filter(
                path='tl-sw/client-a/superuser-site-sw',
                org=self.client_a.org,
            ).exists()
        )

    def test_site_create_blocked_if_client_outside_actor_scope(self):
        # client_user_a is scoped to client-a; trying to create under client-b
        self._login(self.client_user_a)
        payload = {
            'client': self.client_b.pk,
            'name': 'Forbidden Site',
            'code': 'forbidden-site-sw',
            'is_active': True,
        }
        response = self.api.post('/api/sites/profiles/', payload, format='json')
        # client_admin has site.create — they CAN do create, but scope check blocks it
        self.assertIn(response.status_code, [403, 400])

    def test_site_delete_soft_deactivates(self):
        self._login(self.superuser)
        site = SiteProfile.objects.create(
            org=self.org, client=self.client_a, scope_node=self.n_site_a1,
            name='To Delete Site', code='del-site-sw', is_active=True,
        )
        response = self.api.delete(f'/api/sites/profiles/{site.pk}/')
        self.assertEqual(response.status_code, 204)
        site.refresh_from_db()
        self.assertFalse(site.is_active)

    def test_site_update_is_audited(self):
        self._login(self.admin_user)
        initial_count = AuditLog.objects.filter(action='site.update').count()
        response = self.api.patch(
            f'/api/sites/profiles/{self.site_a1.pk}/',
            {'city': 'Pune'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            AuditLog.objects.filter(action='site.update').count(),
            initial_count + 1,
        )


# ─── SiteRoleRequirement Tests ────────────────────────────────────────────────

class TestSiteRoleRequirementWriteAPI(SitesWriteTestBase):

    def _req_payload(self, site_pk=None, **overrides):
        base = {
            'site': site_pk or self.site_a1.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 5,
            'billing_type': 'billable',
            'effective_from': '2025-01-01',
            'is_active': True,
        }
        base.update(overrides)
        return base

    def test_admin_can_create_requirement_for_accessible_site(self):
        self._login(self.admin_user)
        response = self.api.post(
            '/api/sites/role-requirements/', self._req_payload(), format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            AuditLog.objects.filter(action='site_role_requirement.create').exists()
        )

    def test_create_blocked_for_inaccessible_site(self):
        # client_user_a scoped to client-a; site_b1 is under client-b
        n_site_b1 = _node(self.org, 'site-b-sw', 'site', self.n_client_b, 2, 'tl-sw/client-b/site-b-sw')
        site_b1 = SiteProfile.objects.create(
            org=self.org, client=self.client_b, scope_node=n_site_b1,
            name='Site B1 SW', code='site-b1-sw2', is_active=True,
        )
        self._login(self.client_user_a)
        response = self.api.post(
            '/api/sites/role-requirements/',
            self._req_payload(site_pk=site_b1.pk),
            format='json',
        )
        # client_user has no site_role_requirement.create capability → 403
        self.assertEqual(response.status_code, 403)

    def test_wage_min_greater_than_wage_max_blocked(self):
        self._login(self.admin_user)
        payload = self._req_payload(wage_min='500.00', wage_max='100.00')
        response = self.api.post('/api/sites/role-requirements/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('wage_min', str(response.data))

    def test_effective_to_before_effective_from_blocked(self):
        self._login(self.admin_user)
        payload = self._req_payload(
            effective_from='2025-06-01',
            effective_to='2025-01-01',
        )
        response = self.api.post('/api/sites/role-requirements/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('effective_to', str(response.data))

    def test_zero_headcount_blocked(self):
        self._login(self.admin_user)
        payload = self._req_payload(approved_headcount=0)
        response = self.api.post('/api/sites/role-requirements/', payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_destroy_soft_deactivates(self):
        self._login(self.superuser)
        req = SiteRoleRequirement.objects.create(
            site=self.site_a1, job_role=self.job_role,
            approved_headcount=3, billing_type='billable',
            effective_from=datetime.date(2025, 1, 1), is_active=True,
        )
        response = self.api.delete(f'/api/sites/role-requirements/{req.pk}/')
        self.assertEqual(response.status_code, 204)
        req.refresh_from_db()
        self.assertFalse(req.is_active)

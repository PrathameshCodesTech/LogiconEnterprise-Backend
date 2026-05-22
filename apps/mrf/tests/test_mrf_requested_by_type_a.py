"""
apps/mrf/tests/test_mrf_requested_by_type_a.py

Requested-by-type enforcement for client-facing users.

Backend rule: client_admin / client_site_user / site_supervisor / client_user
roles always produce requested_by_type='client', regardless of what the
frontend sends.  Internal users set it freely.

Scenarios:
  1.  is_client_facing_user returns True for client_admin.
  2.  is_client_facing_user returns True for client_site_user.
  3.  is_client_facing_user returns True for site_supervisor.
  4.  is_client_facing_user returns True for client_user.
  5.  is_client_facing_user returns False for hr_admin (internal role).
  6.  is_client_facing_user returns False for superuser.
  7.  is_client_facing_user returns False for user with no role assignments.
  8.  is_client_facing_user returns False when user holds both client and internal roles.
  9.  Client user POST with requested_by_type='internal' → saved as 'client'.
  10. Client user POST without requested_by_type → saved as 'client'.
  11. Client user PATCH with requested_by_type='internal' → remains 'client'.
  12. Internal user POST with requested_by_type='internal' → saved as 'internal'.
  13. Internal user POST with requested_by_type='client' → saved as 'client'.
  14. Internal user PATCH can change requested_by_type.
  15. Superuser POST with requested_by_type='internal' → saved as 'internal'.
"""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    MRF_CREATE, MRF_READ, MRF_UPDATE,
    CLIENT_FACING_ROLE_CODES,
    is_client_facing_user,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest
from apps.sites.models import Client, SiteProfile


MRF_URL = '/api/mrf/requests/'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code='rbt-a'):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client', code=f'cl-{org.code}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    n_site = ScopeNode.objects.create(
        org=org, code=f'si-{org.code}', name=f'si-{org.code}', node_type='site',
        parent=n_cl, depth=2, path=f'{org.code}/cl-{org.code}/si-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_site,
        name='Site', code=f'si-{org.code}', city='City', state='ST', is_active=True,
    )
    return n_co, n_cl, n_site, client, site


def _role(org, code, caps=None):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    if caps is not None:
        bootstrap_role_permissions(role, caps)
    else:
        bootstrap_role_permissions(role)
    return role


def _user(username, org, role=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass', is_superuser=is_superuser)
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _mrf_payload(site):
    return {
        'site': site.pk,
        'mrf_type': 'new_hiring',
        'billing_type': 'non_billable',
    }


# ─── Unit tests for is_client_facing_user ────────────────────────────────────

class TestIsClientFacingUser(TestCase):

    def setUp(self):
        self.org = _org('cfu')
        self.n_co, self.n_cl, self.n_site, self.client, self.site = _scope_tree(self.org)

    def _make_user_with_role(self, role_code, scope_node=None):
        role = _role(self.org, role_code, caps=[MRF_READ, MRF_CREATE, MRF_UPDATE])
        node = scope_node or self.n_co
        return _user(f'u-{role_code}', self.org, role, node)

    def test_client_admin_is_client_facing(self):
        u = self._make_user_with_role('client_admin', self.n_cl)
        self.assertTrue(is_client_facing_user(u))

    def test_client_site_user_is_client_facing(self):
        u = self._make_user_with_role('client_site_user', self.n_site)
        self.assertTrue(is_client_facing_user(u))

    def test_site_supervisor_is_client_facing(self):
        u = self._make_user_with_role('site_supervisor', self.n_site)
        self.assertTrue(is_client_facing_user(u))

    def test_client_user_is_client_facing(self):
        u = self._make_user_with_role('client_user', self.n_co)
        self.assertTrue(is_client_facing_user(u))

    def test_hr_admin_is_not_client_facing(self):
        u = self._make_user_with_role('hr_admin', self.n_co)
        self.assertFalse(is_client_facing_user(u))

    def test_superuser_is_not_client_facing(self):
        u = _user('su', self.org, is_superuser=True)
        self.assertFalse(is_client_facing_user(u))

    def test_user_with_no_assignments_is_not_client_facing(self):
        u = _user('no-roles', self.org)
        self.assertFalse(is_client_facing_user(u))

    def test_user_with_mixed_roles_is_not_client_facing(self):
        """A user holding both client_admin and hr_admin is NOT treated as client-facing."""
        client_role = _role(self.org, 'client_admin', caps=[MRF_READ, MRF_CREATE])
        internal_role = _role(self.org, 'hr_admin', caps=[MRF_READ, MRF_CREATE])
        u = _user('mixed', self.org)
        UserRoleAssignment.objects.create(user=u, role=client_role, scope_node=self.n_cl)
        UserRoleAssignment.objects.create(user=u, role=internal_role, scope_node=self.n_co)
        self.assertFalse(is_client_facing_user(u))


# ─── API-level enforcement tests ─────────────────────────────────────────────

class TestRequestedByTypeEnforcement(TestCase):

    def setUp(self):
        self.org = _org('rbt')
        self.n_co, self.n_cl, self.n_site, self.client, self.site = _scope_tree(self.org)

        self.client_role = _role(self.org, 'client_admin')
        self.internal_role = _role(self.org, 'hr_admin')

        self.client_user = _user('cli', self.org, self.client_role, self.n_cl)
        self.internal_user = _user('hr', self.org, self.internal_role, self.n_co)
        self.super_user = _user('su', self.org, is_superuser=True)

    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    # ── 9. Client POST with 'internal' → saved as 'client' ───────────────────

    def test_client_user_post_internal_forced_to_client(self):
        resp = self._api(self.client_user).post(
            MRF_URL,
            {**_mrf_payload(self.site), 'requested_by_type': 'internal'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf = ManpowerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(mrf.requested_by_type, 'client')

    # ── 10. Client POST without requested_by_type → saved as 'client' ────────

    def test_client_user_post_omitted_defaults_to_client(self):
        resp = self._api(self.client_user).post(
            MRF_URL,
            _mrf_payload(self.site),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf = ManpowerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(mrf.requested_by_type, 'client')

    # ── 11. Client PATCH with 'internal' → remains 'client' ──────────────────

    def test_client_user_patch_internal_forced_to_client(self):
        # Create an MRF that already has requested_by_type='client'
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.site,
            requested_by=self.client_user,
            mrf_type='new_hiring', billing_type='non_billable',
            requested_by_type='client', status='draft',
        )
        resp = self._api(self.client_user).patch(
            f'{MRF_URL}{mrf.pk}/',
            {'requested_by_type': 'internal'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        mrf.refresh_from_db()
        self.assertEqual(mrf.requested_by_type, 'client')

    # ── 12. Internal user POST with 'internal' → saved as 'internal' ─────────

    def test_internal_user_can_set_requested_by_type_internal(self):
        resp = self._api(self.internal_user).post(
            MRF_URL,
            {**_mrf_payload(self.site), 'requested_by_type': 'internal'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf = ManpowerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(mrf.requested_by_type, 'internal')

    # ── 13. Internal user POST with 'client' → saved as 'client' ─────────────

    def test_internal_user_can_set_requested_by_type_client(self):
        resp = self._api(self.internal_user).post(
            MRF_URL,
            {**_mrf_payload(self.site), 'requested_by_type': 'client'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf = ManpowerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(mrf.requested_by_type, 'client')

    # ── 14. Internal user PATCH can change requested_by_type freely ───────────

    def test_internal_user_patch_can_change_requested_by_type(self):
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.site,
            requested_by=self.internal_user,
            mrf_type='new_hiring', billing_type='non_billable',
            requested_by_type='internal', status='draft',
        )
        resp = self._api(self.internal_user).patch(
            f'{MRF_URL}{mrf.pk}/',
            {'requested_by_type': 'client'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        mrf.refresh_from_db()
        self.assertEqual(mrf.requested_by_type, 'client')

    # ── 15. Superuser POST with 'internal' → not forced ───────────────────────

    def test_superuser_post_internal_not_forced(self):
        resp = self._api(self.super_user).post(
            MRF_URL,
            {**_mrf_payload(self.site), 'requested_by_type': 'internal'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf = ManpowerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(mrf.requested_by_type, 'internal')

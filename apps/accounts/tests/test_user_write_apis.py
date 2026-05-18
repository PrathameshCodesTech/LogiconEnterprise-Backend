"""
apps/accounts/tests/test_user_write_apis.py

Tests for User CRUD API.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.models import Organization, ScopeNode


def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass123',
        is_superuser=is_superuser, is_staff=is_superuser,
    )
    if org:
        u.org = org
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


class UserWriteTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Test Org UW', code='tl-uw')
        cls.n_company = _node(cls.org, 'tl-uw', 'company', None, 0, 'tl-uw')
        cls.n_client_a = _node(cls.org, 'client-a-uw', 'client', cls.n_company, 1, 'tl-uw/client-a-uw')

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr_admin = _role(cls.org, 'hr_admin')
        cls.role_client_user = _role(cls.org, 'client_user')
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr_admin)
        bootstrap_role_permissions(cls.role_client_user)

        cls.superuser = _user('uw_superuser', is_superuser=True)
        cls.admin_user = _user('uw_admin', org=cls.org)
        cls.hr_admin_user = _user('uw_hr_admin', org=cls.org)
        cls.client_only_user = _user('uw_client_only', org=cls.org)

        _assign(cls.admin_user, cls.role_admin, cls.n_company)
        _assign(cls.hr_admin_user, cls.role_hr_admin, cls.n_company)
        _assign(cls.client_only_user, cls.role_client_user, cls.n_client_a)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


class TestUserCreateAPI(UserWriteTestBase):

    def test_admin_can_create_internal_user(self):
        self._login(self.admin_user)
        payload = {
            'username': 'new_internal_uw',
            'email': 'ni@example.com',
            'first_name': 'New',
            'last_name': 'Internal',
            'user_type': 'internal',
            'is_active': True,
        }
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(username='new_internal_uw')
        self.assertEqual(created.org, self.org)
        self.assertFalse(created.has_usable_password())
        self.assertTrue(AuditLog.objects.filter(action='user.create', object_type='User').exists())

    def test_admin_can_create_client_user(self):
        self._login(self.admin_user)
        payload = {
            'username': 'new_client_uw',
            'user_type': 'client',
            'is_active': True,
        }
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['user_type'], 'client')

    def test_admin_create_user_with_password(self):
        self._login(self.admin_user)
        payload = {
            'username': 'pwd_user_uw',
            'user_type': 'internal',
            'password': 'SecurePass123!',
            'is_active': True,
        }
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(username='pwd_user_uw')
        self.assertTrue(created.has_usable_password())
        self.assertTrue(created.check_password('SecurePass123!'))

    def test_phone_normalizes_on_create(self):
        self._login(self.admin_user)
        payload = {
            'username': 'phone_user_uw',
            'user_type': 'internal',
            'phone_number': '+91-98765-43210',
            'is_active': True,
        }
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(username='phone_user_uw')
        self.assertEqual(created.phone_normalized, '9876543210')

    def test_duplicate_phone_per_org_blocked(self):
        # Create first user with phone
        first = User(username='first_phone_uw', org=self.org, phone_number='9000000001', user_type='internal')
        first.set_unusable_password()
        first.save()

        self._login(self.admin_user)
        payload = {
            'username': 'second_phone_uw',
            'user_type': 'internal',
            'phone_number': '9000000001',  # same normalized form
            'is_active': True,
        }
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_client_site_user_cannot_create_users(self):
        self._login(self.client_only_user)
        payload = {'username': 'sneaky_uw', 'user_type': 'internal', 'is_active': True}
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_create_users(self):
        payload = {'username': 'anon_uw', 'user_type': 'internal', 'is_active': True}
        response = self.api.post('/api/accounts/users/', payload, format='json')
        self.assertEqual(response.status_code, 401)


class TestUserDestroyAPI(UserWriteTestBase):

    def test_destroy_deactivates_user_not_deletes(self):
        target = User(username='target_deact_uw', org=self.org, user_type='internal')
        target.set_unusable_password()
        target.save()

        self._login(self.admin_user)
        response = self.api.delete(f'/api/accounts/users/{target.pk}/')
        self.assertEqual(response.status_code, 204)

        # Row still exists, just deactivated
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(AuditLog.objects.filter(action='user.delete', object_type='User').exists())


class TestUserReadAPI(UserWriteTestBase):

    def test_admin_can_list_users_in_org(self):
        self._login(self.admin_user)
        response = self.api.get('/api/accounts/users/')
        self.assertEqual(response.status_code, 200)
        usernames = {u['username'] for u in response.data['results']}
        self.assertIn('uw_admin', usernames)

    def test_hr_admin_can_list_users(self):
        self._login(self.hr_admin_user)
        response = self.api.get('/api/accounts/users/')
        self.assertEqual(response.status_code, 200)

    def test_client_user_cannot_list_users(self):
        self._login(self.client_only_user)
        response = self.api.get('/api/accounts/users/')
        self.assertEqual(response.status_code, 403)

"""
apps/access/tests/test_assignment_apis.py

Tests for UserRoleAssignment and UserScopeAssignment write APIs.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment, UserScopeAssignment
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


def _role(org, code, node_type_scope=''):
    return AccessRole.objects.get_or_create(
        org=org, code=code,
        defaults={'name': code, 'node_type_scope': node_type_scope},
    )[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


class AssignmentTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Test Org AA', code='tl-aa')

        cls.n_company = _node(cls.org, 'tl-aa', 'company', None, 0, 'tl-aa')
        cls.n_client_a = _node(cls.org, 'client-a-aa', 'client', cls.n_company, 1, 'tl-aa/client-a-aa')
        cls.n_site_a1 = _node(cls.org, 'site-1-aa', 'site', cls.n_client_a, 2, 'tl-aa/client-a-aa/site-1-aa')
        cls.n_client_b = _node(cls.org, 'client-b-aa', 'client', cls.n_company, 1, 'tl-aa/client-b-aa')

        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr_admin = _role(cls.org, 'hr_admin')
        cls.role_client_admin = _role(cls.org, 'client_admin', node_type_scope='client')
        cls.role_site_manager = _role(cls.org, 'site_manager', node_type_scope='site')

        cls.superuser = _user('aa_superuser', is_superuser=True)
        cls.admin_user = _user('aa_admin', org=cls.org)
        cls.hr_admin_user = _user('aa_hr_admin', org=cls.org)
        cls.limited_user = _user('aa_limited', org=cls.org)
        cls.target_user = _user('aa_target', org=cls.org)

        _assign(cls.admin_user, cls.role_admin, cls.n_company)
        _assign(cls.hr_admin_user, cls.role_hr_admin, cls.n_company)
        # limited_user has no role → no capabilities

        # DB permissions for runtime capability lookup
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr_admin)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


class TestUserRoleAssignmentWrite(AssignmentTestBase):

    def test_admin_can_assign_client_admin_to_client_scope(self):
        self._login(self.admin_user)
        payload = {
            'user': self.target_user.pk,
            'role': self.role_client_admin.pk,
            'scope_node': self.n_client_a.pk,
        }
        response = self.api.post('/api/access/user-role-assignments/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            UserRoleAssignment.objects.filter(
                user=self.target_user,
                role=self.role_client_admin,
                scope_node=self.n_client_a,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action='role_assignment.create').exists()
        )

    def test_role_node_type_scope_enforced_client_admin_to_site_fails(self):
        """client_admin has node_type_scope='client'; assigning to a site node must fail."""
        self._login(self.admin_user)
        payload = {
            'user': self.target_user.pk,
            'role': self.role_client_admin.pk,
            'scope_node': self.n_site_a1.pk,  # site node — wrong type
        }
        response = self.api.post('/api/access/user-role-assignments/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('scope_node', str(response.data))

    def test_site_manager_cannot_be_assigned_to_client_scope(self):
        """site_manager has node_type_scope='site'; client scope must be rejected."""
        self._login(self.admin_user)
        payload = {
            'user': self.target_user.pk,
            'role': self.role_site_manager.pk,
            'scope_node': self.n_client_a.pk,  # client node — wrong type
        }
        response = self.api.post('/api/access/user-role-assignments/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_assignment_outside_actor_scope_blocked(self):
        """A user scoped only to client-a cannot assign roles at client-b."""
        # Give limited_user role.update capability but only at client-a scope
        scoped_role = _role(self.org, 'hr_admin')
        _assign(self.limited_user, scoped_role, self.n_client_a)
        self._login(self.limited_user)
        payload = {
            'user': self.target_user.pk,
            'role': self.role_client_admin.pk,
            'scope_node': self.n_client_b.pk,  # outside limited_user's scope
        }
        response = self.api.post('/api/access/user-role-assignments/', payload, format='json')
        # hr_admin has role.update capability but scope is client-a only
        self.assertIn(response.status_code, [403, 400])

    def test_delete_assignment_works(self):
        self._login(self.admin_user)
        assignment = UserRoleAssignment.objects.create(
            user=self.target_user,
            role=self.role_hr_admin,
            scope_node=self.n_company,
        )
        response = self.api.delete(f'/api/access/user-role-assignments/{assignment.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            UserRoleAssignment.objects.filter(pk=assignment.pk).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action='role_assignment.delete').exists()
        )

    def test_unauthenticated_cannot_access_assignments(self):
        response = self.api.get('/api/access/user-role-assignments/')
        self.assertEqual(response.status_code, 401)

    def test_user_without_role_read_gets_403(self):
        # limited_user has no role assignments initially after potential cleanup;
        # ensure a fresh user with no capabilities gets 403
        fresh = _user('fresh_aa_noperms', org=self.org)
        self._login(fresh)
        response = self.api.get('/api/access/user-role-assignments/')
        self.assertEqual(response.status_code, 403)


class TestUserScopeAssignmentWrite(AssignmentTestBase):

    def test_admin_can_create_scope_assignment(self):
        self._login(self.admin_user)
        payload = {
            'user': self.target_user.pk,
            'scope_node': self.n_client_a.pk,
            'assignment_type': 'primary',
        }
        response = self.api.post('/api/access/user-scope-assignments/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            UserScopeAssignment.objects.filter(
                user=self.target_user, scope_node=self.n_client_a,
            ).exists()
        )

    def test_delete_scope_assignment_works(self):
        self._login(self.admin_user)
        sa = UserScopeAssignment.objects.create(
            user=self.target_user, scope_node=self.n_company, assignment_type='primary',
        )
        response = self.api.delete(f'/api/access/user-scope-assignments/{sa.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UserScopeAssignment.objects.filter(pk=sa.pk).exists())

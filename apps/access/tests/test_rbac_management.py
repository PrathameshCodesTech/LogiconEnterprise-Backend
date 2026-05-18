"""
apps/access/tests/test_rbac_management.py

Phase RBAC-Management-A tests — role, permission, and role-permission management APIs.

Scenarios:

AccessRoleViewSet (14 tests):
  1.  role.read can list roles.
  2.  no role.read gets 403.
  3.  unauthenticated gets 401.
  4.  role.create can create role (201, read serializer returned).
  5.  non-superuser create forces actor.org.
  6.  superuser create may specify org.
  7.  duplicate active code per org returns 400.
  8.  code is normalized to lowercase on create.
  9.  role.update can PATCH name.
  10. role.update can PATCH is_active.
  11. role.update returns read serializer after PATCH.
  12. role.delete soft-deactivates (is_active=False, no hard delete, 204).
  13. role from another org is hidden for non-superuser.
  14. superuser sees all orgs.

PermissionViewSet (4 tests):
  15. role.read can list permissions (200).
  16. response includes code field.
  17. workflow.config.read row has code=workflow.config.read, resource=workflow_config.
  18. filter by resource works.

AccessRolePermissionViewSet (11 tests):
  19. role.read can list role-permissions (200).
  20. role.update can attach permission (201, rich read response).
  21. role.update can delete permission mapping (204).
  22. duplicate attach returns clean 400.
  23. attach to inactive role returns 400.
  24. non-superuser cannot attach permission to another org's role (403).
  25. non-superuser cannot delete permission from another org's role (403).
  26. non-superuser cannot see another org's role-permissions.
  27. filter by role works.
  28. filter by permission__resource works.
  29. runtime: /api/core/me/ capabilities reflect attach/detach immediately.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import ALL_CAPABILITIES
from apps.access.models import AccessRole, AccessRolePermission, Permission, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions, get_or_create_permission
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode


# ─── Base ─────────────────────────────────────────────────────────────────────

class RBACManagementBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Mgmt Org', code='mgmt-org')
        cls.org2 = Organization.objects.create(name='Other Org', code='mgmt-org2')

        cls.n_company = ScopeNode.objects.create(
            org=cls.org, code='mgmt-org', name='Mgmt', node_type='company',
            parent=None, depth=0, path='mgmt-org', is_active=True,
        )
        cls.n_company2 = ScopeNode.objects.create(
            org=cls.org2, code='mgmt-org2', name='Other', node_type='company',
            parent=None, depth=0, path='mgmt-org2', is_active=True,
        )

        # Admin has all capabilities (role.read, role.create, role.update, role.delete)
        cls.role_admin = AccessRole.objects.create(org=cls.org, name='Admin', code='admin')
        bootstrap_role_permissions(cls.role_admin)

        # Role in org2
        cls.role_admin2 = AccessRole.objects.create(org=cls.org2, name='Admin', code='admin')
        bootstrap_role_permissions(cls.role_admin2)

        cls.admin_user = User.objects.create_user(username='mgmt_admin', password='pass')
        cls.admin_user.org = cls.org
        cls.admin_user.save()
        UserRoleAssignment.objects.create(
            user=cls.admin_user, role=cls.role_admin, scope_node=cls.n_company
        )

        cls.no_cap_user = User.objects.create_user(username='mgmt_nocap', password='pass')
        cls.no_cap_user.org = cls.org
        cls.no_cap_user.save()

        cls.superuser = User.objects.create_user(
            username='mgmt_super', password='pass', is_superuser=True, is_staff=True
        )

        # A permission to use in tests
        cls.perm_client_read = get_or_create_permission('client.read')
        cls.perm_site_read = get_or_create_permission('site.read')

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── AccessRoleViewSet ────────────────────────────────────────────────────────

class TestAccessRoleViewSet(RBACManagementBase):

    def test_role_read_can_list(self):
        """Scenario 1: role.read can list roles."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/roles/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data['count'], 1)

    def test_no_role_read_gets_403(self):
        """Scenario 2: user without role.read gets 403."""
        self._login(self.no_cap_user)
        resp = self.api.get('/api/access/roles/')
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_gets_401(self):
        """Scenario 3: unauthenticated gets 401."""
        resp = self.api.get('/api/access/roles/')
        self.assertEqual(resp.status_code, 401)

    def test_role_create_returns_201(self):
        """Scenario 4: role.create can create role with 201 and read serializer fields."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/roles/', {
            'name': 'Test Role', 'code': 'test_role_mgmt',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('id', resp.data)
        self.assertIn('created_at', resp.data)
        self.assertEqual(resp.data['code'], 'test_role_mgmt')
        self.assertEqual(resp.data['org'], self.org.pk)

    def test_non_superuser_create_forces_actor_org(self):
        """Scenario 5: non-superuser cannot set a different org."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/roles/', {
            'name': 'Forced Org Role', 'code': 'forced_org_role',
            'org': self.org2.pk,  # attempt to create in org2
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org.pk)  # forced to actor.org

    def test_superuser_create_may_specify_org(self):
        """Scenario 6: superuser may pass org explicitly."""
        self._login(self.superuser)
        resp = self.api.post('/api/access/roles/', {
            'name': 'Super Created', 'code': 'super_created_mgmt',
            'org': self.org2.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org2.pk)

    def test_duplicate_code_per_org_returns_400(self):
        """Scenario 7: duplicate active code per org returns 400."""
        self._login(self.admin_user)
        self.api.post('/api/access/roles/', {
            'name': 'First', 'code': 'dup_code_mgmt',
        }, format='json')
        resp = self.api.post('/api/access/roles/', {
            'name': 'Second', 'code': 'dup_code_mgmt',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', str(resp.data))

    def test_code_normalized_to_lowercase(self):
        """Scenario 8: code is lowercased on create."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/roles/', {
            'name': 'Case Test', 'code': 'UPPER_CASE',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['code'], 'upper_case')

    def test_role_update_can_patch_name(self):
        """Scenario 9: role.update can PATCH name."""
        role = AccessRole.objects.create(org=self.org, name='Patchable', code='patchable_mgmt')
        self._login(self.admin_user)
        resp = self.api.patch(f'/api/access/roles/{role.pk}/', {'name': 'Patched'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['name'], 'Patched')

    def test_role_update_can_patch_is_active(self):
        """Scenario 10: role.update can PATCH is_active."""
        role = AccessRole.objects.create(org=self.org, name='Toggle Role', code='toggle_mgmt')
        self._login(self.admin_user)
        resp = self.api.patch(f'/api/access/roles/{role.pk}/', {'is_active': False}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data['is_active'])
        role.refresh_from_db()
        self.assertFalse(role.is_active)

    def test_role_update_returns_read_serializer(self):
        """Scenario 11: PATCH response includes read-only fields (created_at, updated_at)."""
        role = AccessRole.objects.create(org=self.org, name='Read After Write', code='raw_mgmt')
        self._login(self.admin_user)
        resp = self.api.patch(f'/api/access/roles/{role.pk}/', {'name': 'Updated'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('created_at', resp.data)
        self.assertIn('updated_at', resp.data)

    def test_role_destroy_soft_deactivates(self):
        """Scenario 12: DELETE soft-deactivates (is_active=False), returns 204, row still exists."""
        role = AccessRole.objects.create(org=self.org, name='Deletable', code='deletable_mgmt')
        self._login(self.admin_user)
        resp = self.api.delete(f'/api/access/roles/{role.pk}/')
        self.assertEqual(resp.status_code, 204)
        role.refresh_from_db()
        self.assertFalse(role.is_active)

    def test_non_superuser_cannot_see_other_org_roles(self):
        """Scenario 13: role from another org is hidden for non-superuser."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/roles/')
        self.assertEqual(resp.status_code, 200)
        org_ids = {r['org'] for r in resp.data['results']}
        self.assertNotIn(self.org2.pk, org_ids)

    def test_superuser_sees_all_orgs(self):
        """Scenario 14: superuser sees roles from all orgs."""
        self._login(self.superuser)
        resp = self.api.get('/api/access/roles/')
        self.assertEqual(resp.status_code, 200)
        org_ids = {r['org'] for r in resp.data['results']}
        self.assertIn(self.org.pk, org_ids)
        self.assertIn(self.org2.pk, org_ids)


# ─── PermissionViewSet ────────────────────────────────────────────────────────

class TestPermissionViewSet(RBACManagementBase):

    def test_role_read_can_list_permissions(self):
        """Scenario 15: role.read can list permissions (200)."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/permissions/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data['count'], 1)

    def test_no_role_read_gets_403(self):
        """Scenario 15b: user without role.read gets 403."""
        self._login(self.no_cap_user)
        resp = self.api.get('/api/access/permissions/')
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_gets_401(self):
        """Scenario 15c: unauthenticated gets 401."""
        resp = self.api.get('/api/access/permissions/')
        self.assertEqual(resp.status_code, 401)

    def test_response_includes_code(self):
        """Scenario 16: permission response includes code field."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/permissions/')
        self.assertEqual(resp.status_code, 200)
        first = resp.data['results'][0]
        self.assertIn('code', first)

    def test_workflow_config_read_permission_fields(self):
        """Scenario 17: workflow.config.read has correct code, resource, action."""
        get_or_create_permission('workflow.config.read')
        self._login(self.admin_user)
        resp = self.api.get('/api/access/permissions/?resource=workflow_config')
        self.assertEqual(resp.status_code, 200)
        results = resp.data['results']
        self.assertTrue(any(r['code'] == 'workflow.config.read' for r in results), results)
        wfc_read = next(r for r in results if r['code'] == 'workflow.config.read')
        self.assertEqual(wfc_read['resource'], 'workflow_config')
        self.assertEqual(wfc_read['action'], 'read')

    def test_filter_by_resource(self):
        """Scenario 18: filter by resource returns only matching permissions."""
        get_or_create_permission('mrf.read')
        self._login(self.admin_user)
        resp = self.api.get('/api/access/permissions/?resource=mrf')
        self.assertEqual(resp.status_code, 200)
        for perm in resp.data['results']:
            self.assertEqual(perm['resource'], 'mrf')


# ─── AccessRolePermissionViewSet ──────────────────────────────────────────────

class TestRolePermissionViewSet(RBACManagementBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A role that already has client.read attached (for list/read tests)
        cls.role_with_perm = AccessRole.objects.create(
            org=cls.org, name='Has Perm', code='has_perm_mgmt'
        )
        cls.existing_rp = AccessRolePermission.objects.create(
            role=cls.role_with_perm, permission=cls.perm_client_read,
        )

        # An inactive role
        cls.inactive_role = AccessRole.objects.create(
            org=cls.org, name='Inactive', code='inactive_mgmt', is_active=False
        )

        # A role in org2 with a permission (to test cross-org blocking)
        cls.role_org2 = AccessRole.objects.create(
            org=cls.org2, name='Org2 Role', code='org2_role_mgmt'
        )

    def test_role_read_can_list_role_permissions(self):
        """Scenario 19: role.read can list role-permissions."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/role-permissions/')
        self.assertEqual(resp.status_code, 200)

    def test_role_update_can_attach_permission(self):
        """Scenario 20: role.update can attach a permission (201 with rich response)."""
        target_role = AccessRole.objects.create(org=self.org, name='Attach Target', code='attach_target_mgmt')
        self._login(self.admin_user)
        resp = self.api.post('/api/access/role-permissions/', {
            'role': target_role.pk,
            'permission': self.perm_site_read.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['role'], target_role.pk)
        self.assertEqual(resp.data['role_code'], 'attach_target_mgmt')
        self.assertEqual(resp.data['permission_code'], 'site.read')
        self.assertEqual(resp.data['permission_resource'], 'site')
        self.assertEqual(resp.data['permission_action'], 'read')
        self.assertTrue(
            AccessRolePermission.objects.filter(role=target_role, permission=self.perm_site_read).exists()
        )

    def test_role_update_can_delete_permission_mapping(self):
        """Scenario 21: role.update can DELETE a role-permission mapping (204)."""
        role = AccessRole.objects.create(org=self.org, name='Detach Role', code='detach_role_mgmt')
        rp = AccessRolePermission.objects.create(role=role, permission=self.perm_site_read)
        self._login(self.admin_user)
        resp = self.api.delete(f'/api/access/role-permissions/{rp.pk}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(AccessRolePermission.objects.filter(pk=rp.pk).exists())

    def test_duplicate_attach_returns_400(self):
        """Scenario 22: attaching already-attached permission returns clean 400."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/role-permissions/', {
            'role': self.role_with_perm.pk,
            'permission': self.perm_client_read.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('permission', str(resp.data))

    def test_attach_to_inactive_role_returns_400(self):
        """Scenario 23: attaching permission to an inactive role returns 400."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/role-permissions/', {
            'role': self.inactive_role.pk,
            'permission': self.perm_client_read.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('role', str(resp.data))

    def test_non_superuser_cannot_attach_to_other_org_role(self):
        """Scenario 24: non-superuser gets 403 when attaching to another org's role."""
        self._login(self.admin_user)
        resp = self.api.post('/api/access/role-permissions/', {
            'role': self.role_org2.pk,
            'permission': self.perm_client_read.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_non_superuser_cannot_delete_from_other_org_role(self):
        """Scenario 25: cross-org role-permission is hidden by queryset → 404 on delete."""
        rp = AccessRolePermission.objects.create(
            role=self.role_org2, permission=self.perm_client_read,
        )
        self._login(self.admin_user)
        resp = self.api.delete(f'/api/access/role-permissions/{rp.pk}/')
        # Queryset filters to actor.org — cross-org row is not visible (404)
        self.assertEqual(resp.status_code, 404)

    def test_non_superuser_cannot_see_other_org_role_permissions(self):
        """Scenario 26: non-superuser only sees role-permissions for their org."""
        rp_org2 = AccessRolePermission.objects.create(
            role=self.role_org2, permission=self.perm_site_read,
        )
        self._login(self.admin_user)
        resp = self.api.get('/api/access/role-permissions/')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data['results']}
        self.assertNotIn(rp_org2.pk, ids)

    def test_filter_by_role(self):
        """Scenario 27: filter by role returns only that role's permissions."""
        self._login(self.admin_user)
        resp = self.api.get(f'/api/access/role-permissions/?role={self.role_with_perm.pk}')
        self.assertEqual(resp.status_code, 200)
        for rp in resp.data['results']:
            self.assertEqual(rp['role'], self.role_with_perm.pk)

    def test_filter_by_permission_resource(self):
        """Scenario 28: filter by permission__resource works."""
        self._login(self.admin_user)
        resp = self.api.get('/api/access/role-permissions/?permission__resource=client')
        self.assertEqual(resp.status_code, 200)
        for rp in resp.data['results']:
            self.assertEqual(rp['permission_resource'], 'client')

    def test_runtime_me_reflects_attach_detach(self):
        """Scenario 29: /api/core/me/ capabilities update immediately after attach/detach."""
        # Fresh role with no permissions
        fresh_role = AccessRole.objects.create(
            org=self.org, name='Fresh Role', code='fresh_role_mgmt'
        )
        fresh_user = User.objects.create_user(username='mgmt_fresh', password='pass')
        fresh_user.org = self.org
        fresh_user.save()
        n = ScopeNode.objects.create(
            org=self.org, code='mgmt-fresh-node', name='Fresh', node_type='company',
            parent=None, depth=0, path='mgmt-fresh-node', is_active=True,
        )
        UserRoleAssignment.objects.create(user=fresh_user, role=fresh_role, scope_node=n)

        user_api = APIClient()
        user_api.force_authenticate(user=fresh_user)

        # Before attach — client.read not in capabilities
        resp = user_api.get('/api/core/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('client.read', resp.data['capabilities'])

        # Attach client.read via admin
        self._login(self.admin_user)
        attach_resp = self.api.post('/api/access/role-permissions/', {
            'role': fresh_role.pk,
            'permission': self.perm_client_read.pk,
        }, format='json')
        self.assertEqual(attach_resp.status_code, 201, attach_resp.data)
        rp_id = attach_resp.data['id']

        # After attach — client.read now in capabilities
        resp = user_api.get('/api/core/me/')
        self.assertIn('client.read', resp.data['capabilities'])

        # Detach
        del_resp = self.api.delete(f'/api/access/role-permissions/{rp_id}/')
        self.assertEqual(del_resp.status_code, 204)

        # After detach — client.read gone
        resp = user_api.get('/api/core/me/')
        self.assertNotIn('client.read', resp.data['capabilities'])

"""
apps/access/tests/test_rbac_runtime.py

Phase RBAC-Runtime-A tests — DB-driven capability resolution.

Scenarios:
  1.  User with a DB role-permission gets that capability.
  2.  User with role code in ROLE_CAPABILITIES but no DB row gets nothing.
  3.  Inactive role is ignored.
  4.  Multiple roles union their permissions.
  5.  Superuser gets ALL_CAPABILITIES regardless of DB rows.
  6.  /api/core/me/ returns DB-derived capabilities.
  7.  HasCapability grants API access based on DB permission.
  8.  workflow.config.read resolved from Permission.code.
  9.  workflow.config.manage resolved from Permission.code.
  10. Seed command creates Permission rows for all ALL_CAPABILITIES.
  11. Seed command maps role permissions using Permission.code.
"""

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    ALL_CAPABILITIES,
    ROLE_CAPABILITIES,
    get_user_access_profile,
    get_user_capabilities,
)
from apps.access.models import AccessRole, AccessRolePermission, Permission, UserRoleAssignment
from apps.access.scope import user_has_capability
from apps.access.tests.utils import bootstrap_role_permissions, get_or_create_permission
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode


# ─── Base ─────────────────────────────────────────────────────────────────────

class RBACRuntimeBase(TestCase):
    """
    Minimal base: one org + company scope node. No pre-created roles or permissions.
    Each test builds exactly what it needs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='RBAC Test Org', code='rbac-test')
        cls.n_company = ScopeNode.objects.create(
            org=cls.org, code='rbac-test', name='RBAC Test',
            node_type='company', parent=None, depth=0, path='rbac-test', is_active=True,
        )

    def _make_role(self, code, is_active=True):
        role, _ = AccessRole.objects.get_or_create(
            org=self.org, code=code,
            defaults={'name': code, 'is_active': is_active},
        )
        if not is_active:
            role.is_active = False
            role.save(update_fields=['is_active'])
        return role

    def _make_user(self, username, role=None, is_superuser=False):
        u = User.objects.create_user(username=username, password='pass',
                                     is_superuser=is_superuser)
        u.org = self.org
        u.save()
        if role:
            UserRoleAssignment.objects.create(user=u, role=role, scope_node=self.n_company)
        return u

    def _grant(self, role, cap):
        """Create Permission + AccessRolePermission for the given capability."""
        perm = get_or_create_permission(cap)
        AccessRolePermission.objects.get_or_create(role=role, permission=perm)
        return perm


# ─── Core DB capability resolution ────────────────────────────────────────────

class TestDBCapabilityResolution(RBACRuntimeBase):

    def test_user_with_db_permission_gets_capability(self):
        """Scenario 1: DB row is the source — not ROLE_CAPABILITIES."""
        role = self._make_role('custom_role_1')
        self._grant(role, 'mrf.read')
        user = self._make_user('rbac1', role=role)
        caps = get_user_capabilities(user)
        self.assertIn('mrf.read', caps)

    def test_role_code_in_role_capabilities_without_db_row_gets_nothing(self):
        """Scenario 2: hr_admin in ROLE_CAPABILITIES but no AccessRolePermission → empty."""
        # Create a role with code hr_admin but intentionally add NO DB permissions.
        role = self._make_role('hr_admin')
        user = self._make_user('rbac2', role=role)
        caps = get_user_capabilities(user)
        # mrf.read is in ROLE_CAPABILITIES['hr_admin'] but no DB row → not returned
        self.assertNotIn('mrf.read', caps)
        self.assertEqual(caps, [])

    def test_inactive_role_is_ignored(self):
        """Scenario 3: role with is_active=False contributes no capabilities."""
        role = self._make_role('inactive_r', is_active=False)
        self._grant(role, 'site.read')
        user = self._make_user('rbac3', role=role)
        caps = get_user_capabilities(user)
        self.assertNotIn('site.read', caps)
        self.assertEqual(caps, [])

    def test_multiple_roles_union_permissions(self):
        """Scenario 4: capabilities from all active roles are unioned."""
        role_a = self._make_role('multi_a')
        role_b = self._make_role('multi_b')
        self._grant(role_a, 'mrf.read')
        self._grant(role_b, 'site.read')
        user = self._make_user('rbac4')
        UserRoleAssignment.objects.create(user=user, role=role_a, scope_node=self.n_company)
        UserRoleAssignment.objects.create(user=user, role=role_b, scope_node=self.n_company)
        caps = get_user_capabilities(user)
        self.assertIn('mrf.read', caps)
        self.assertIn('site.read', caps)

    def test_superuser_gets_all_capabilities(self):
        """Scenario 5: superuser bypasses DB and returns ALL_CAPABILITIES."""
        superuser = self._make_user('rbac_super5', is_superuser=True)
        caps = get_user_capabilities(superuser)
        self.assertEqual(caps, sorted(ALL_CAPABILITIES))

    def test_user_has_capability_uses_db(self):
        """user_has_capability() reflects DB, not ROLE_CAPABILITIES."""
        role = self._make_role('uchk_role')
        self._grant(role, 'client.read')
        user = self._make_user('rbac_uchk', role=role)
        self.assertTrue(user_has_capability(user, 'client.read'))
        self.assertFalse(user_has_capability(user, 'mrf.read'))

    def test_client_facing_role_presets_do_not_include_department_read(self):
        """Client portal roles should not expose internal department setup."""
        for role_code in ('client_admin', 'client_site_user', 'site_supervisor', 'client_user'):
            with self.subTest(role_code=role_code):
                self.assertNotIn('department.read', ROLE_CAPABILITIES.get(role_code, []))


# ─── API-level enforcement ─────────────────────────────────────────────────────

class TestAccessProfileDerivation(RBACRuntimeBase):

    def test_client_role_gets_client_portal_profile(self):
        role = self._make_role('client_admin')
        user = self._make_user('profile_client', role=role)

        profile = get_user_access_profile(user)

        self.assertTrue(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'client')
        self.assertEqual(profile['nav_persona'], 'client')
        self.assertEqual(profile['primary_role_codes'], ['client_admin'])

    def test_sales_role_gets_sales_persona(self):
        role = self._make_role('sales_manager')
        user = self._make_user('profile_sales', role=role)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'sales')

    def test_operations_role_gets_operations_persona(self):
        role = self._make_role('operations_manager')
        user = self._make_user('profile_ops', role=role)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'operations')

    def test_finance_role_gets_finance_persona(self):
        role = self._make_role('finance_manager')
        user = self._make_user('profile_finance', role=role)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'finance')

    def test_hr_role_gets_hr_persona(self):
        role = self._make_role('hr_admin')
        user = self._make_user('profile_hr', role=role)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'hr')

    def test_superuser_gets_admin_persona(self):
        user = self._make_user('profile_superuser', is_superuser=True)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'admin')
        self.assertEqual(profile['primary_role_codes'], [])

    def test_mixed_internal_roles_get_mixed_persona(self):
        sales_role = self._make_role('sales_manager')
        finance_role = self._make_role('finance_manager')
        user = self._make_user('profile_mixed')
        UserRoleAssignment.objects.create(user=user, role=sales_role, scope_node=self.n_company)
        UserRoleAssignment.objects.create(user=user, role=finance_role, scope_node=self.n_company)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'mixed')
        self.assertEqual(profile['primary_role_codes'], ['finance_manager', 'sales_manager'])

    def test_mixed_client_and_internal_roles_get_internal_persona(self):
        client_role = self._make_role('client_admin')
        sales_role = self._make_role('sales_manager')
        user = self._make_user('profile_client_internal')
        UserRoleAssignment.objects.create(user=user, role=client_role, scope_node=self.n_company)
        UserRoleAssignment.objects.create(user=user, role=sales_role, scope_node=self.n_company)

        profile = get_user_access_profile(user)

        self.assertFalse(profile['is_client_facing'])
        self.assertEqual(profile['portal_mode'], 'internal')
        self.assertEqual(profile['nav_persona'], 'sales')
        self.assertEqual(profile['primary_role_codes'], ['client_admin', 'sales_manager'])


class TestAPIEnforcementFromDB(RBACRuntimeBase):

    def test_me_endpoint_returns_db_capabilities(self):
        """Scenario 6: /api/core/me/ reflects DB-sourced capabilities."""
        role = self._make_role('me_role_6')
        self._grant(role, 'client.read')
        user = self._make_user('rbac6', role=role)
        api = APIClient()
        api.force_authenticate(user=user)
        resp = api.get('/api/core/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('client.read', resp.data['capabilities'])
        self.assertNotIn('mrf.read', resp.data['capabilities'])

    def test_me_endpoint_returns_access_profile(self):
        role = self._make_role('operations_manager')
        user = self._make_user('rbac_me_profile', role=role)
        api = APIClient()
        api.force_authenticate(user=user)

        resp = api.get('/api/core/me/')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_client_facing'])
        self.assertEqual(resp.data['portal_mode'], 'internal')
        self.assertEqual(resp.data['nav_persona'], 'operations')
        self.assertEqual(resp.data['primary_role_codes'], ['operations_manager'])

    def test_has_capability_allows_from_db_permission(self):
        """Scenario 7: API endpoint gated on site.read grants access when DB row exists."""
        role = self._make_role('site_role_7')
        self._grant(role, 'site.read')
        user = self._make_user('rbac7', role=role)
        api = APIClient()
        api.force_authenticate(user=user)
        resp = api.get('/api/sites/profiles/')
        # With site.read in DB → NOT 403 (may be 200 with empty list)
        self.assertNotEqual(resp.status_code, 403)

    def test_has_capability_denies_without_db_permission(self):
        """HasCapability returns 403 when no DB row exists, even if ROLE_CAPABILITIES has it."""
        # Create hr_admin role (which has site.read in ROLE_CAPABILITIES) but no DB perm.
        role = self._make_role('no_db_role_7b')
        user = self._make_user('rbac7b', role=role)
        api = APIClient()
        api.force_authenticate(user=user)
        resp = api.get('/api/sites/profiles/')
        self.assertEqual(resp.status_code, 403)


# ─── Three-part capability strings ────────────────────────────────────────────

class TestWorkflowConfigCapabilities(RBACRuntimeBase):

    def test_workflow_config_read_from_db(self):
        """Scenario 8: workflow.config.read resolved via Permission.code."""
        role = self._make_role('wfc_read_8')
        self._grant(role, 'workflow.config.read')
        user = self._make_user('rbac8', role=role)
        caps = get_user_capabilities(user)
        self.assertIn('workflow.config.read', caps)

    def test_workflow_config_manage_from_db(self):
        """Scenario 9: workflow.config.manage resolved via Permission.code."""
        role = self._make_role('wfc_manage_9')
        self._grant(role, 'workflow.config.manage')
        user = self._make_user('rbac9', role=role)
        caps = get_user_capabilities(user)
        self.assertIn('workflow.config.manage', caps)

    def test_workflow_config_permission_stored_correctly(self):
        """Permission.code is the canonical 3-part string; resource uses underscore."""
        perm = get_or_create_permission('workflow.config.read')
        self.assertEqual(perm.code, 'workflow.config.read')
        self.assertEqual(perm.resource, 'workflow_config')
        self.assertEqual(perm.action, 'read')

    def test_workflow_config_api_allowed_with_db_permission(self):
        """workflow.config.read grants access to /api/workflow/config/flows/."""
        role = self._make_role('wfc_api_9b')
        self._grant(role, 'workflow.config.read')
        user = self._make_user('rbac9b', role=role)
        api = APIClient()
        api.force_authenticate(user=user)
        resp = api.get('/api/workflow/config/flows/')
        self.assertNotEqual(resp.status_code, 403)

    def test_workflow_config_api_denied_without_db_permission(self):
        """No DB permission → 403 even if role code would match ROLE_CAPABILITIES."""
        role = self._make_role('wfc_api_9c')
        user = self._make_user('rbac9c', role=role)
        api = APIClient()
        api.force_authenticate(user=user)
        resp = api.get('/api/workflow/config/flows/')
        self.assertEqual(resp.status_code, 403)


# ─── Seed command ─────────────────────────────────────────────────────────────

class TestSeedCreatesPermissions(RBACRuntimeBase):
    """Tests 10-11: seed_foundation creates correct Permission + AccessRolePermission rows."""

    def _run_seed_permissions(self):
        """Call just the permission + role_permission seeding part of seed_foundation."""
        seed_path = Path(__file__).resolve().parents[2] / 'ServerUAT' / 'seed_foundation.py'
        spec = spec_from_file_location('_test_server_uat_seed_foundation', seed_path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        Command = module.Command

        cmd = Command()
        cmd.stdout = type('FakeStdout', (), {'write': lambda self, x: None})()

        roles = {}
        for code in ('admin', 'hr_admin', 'finance', 'field_supervisor', 'sales_manager'):
            role, _ = AccessRole.objects.get_or_create(
                org=self.org, code=code, defaults={'name': code}
            )
            roles[code] = role

        permissions = cmd._seed_permissions()
        cmd._seed_role_permissions(roles, permissions)
        return roles, permissions

    def test_seed_creates_permission_for_every_capability(self):
        """Scenario 10: after seeding, every ALL_CAPABILITIES entry has a Permission.code row."""
        self._run_seed_permissions()
        for cap in ALL_CAPABILITIES:
            self.assertTrue(
                Permission.objects.filter(code=cap).exists(),
                f'Missing Permission row for capability: {cap}',
            )

    def test_seed_creates_workflow_config_permissions(self):
        """workflow.config.read and workflow.config.manage get proper Permission rows."""
        self._run_seed_permissions()
        wfc_read = Permission.objects.get(code='workflow.config.read')
        self.assertEqual(wfc_read.resource, 'workflow_config')
        self.assertEqual(wfc_read.action, 'read')

        wfc_manage = Permission.objects.get(code='workflow.config.manage')
        self.assertEqual(wfc_manage.resource, 'workflow_config')
        self.assertEqual(wfc_manage.action, 'manage')

    def test_seed_maps_role_permissions_via_code(self):
        """Scenario 11: AccessRolePermission rows exist for all ROLE_CAPABILITIES entries."""
        roles, _ = self._run_seed_permissions()
        for role_code, role_obj in roles.items():
            caps = ROLE_CAPABILITIES.get(role_code, [])
            db_caps = set(
                AccessRolePermission.objects
                .filter(role=role_obj)
                .values_list('permission__code', flat=True)
            )
            for cap in caps:
                self.assertIn(cap, db_caps,
                              f'Role {role_code} missing DB permission for {cap}')

    def test_sales_manager_preset_excludes_non_sales_workstreams(self):
        """Sales manager should not inherit MRF/ATS/workflow/admin workstreams."""
        sales_caps = set(ROLE_CAPABILITIES.get('sales_manager', []))

        forbidden = {
            'mrf.read',
            'mrf.create',
            'workflow.start_workflow',
            'workflow.config.read',
            'candidate.read',
            'submission.read',
            'department.read',
            'user.read',
            'site_role_requirement.read',
            'sales_proposal.approve',
            'sales_survey.update',
            'sales_survey.assign',
            'client_onboarding.read',
        }
        self.assertFalse(sales_caps & forbidden)

    def test_seed_removes_stale_role_permissions(self):
        """Rerunning ServerUAT foundation sync removes DB permissions no longer in the preset."""
        roles, permissions = self._run_seed_permissions()
        sales_role = roles['sales_manager']
        stale_permission = permissions['mrf.read']

        AccessRolePermission.objects.get_or_create(
            role=sales_role,
            permission=stale_permission,
        )
        self.assertTrue(
            AccessRolePermission.objects.filter(
                role=sales_role,
                permission=stale_permission,
            ).exists()
        )

        self._run_seed_permissions()

        self.assertFalse(
            AccessRolePermission.objects.filter(
                role=sales_role,
                permission=stale_permission,
            ).exists()
        )

    def test_seed_idempotent_for_permissions(self):
        """Running seed twice does not create duplicate Permission rows."""
        self._run_seed_permissions()
        count_after_first = Permission.objects.count()
        self._run_seed_permissions()
        count_after_second = Permission.objects.count()
        self.assertEqual(count_after_first, count_after_second)

    def test_user_gets_capability_after_seeding(self):
        """End-to-end: seed → assign role → get_user_capabilities returns seeded caps."""
        roles, _ = self._run_seed_permissions()
        hr_role = roles['hr_admin']
        user = self._make_user('rbac_seed_e2e', role=hr_role)
        caps = get_user_capabilities(user)
        self.assertIn('mrf.read', caps)
        self.assertIn('workflow.config.read', caps)
        self.assertIn('workflow.config.manage', caps)

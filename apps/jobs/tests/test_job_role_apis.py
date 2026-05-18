"""
apps/jobs/tests/test_job_role_apis.py

Tests for JobRole CRUD API.

Scenarios:
  1.  Unauthenticated list → 401
  2.  User with job_role.read can list
  3.  User without job_role.read → 403
  4.  Create with job_role.create → 201, read serializer returned
  5.  Create without job_role.create → 403
  6.  PATCH with job_role.update → 200
  7.  PATCH without job_role.update → 403
  8.  DELETE with job_role.delete → 200, is_active=False (soft delete)
  9.  DELETE without job_role.delete → 403
  10. Duplicate code returns clean 400
  11. Filter by is_active=false returns only inactive roles
  12. Superuser can access all org roles
  13. Code is normalized to lowercase on create
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole


URL = '/api/jobs/roles/'


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


def _job_role(org, name, code, skill_category='unskilled', is_active=True):
    return JobRole.objects.create(
        org=org, name=name, code=code,
        skill_category=skill_category, is_active=is_active,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class JobRoleTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='JR Test Org', code='jr-org')
        cls.org2 = Organization.objects.create(name='JR Other Org', code='jr-org2')

        cls.n_company = _node(cls.org, 'jr-org', 'company', None, 0, 'jr-org')

        # Roles
        cls.role_admin = _role(cls.org, 'admin')
        cls.role_hr = _role(cls.org, 'hr_executive')
        bootstrap_role_permissions(cls.role_admin)
        bootstrap_role_permissions(cls.role_hr)

        # Users
        cls.superuser = _user('jr_super', is_superuser=True)

        cls.admin = _user('jr_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.hr = _user('jr_hr', org=cls.org)
        _assign(cls.hr, cls.role_hr, cls.n_company)

        cls.noauth = _user('jr_noauth', org=cls.org)  # no role assignment → no capabilities

        # Seed a job role for org2
        cls.other_role = _job_role(cls.org2, 'Guard Org2', 'guard-org2')

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestJobRoleList(JobRoleTestBase):

    def test_unauthenticated_returns_401(self):
        resp = self._api().get(URL)
        self.assertEqual(resp.status_code, 401)

    def test_user_with_read_can_list(self):
        # hr_executive has job_role.read
        resp = self._api(self.hr).get(URL)
        self.assertEqual(resp.status_code, 200)

    def test_user_without_read_gets_403(self):
        resp = self._api(self.noauth).get(URL)
        self.assertEqual(resp.status_code, 403)

    def test_list_scoped_to_own_org(self):
        _job_role(self.org, 'Security', 'security-jr')
        resp = self._api(self.hr).get(URL)
        self.assertEqual(resp.status_code, 200)
        codes = [r['code'] for r in resp.data['results']]
        self.assertIn('security-jr', codes)
        # org2 role must not appear
        self.assertNotIn('guard-org2', codes)

    def test_superuser_sees_all_orgs(self):
        resp = self._api(self.superuser).get(URL)
        self.assertEqual(resp.status_code, 200)
        codes = [r['code'] for r in resp.data['results']]
        self.assertIn('guard-org2', codes)

    def test_filter_by_is_active_false(self):
        inactive = _job_role(self.org, 'Old Role', 'old-role-jr', is_active=False)
        resp = self._api(self.hr).get(URL, {'is_active': 'false'})
        self.assertEqual(resp.status_code, 200)
        codes = [r['code'] for r in resp.data['results']]
        self.assertIn('old-role-jr', codes)


class TestJobRoleCreate(JobRoleTestBase):

    def test_admin_can_create(self):
        resp = self._api(self.admin).post(URL, {
            'name': 'Electrician JR',
            'code': 'electrician-jr',
            'skill_category': 'skilled',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('id', resp.data)
        self.assertEqual(resp.data['code'], 'electrician-jr')
        self.assertEqual(resp.data['skill_category'], 'skilled')
        self.assertIn('skill_category_display', resp.data)

    def test_create_without_capability_gets_403(self):
        resp = self._api(self.hr).post(URL, {
            'name': 'Plumber JR',
            'code': 'plumber-jr',
            'skill_category': 'skilled',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_code_normalized_to_lowercase(self):
        resp = self._api(self.admin).post(URL, {
            'name': 'Supervisor JR',
            'code': 'SUPERVISOR-JR',
            'skill_category': 'supervisor',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['code'], 'supervisor-jr')

    def test_duplicate_code_returns_400(self):
        _job_role(self.org, 'Guard JR', 'guard-jr')
        resp = self._api(self.admin).post(URL, {
            'name': 'Guard JR Copy',
            'code': 'guard-jr',
            'skill_category': 'unskilled',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', resp.data)

    def test_org_injected_from_actor(self):
        resp = self._api(self.admin).post(URL, {
            'name': 'Housekeeper JR',
            'code': 'housekeeper-jr',
            'skill_category': 'unskilled',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        role = JobRole.objects.get(pk=resp.data['id'])
        self.assertEqual(role.org_id, self.org.pk)


class TestJobRoleUpdate(JobRoleTestBase):

    def setUp(self):
        self.role = _job_role(self.org, 'Patch Me', 'patch-me-jr')

    def test_admin_can_patch(self):
        resp = self._api(self.admin).patch(f'{URL}{self.role.pk}/', {
            'name': 'Patched Name',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['name'], 'Patched Name')

    def test_patch_without_capability_gets_403(self):
        resp = self._api(self.hr).patch(f'{URL}{self.role.pk}/', {
            'name': 'Should Fail',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_update_returns_read_serializer(self):
        resp = self._api(self.admin).patch(f'{URL}{self.role.pk}/', {
            'skill_category': 'skilled',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        # Read serializer includes skill_category_display
        self.assertIn('skill_category_display', resp.data)


class TestJobRoleDelete(JobRoleTestBase):

    def setUp(self):
        self.role = _job_role(self.org, 'Delete Me', 'delete-me-jr')

    def test_admin_can_soft_delete(self):
        resp = self._api(self.admin).delete(f'{URL}{self.role.pk}/')
        self.assertEqual(resp.status_code, 204, resp.data)
        self.role.refresh_from_db()
        self.assertFalse(self.role.is_active)

    def test_delete_without_capability_gets_403(self):
        resp = self._api(self.hr).delete(f'{URL}{self.role.pk}/')
        self.assertEqual(resp.status_code, 403)

    def test_soft_deleted_role_still_in_db(self):
        self._api(self.admin).delete(f'{URL}{self.role.pk}/')
        self.assertTrue(JobRole.objects.filter(pk=self.role.pk).exists())

"""
apps/core/tests/test_department_apis.py

Tests for Department model and CRUD API (17 scenarios from Phase D1 spec).

Fixture layout:
  org: Test Org Dept (code: tl-dept)
  org2: Other Org (code: other-dept) — for cross-org checks
  scope tree:
    tl-dept (company)
    tl-dept/client-a (client)
    tl-dept/client-a/site-1 (site)
    tl-dept/client-b (client)

  users:
    hr_admin_user  — role=hr_admin @ company root   (department.* caps)
    reader_user    — role=hr_executive @ company root (department.read only)
    no_cap_user    — role=client_user @ client-a     (no department caps)
    superuser      — Django superuser
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode, Department
from apps.sites.models import Client, SiteProfile


# ─── Helpers ─────────────────────────────────────────────────────────────────

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


def _dept(org, name, code, client=None, site=None, is_active=True):
    return Department.objects.create(
        org=org, name=name, code=code,
        client=client, site=site, is_active=is_active,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class DeptTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Test Org Dept', code='tl-dept')
        cls.org2 = Organization.objects.create(name='Other Org', code='other-dept')

        # scope tree for org
        cls.n_company = _node(cls.org, 'tl-dept', 'company', None, 0, 'tl-dept')
        cls.n_client_a = _node(cls.org, 'client-a-d', 'client', cls.n_company, 1, 'tl-dept/client-a-d')
        cls.n_site_1 = _node(cls.org, 'site-1-d', 'site', cls.n_client_a, 2, 'tl-dept/client-a-d/site-1-d')
        cls.n_client_b = _node(cls.org, 'client-b-d', 'client', cls.n_company, 1, 'tl-dept/client-b-d')

        # scope tree for org2
        cls.n2_company = _node(cls.org2, 'other-dept', 'company', None, 0, 'other-dept')

        # client/site objects
        cls.client_a = Client.objects.create(org=cls.org, name='Client A', code='client-a-dept')
        cls.client_b = Client.objects.create(org=cls.org, name='Client B', code='client-b-dept')
        cls.site_1 = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, name='Site 1', code='site-1-dept',
        )
        # org2 client/site — for cross-org mismatch tests
        cls.client_x = Client.objects.create(org=cls.org2, name='Client X', code='client-x-dept')
        cls.site_x = SiteProfile.objects.create(
            org=cls.org2, client=cls.client_x, name='Site X', code='site-x-dept',
        )

        # roles
        cls.role_hr_admin = _role(cls.org, 'hr_admin')
        cls.role_hr_exec = _role(cls.org, 'hr_executive')
        cls.role_client_user = _role(cls.org, 'client_user')

        # users
        cls.superuser = _user('dept_superuser', is_superuser=True)
        cls.hr_admin_user = _user('dept_hr_admin', org=cls.org)
        cls.reader_user = _user('dept_reader', org=cls.org)
        cls.no_cap_user = _user('dept_no_cap', org=cls.org)

        _assign(cls.hr_admin_user, cls.role_hr_admin, cls.n_company)
        _assign(cls.reader_user, cls.role_hr_exec, cls.n_company)
        _assign(cls.no_cap_user, cls.role_client_user, cls.n_client_a)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)

    def _url(self, pk=None):
        base = '/api/core/departments/'
        return f'{base}{pk}/' if pk else base


# ─── 1–3: Scope creation ─────────────────────────────────────────────────────

class TestDepartmentScopeCreate(DeptTestBase):

    def test_create_org_level_department(self):
        """Scenario 1: org-level department (client null, site null)."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {'name': 'HR', 'code': 'hr'}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['code'], 'hr')
        self.assertIsNone(resp.data['client'])
        self.assertIsNone(resp.data['site'])
        self.assertEqual(resp.data['org'], self.org.pk)

    def test_create_client_level_department(self):
        """Scenario 2: client-level department."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Client Ops',
            'code': 'client-ops',
            'client': self.client_a.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client'], self.client_a.pk)
        self.assertIsNone(resp.data['site'])

    def test_create_site_level_department(self):
        """Scenario 3: site-level department."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Site Ops',
            'code': 'site-ops',
            'site': self.site_1.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['site'], self.site_1.pk)
        # Client must be auto-filled
        self.assertEqual(resp.data['client'], self.client_a.pk)


# ─── 4: Auto-fill client from site ───────────────────────────────────────────

class TestDepartmentClientAutofill(DeptTestBase):

    def test_site_level_auto_fills_client(self):
        """Scenario 4: passing site without client → client auto-filled from site.client."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Auto Fill Dept',
            'code': 'autofill',
            'site': self.site_1.pk,
            # client NOT provided
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client'], self.client_a.pk)

    def test_site_level_explicit_client_must_match(self):
        """Scenario 4 validation: explicit client that doesn't match site.client → 400."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Mismatch Dept',
            'code': 'mismatch-cl',
            'site': self.site_1.pk,
            'client': self.client_b.pk,  # wrong client
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', resp.data)


# ─── 5–6: Org mismatch rejection ─────────────────────────────────────────────

class TestDepartmentOrgMismatch(DeptTestBase):

    def test_client_org_mismatch_rejected(self):
        """Scenario 5: client belongs to org2 but user is in org → 400."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Bad Client Dept',
            'code': 'bad-client-dept',
            'client': self.client_x.pk,  # org2 client
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', resp.data)

    def test_site_org_mismatch_rejected(self):
        """Scenario 6: site belongs to org2 → 400."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Bad Site Dept',
            'code': 'bad-site-dept',
            'site': self.site_x.pk,  # org2 site
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', resp.data)


# ─── 7–8: Uniqueness ─────────────────────────────────────────────────────────

class TestDepartmentCodeUniqueness(DeptTestBase):

    def test_duplicate_active_org_code_rejected(self):
        """Scenario 7: duplicate active code at org scope → 400."""
        _dept(self.org, 'Finance', 'finance-uniq')
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Finance 2',
            'code': 'finance-uniq',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', str(resp.data))

    def test_same_code_allowed_at_different_scopes(self):
        """Scenario 8: same code at org level and client level → both OK."""
        _dept(self.org, 'Ops Org', 'ops-scope-test')
        self._login(self.hr_admin_user)
        # Same code but client-scoped
        resp = self.api.post(self._url(), {
            'name': 'Ops Client',
            'code': 'ops-scope-test',
            'client': self.client_a.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_same_code_client_and_site_level(self):
        """Same code at client and site level → both OK."""
        _dept(self.org, 'Ops Client', 'ops-multi-scope', client=self.client_a)
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'Ops Site',
            'code': 'ops-multi-scope',
            'site': self.site_1.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── 9: Soft delete ──────────────────────────────────────────────────────────

class TestDepartmentSoftDelete(DeptTestBase):

    def test_delete_sets_is_active_false(self):
        """Scenario 9: DELETE → is_active=False, row not removed."""
        dept = _dept(self.org, 'To Delete', 'to-delete-dept')
        self._login(self.hr_admin_user)
        resp = self.api.delete(self._url(dept.pk))
        self.assertEqual(resp.status_code, 204)
        dept.refresh_from_db()
        self.assertFalse(dept.is_active)

    def test_delete_requires_department_delete_capability(self):
        """Reader (department.read only) cannot delete."""
        dept = _dept(self.org, 'No Delete', 'no-delete-dept')
        self._login(self.reader_user)
        resp = self.api.delete(self._url(dept.pk))
        self.assertEqual(resp.status_code, 403)


# ─── 10: Inactive does not block replacement ──────────────────────────────────

class TestDepartmentInactiveAllowsReplacement(DeptTestBase):

    def test_inactive_does_not_block_new_active_at_same_scope(self):
        """Scenario 10: deactivated code allows new active department with same code."""
        _dept(self.org, 'Old HR', 'hr-inactive-test', is_active=False)
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {
            'name': 'New HR',
            'code': 'hr-inactive-test',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['is_active'], True)


# ─── 11: List / search / filter ──────────────────────────────────────────────

class TestDepartmentListFilter(DeptTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.d_hr = _dept(cls.org, 'Human Resources', 'hr-list')
        cls.d_fin = _dept(cls.org, 'Finance', 'fin-list', is_active=False)
        cls.d_client = _dept(cls.org, 'Client Dept', 'client-dept-list', client=cls.client_a)

    def test_list_returns_org_departments(self):
        """Scenario 11: list returns org-filtered departments."""
        self._login(self.hr_admin_user)
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 200)
        codes = {d['code'] for d in resp.data['results']}
        self.assertIn('hr-list', codes)
        self.assertIn('fin-list', codes)

    def test_filter_by_is_active(self):
        """Filter is_active=True returns only active records."""
        self._login(self.hr_admin_user)
        resp = self.api.get(self._url() + '?is_active=true')
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertTrue(item['is_active'])

    def test_filter_by_client(self):
        """Filter by client FK."""
        self._login(self.hr_admin_user)
        resp = self.api.get(self._url() + f'?client={self.client_a.pk}')
        self.assertEqual(resp.status_code, 200)
        codes = {d['code'] for d in resp.data['results']}
        self.assertIn('client-dept-list', codes)
        self.assertNotIn('hr-list', codes)

    def test_search_by_name(self):
        """Search returns matching department names."""
        self._login(self.hr_admin_user)
        resp = self.api.get(self._url() + '?search=Human')
        self.assertEqual(resp.status_code, 200)
        codes = {d['code'] for d in resp.data['results']}
        self.assertIn('hr-list', codes)


# ─── 12: Capability gates ─────────────────────────────────────────────────────

class TestDepartmentCapabilityGates(DeptTestBase):

    def test_unauthenticated_returns_401(self):
        """Scenario 12: no auth → 401."""
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 401)

    def test_no_department_capability_returns_403(self):
        """Scenario 12: no department capability → 403."""
        self._login(self.no_cap_user)
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_department_read_allows_list(self):
        """Scenario 12: department.read → 200 on list."""
        self._login(self.reader_user)
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 200)

    def test_department_read_only_cannot_create(self):
        """Scenario 12: department.read (no create) → 403 on POST."""
        self._login(self.reader_user)
        resp = self.api.post(self._url(), {'name': 'Try', 'code': 'try-cap'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_department_read_only_cannot_update(self):
        """Scenario 12: department.read only → 403 on PATCH."""
        dept = _dept(self.org, 'Lock', 'lock-dept')
        self._login(self.reader_user)
        resp = self.api.patch(self._url(dept.pk), {'name': 'Locked'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_hr_admin_can_create_update_delete(self):
        """Scenario 12: hr_admin has all department capabilities."""
        self._login(self.hr_admin_user)
        resp = self.api.post(self._url(), {'name': 'Cap Test', 'code': 'cap-test-dept'}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        pk = resp.data['id']

        resp = self.api.patch(self._url(pk), {'name': 'Cap Test Updated'}, format='json')
        self.assertEqual(resp.status_code, 200)

        resp = self.api.delete(self._url(pk))
        self.assertEqual(resp.status_code, 204)

    def test_superuser_can_access_all(self):
        """Superuser bypasses capability checks."""
        self._login(self.superuser)
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 200)


# ─── 13–17: User integration ─────────────────────────────────────────────────

class TestUserDepartmentIntegration(DeptTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.role_admin = AccessRole.objects.get_or_create(
            org=cls.org, code='admin', defaults={'name': 'admin'}
        )[0]
        cls.admin_user = _user('dept_int_admin', org=cls.org)
        _assign(cls.admin_user, cls.role_admin, cls.n_company)

        cls.dept_hr = _dept(cls.org, 'HR Int', 'hr-int')
        cls.dept_fin = _dept(cls.org, 'Finance Int', 'fin-int')

        # org2 setup
        cls.role_admin2 = AccessRole.objects.get_or_create(
            org=cls.org2, code='admin', defaults={'name': 'admin'}
        )[0]
        cls.dept_org2 = _dept(cls.org2, 'Org2 HR', 'org2-hr')

    def _user_url(self, pk=None):
        base = '/api/accounts/users/'
        return f'{base}{pk}/' if pk else base

    def test_create_user_with_department(self):
        """Scenario 13: create user with department FK → department set correctly."""
        self._login(self.admin_user)
        resp = self.api.post(self._user_url(), {
            'username': 'user_with_dept',
            'user_type': 'internal',
            'is_active': True,
            'department': self.dept_hr.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        created = User.objects.get(username='user_with_dept')
        self.assertEqual(created.department_id, self.dept_hr.pk)

    def test_update_user_department(self):
        """Scenario 14: PATCH user department → updated successfully."""
        target = User(username='target_dept_upd', org=self.org, user_type='internal')
        target.set_unusable_password()
        target.department = self.dept_hr
        target.save()

        self._login(self.admin_user)
        resp = self.api.patch(self._user_url(target.pk), {
            'department': self.dept_fin.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        target.refresh_from_db()
        self.assertEqual(target.department_id, self.dept_fin.pk)

    def test_department_from_different_org_rejected(self):
        """Scenario 15: department.org != user.org → 400."""
        self._login(self.admin_user)
        resp = self.api.post(self._user_url(), {
            'username': 'user_wrong_dept',
            'user_type': 'internal',
            'is_active': True,
            'department': self.dept_org2.pk,  # org2 department
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', str(resp.data))

    def test_user_list_filter_by_department(self):
        """Scenario 16: filter users by department FK."""
        u = User(username='dept_filter_user', org=self.org, user_type='internal')
        u.set_unusable_password()
        u.department = self.dept_hr
        u.save()

        self._login(self.admin_user)
        resp = self.api.get(self._user_url() + f'?department={self.dept_hr.pk}')
        self.assertEqual(resp.status_code, 200)
        usernames = {u['username'] for u in resp.data['results']}
        self.assertIn('dept_filter_user', usernames)

    def test_user_read_returns_department_fields(self):
        """Scenario 17: user list/retrieve includes department, department_name, department_code."""
        u = User(username='dept_read_user', org=self.org, user_type='internal')
        u.set_unusable_password()
        u.department = self.dept_hr
        u.save()

        self._login(self.admin_user)
        resp = self.api.get(self._user_url() + f'{u.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['department'], self.dept_hr.pk)
        self.assertEqual(resp.data['department_name'], self.dept_hr.name)
        self.assertEqual(resp.data['department_code'], self.dept_hr.code)

    def test_update_department_null_clears_it(self):
        """PATCH department=null clears the department FK."""
        u = User(username='dept_clear_user', org=self.org, user_type='internal')
        u.set_unusable_password()
        u.department = self.dept_hr
        u.save()

        self._login(self.admin_user)
        resp = self.api.patch(self._user_url(u.pk), {'department': None}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        u.refresh_from_db()
        self.assertIsNone(u.department_id)

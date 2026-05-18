"""
apps/sites/tests/test_srr_department.py

Tests for Phase MRF-SRR-Department-A:
  SRR.department FK, serializer exposure, write validation, and filtering.

Scenarios (D01–D15):
  D01  SRR created without department (nullable, backward-compat)
  D02  SRR created with org-level department — accepted
  D03  SRR created with client-level department (same client) — accepted
  D04  SRR created with site-level department (same site) — accepted
  D05  SRR rejects department from a different org
  D06  SRR rejects department scoped to different site
  D07  SRR rejects department scoped to different client
  D08  Read serializer exposes department, department_name, department_code
  D09  Read serializer exposes site_name, job_role_name, job_role_code
  D10  Read serializer exposes wage_category_name, wage_category_code, location_area_name
  D11  Filter ?department=<id> returns only matching SRRs
  D12  Filter ?site=X&department=Y returns intersection
  D13  Filter ?department=<id> with no match returns empty list
  D14  PATCH can update department to null (clear)
  D15  PATCH can update department from null to valid department
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode, Department
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.wages.models import WageCategory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass',
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


def _srr(site, job_role, department=None, **kwargs):
    defaults = dict(
        approved_headcount=10,
        billing_type='billable',
        effective_from=datetime.date.today(),
        is_active=True,
    )
    defaults.update(kwargs)
    return SiteRoleRequirement.objects.create(
        site=site, job_role=job_role, department=department, **defaults
    )


# ─── Base fixture ─────────────────────────────────────────────────────────────

class SRRDeptBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='SRR Dept Org', code='srr-dept-org')
        cls.org2 = Organization.objects.create(name='SRR Other Org', code='srr-dept-org2')

        cls.n_company = _node(cls.org, 'srr-dept-org', 'company', None, 0, 'srr-dept-org')
        cls.n_client = _node(cls.org, 'srrd-cli', 'client', cls.n_company, 1, 'srr-dept-org/srrd-cli')
        cls.n_site = _node(cls.org, 'srrd-site', 'site', cls.n_client, 2, 'srr-dept-org/srrd-cli/srrd-site')
        cls.n_client2 = _node(cls.org, 'srrd-cli2', 'client', cls.n_company, 1, 'srr-dept-org/srrd-cli2')
        cls.n_site2 = _node(cls.org, 'srrd-site2', 'site', cls.n_client2, 2, 'srr-dept-org/srrd-cli2/srrd-site2')

        cls.client = Client.objects.create(
            org=cls.org, name='SRRD Client', code='srrd-cli', scope_node=cls.n_client,
        )
        cls.client2 = Client.objects.create(
            org=cls.org, name='SRRD Client2', code='srrd-cli2', scope_node=cls.n_client2,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client, name='SRRD Site', code='srrd-site',
            scope_node=cls.n_site,
        )
        cls.site2 = SiteProfile.objects.create(
            org=cls.org, client=cls.client2, name='SRRD Site2', code='srrd-site2',
            scope_node=cls.n_site2,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='srrd-guard', skill_category='unskilled',
        )
        cls.job_role2 = JobRole.objects.create(
            org=cls.org, name='Housekeeping', code='srrd-hk', skill_category='unskilled',
        )

        # Departments at different scopes
        cls.dept_org = Department.objects.create(
            org=cls.org, name='HR', code='srrd-hr', is_active=True,
        )
        cls.dept_client = Department.objects.create(
            org=cls.org, client=cls.client, name='Ops', code='srrd-ops', is_active=True,
        )
        cls.dept_site = Department.objects.create(
            org=cls.org, client=cls.client, site=cls.site, name='Site Ops', code='srrd-site-ops', is_active=True,
        )
        cls.dept_other_site = Department.objects.create(
            org=cls.org, client=cls.client2, site=cls.site2, name='Other Ops', code='srrd-other-ops', is_active=True,
        )
        cls.dept_other_client = Department.objects.create(
            org=cls.org, client=cls.client2, name='Client2 Dept', code='srrd-cli2-dept', is_active=True,
        )

        # Admin user with all capabilities
        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)
        cls.admin = _user('srrd_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.superuser = _user('srrd_super', is_superuser=True)

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def _srr_create_url(self):
        return '/api/sites/role-requirements/'

    def _srr_list_url(self):
        return '/api/sites/role-requirements/'

    def _srr_detail_url(self, pk):
        return f'/api/sites/role-requirements/{pk}/'


# ─── D01–D07: Write validation ────────────────────────────────────────────────

class TestSRRDepartmentWrite(SRRDeptBase):

    def test_d01_srr_no_department_accepted(self):
        """SRR without department is created successfully (nullable)."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 5,
            'billing_type': 'billable',
            'effective_from': '2026-01-01',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['department'])

    def test_d02_srr_org_level_department_accepted(self):
        """SRR with org-level department (no client/site) is accepted."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role2.pk,
            'approved_headcount': 3,
            'billing_type': 'billable',
            'effective_from': '2026-01-01',
            'department': self.dept_org.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['department'], self.dept_org.pk)
        self.assertEqual(resp.data['department_name'], 'HR')
        self.assertEqual(resp.data['department_code'], 'srrd-hr')

    def test_d03_srr_client_level_department_same_client_accepted(self):
        """SRR with department scoped to same client is accepted."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 4,
            'billing_type': 'billable',
            'effective_from': '2026-02-01',
            'department': self.dept_client.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['department'], self.dept_client.pk)

    def test_d04_srr_site_level_department_same_site_accepted(self):
        """SRR with department scoped to same site is accepted."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 6,
            'billing_type': 'billable',
            'effective_from': '2026-03-01',
            'department': self.dept_site.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['department'], self.dept_site.pk)

    def test_d05_srr_department_wrong_org_rejected(self):
        """Department from another org is rejected."""
        org2_dept = Department.objects.create(
            org=self.org2, name='Org2 Dept', code='org2-dept', is_active=True,
        )
        resp = self._api(self.superuser).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 2,
            'billing_type': 'billable',
            'effective_from': '2026-01-01',
            'department': org2_dept.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', resp.data)

    def test_d06_srr_department_different_site_rejected(self):
        """Department scoped to a different site is rejected."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 2,
            'billing_type': 'billable',
            'effective_from': '2026-04-01',
            'department': self.dept_other_site.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', resp.data)

    def test_d07_srr_department_different_client_rejected(self):
        """Department scoped to a different client is rejected."""
        resp = self._api(self.admin).post(self._srr_create_url(), {
            'site': self.site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 2,
            'billing_type': 'billable',
            'effective_from': '2026-05-01',
            'department': self.dept_other_client.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', resp.data)


# ─── D08–D10: Read serializer display fields ──────────────────────────────────

class TestSRRDepartmentRead(SRRDeptBase):

    def test_d08_read_exposes_department_fields(self):
        """department, department_name, department_code appear in GET response."""
        srr = _srr(self.site, self.job_role, department=self.dept_client)
        resp = self._api(self.admin).get(self._srr_detail_url(srr.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['department'], self.dept_client.pk)
        self.assertEqual(resp.data['department_name'], self.dept_client.name)
        self.assertEqual(resp.data['department_code'], self.dept_client.code)

    def test_d09_read_exposes_site_and_role_names(self):
        """site_name, job_role_name, job_role_code appear in GET response."""
        srr = _srr(self.site, self.job_role)
        resp = self._api(self.admin).get(self._srr_detail_url(srr.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['site_name'], self.site.name)
        self.assertEqual(resp.data['job_role_name'], self.job_role.name)
        self.assertEqual(resp.data['job_role_code'], self.job_role.code)

    def test_d10_read_exposes_wage_and_location_names(self):
        """wage_category_name, wage_category_code, location_area_name in response."""
        wage_cat = WageCategory.objects.create(
            name='Unskilled D10', code='unsk-d10',
        )
        srr = _srr(self.site, self.job_role, wage_category=wage_cat)
        resp = self._api(self.admin).get(self._srr_detail_url(srr.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['wage_category_name'], 'Unskilled D10')
        self.assertEqual(resp.data['wage_category_code'], 'unsk-d10')
        # location_area_name is None (site has no location_area in this fixture)
        self.assertIsNone(resp.data['location_area_name'])

    def test_d08_read_null_department_returns_null_fields(self):
        """department=None → department_name and department_code are null."""
        srr = _srr(self.site, self.job_role)
        resp = self._api(self.admin).get(self._srr_detail_url(srr.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data['department'])
        self.assertIsNone(resp.data['department_name'])
        self.assertIsNone(resp.data['department_code'])


# ─── D11–D13: Filtering ───────────────────────────────────────────────────────

class TestSRRDepartmentFilter(SRRDeptBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.srr_with_dept = _srr(cls.site, cls.job_role, department=cls.dept_client)
        cls.srr_no_dept = _srr(cls.site, cls.job_role2)

    def test_d11_filter_by_department_returns_matching(self):
        """?department=<id> returns only SRRs with that department."""
        resp = self._api(self.admin).get(
            self._srr_list_url() + f'?department={self.dept_client.pk}',
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.srr_with_dept.pk, ids)
        self.assertNotIn(self.srr_no_dept.pk, ids)

    def test_d12_filter_site_and_department_returns_intersection(self):
        """?site=X&department=Y returns only SRRs matching both."""
        resp = self._api(self.admin).get(
            self._srr_list_url() + f'?site={self.site.pk}&department={self.dept_client.pk}',
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.srr_with_dept.pk, ids)
        self.assertNotIn(self.srr_no_dept.pk, ids)

    def test_d13_filter_unassigned_department_returns_empty(self):
        """?department=<dept with no SRRs> returns empty list."""
        resp = self._api(self.admin).get(
            self._srr_list_url() + f'?department={self.dept_org.pk}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 0)


# ─── D14–D15: PATCH updates ───────────────────────────────────────────────────

class TestSRRDepartmentPatch(SRRDeptBase):

    def test_d14_patch_clears_department(self):
        """PATCH department=null clears the department."""
        srr = _srr(self.site, self.job_role, department=self.dept_org)
        resp = self._api(self.admin).patch(
            self._srr_detail_url(srr.pk),
            {'department': None},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['department'])
        srr.refresh_from_db()
        self.assertIsNone(srr.department_id)

    def test_d15_patch_sets_department(self):
        """PATCH department=<id> assigns a department."""
        srr = _srr(self.site, self.job_role2)
        resp = self._api(self.admin).patch(
            self._srr_detail_url(srr.pk),
            {'department': self.dept_site.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['department'], self.dept_site.pk)
        srr.refresh_from_db()
        self.assertEqual(srr.department_id, self.dept_site.pk)

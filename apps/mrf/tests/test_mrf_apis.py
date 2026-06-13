"""
apps/mrf/tests/test_mrf_apis.py

Tests for authenticated MRF admin APIs:
  - ManpowerRequest CRUD
  - MRFLineItem CRUD
  - Capability gating (403 for wrong role)
  - Scope enforcement (403 for inaccessible site)
  - Validation (wage range, budget range, cross-site line items)
"""

from datetime import date, timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.budgets.models import BudgetPlan
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code='mrf-ta'):
    return Organization.objects.create(name=f'MRFOrg {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client MRF', code='client-mrf',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='client-mrf', name='client-mrf', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/client-mrf', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site MRF', code='site-mrf', city='Mumbai', state='MH', is_active=True,
    )
    return n_co, n_cl, client, site


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _user(username, org, role_obj, scope_node, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass123',
        is_superuser=is_superuser, is_staff=is_superuser,
    )
    u.org = org
    u.save()
    if role_obj and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _srr(site, job_role, **kwargs):
    defaults = {
        'approved_headcount': 10,
        'billing_rate': '500.00',
        'wage_min': '250.00',
        'wage_max': '350.00',
        'billing_type': 'billable',
        'effective_from': date.today(),
        'is_active': True,
    }
    defaults.update(kwargs)
    return SiteRoleRequirement.objects.create(site=site, job_role=job_role, **defaults)


def _mrf(org, site, requested_by, **kwargs):
    defaults = {
        'mrf_type': 'new_hiring',
        'status': 'draft',
        'billing_type': 'billable',
        'department': 'Security',
        'reason': 'Expansion',
    }
    defaults.update(kwargs)
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by, **defaults
    )


def _line_item(mrf, job_role, srr=None, headcount=2, **kwargs):
    defaults = {
        'headcount': headcount,
        'replacement_for_employee': '',
        'required_skills': [],
    }
    defaults.update(kwargs)
    return MRFLineItem.objects.create(
        mrf=mrf, job_role=job_role, site_role_requirement=srr, **defaults
    )


class MRFTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        # hr_admin: mrf.read, mrf.create, mrf.update, mrf.delete
        cls.r_hr_admin = _role(cls.org, 'hr_admin')
        # hr_executive: mrf.read, mrf.create  (no mrf.update, no mrf.delete)
        cls.r_hr_exec = _role(cls.org, 'hr_executive')
        # finance: mrf.read, mrf.approve, mrf.reject  (no mrf.create, no mrf.update)
        cls.r_finance = _role(cls.org, 'finance')
        # field_supervisor: no mrf.read at all
        cls.r_field_sup = _role(cls.org, 'field_supervisor')
        bootstrap_role_permissions(cls.r_hr_admin)
        bootstrap_role_permissions(cls.r_hr_exec)
        bootstrap_role_permissions(cls.r_finance)
        bootstrap_role_permissions(cls.r_field_sup)

        cls.hr_admin = _user('mrf_hr_admin', cls.org, cls.r_hr_admin, cls.n_co)
        cls.hr_exec = _user('mrf_hr_exec', cls.org, cls.r_hr_exec, cls.n_co)
        cls.finance_user = _user('mrf_finance', cls.org, cls.r_finance, cls.n_co)
        cls.no_mrf_user = _user('mrf_field_sup', cls.org, cls.r_field_sup, cls.n_co)
        cls.superuser = _user('mrf_super', cls.org, None, None, is_superuser=True)

        cls.dept_operations = Department.objects.create(
            org=cls.org, name='Operations', code='operations',
        )
        cls.dept_housekeeping = Department.objects.create(
            org=cls.org, client=cls.client_obj, site=cls.site,
            name='Housekeeping', code='housekeeping',
        )
        cls.hr_admin.department = cls.dept_operations
        cls.hr_admin.save(update_fields=['department'])

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Security Guard MRF', code='guard-mrf',
            skill_category='unskilled',
        )
        cls.srr = _srr(cls.site, cls.job_role)
        cls.mrf = _mrf(cls.org, cls.site, cls.hr_admin)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── ManpowerRequest Tests ────────────────────────────────────────────────────

class TestManpowerRequestAPI(MRFTestBase):

    def test_list_returns_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/mrf/requests/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_unauthenticated_401(self):
        resp = self.api.get('/api/mrf/requests/')
        self.assertEqual(resp.status_code, 401)

    def test_list_no_mrf_read_capability_403(self):
        # field_supervisor has no mrf.read
        self._login(self.no_mrf_user)
        resp = self.api.get('/api/mrf/requests/')
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/mrf/requests/{self.mrf.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], self.mrf.pk)

    def test_retrieve_includes_expected_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/mrf/requests/{self.mrf.pk}/')
        for field in [
            'id', 'org', 'site', 'requested_by', 'requested_by_type',
            'mrf_type', 'status',
            'requesting_department', 'requesting_department_name', 'requesting_department_code',
            'required_department', 'required_department_name', 'required_department_code',
            'department', 'billing_type',
            'required_by_date', 'reason', 'client_visible',
            'submitted_at', 'approved_at', 'rejected_at',
            'line_items', 'created_at', 'updated_at',
        ]:
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_retrieve_includes_line_items_array(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/mrf/requests/{self.mrf.pk}/')
        self.assertIn('line_items', resp.data)
        self.assertIsInstance(resp.data['line_items'], list)

    def test_create_mrf_201(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'requested_by_type': 'internal',
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'department': 'Operations',
            'reason': 'New project',
            'status': 'draft',
            'client_visible': False,
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['mrf_type'], 'new_hiring')
        self.assertEqual(resp.data['status'], 'draft')
        self.assertIn('requested_by', resp.data)

    def test_create_mrf_with_structured_departments_201(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'requested_by_type': 'internal',
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'requesting_department': self.dept_operations.pk,
            'required_department': self.dept_housekeeping.pk,
            'department': 'Legacy display text',
            'reason': 'Housekeeping manpower request',
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['requesting_department'], self.dept_operations.pk)
        self.assertEqual(resp.data['requesting_department_name'], 'Operations')
        self.assertEqual(resp.data['requesting_department_code'], 'operations')
        self.assertEqual(resp.data['required_department'], self.dept_housekeeping.pk)
        self.assertEqual(resp.data['required_department_name'], 'Housekeeping')
        self.assertEqual(resp.data['required_department_code'], 'housekeeping')

    def test_create_defaults_requesting_department_from_actor(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'required_department': self.dept_housekeeping.pk,
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['requesting_department'], self.dept_operations.pk)

    def test_create_defaults_requesting_department_from_actor_when_null(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'requesting_department': None,
            'required_department': self.dept_housekeeping.pk,
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['requesting_department'], self.dept_operations.pk)

    def test_create_rejects_department_from_other_org(self):
        other_org = _org(code='mrf-dept-other')
        other_dept = Department.objects.create(
            org=other_org, name='Other Operations', code='other-ops',
        )
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'requesting_department': other_dept.pk,
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('requesting_department', resp.data)

    def test_create_rejects_required_department_from_other_site(self):
        other_client = Client.objects.create(
            org=self.org, name='Other Client', code='other-client-mrf',
            scope_node=self.n_co, is_active=True,
        )
        other_site = SiteProfile.objects.create(
            org=self.org, client=other_client, scope_node=self.n_cl,
            name='Other Site', code='other-site-mrf', city='Pune', state='MH', is_active=True,
        )
        other_dept = Department.objects.create(
            org=self.org, client=other_client, site=other_site,
            name='Other Housekeeping', code='other-housekeeping',
        )
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'required_department': other_dept.pk,
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('required_department', resp.data)

    def test_create_injects_org_from_site(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'replacement',
            'billing_type': 'billable',
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org.pk)

    def test_create_injects_requested_by_from_actor(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['requested_by'], self.hr_admin.pk)

    def test_create_blocked_without_mrf_create_403(self):
        # finance has mrf.read but not mrf.create
        self._login(self.finance_user)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_validates_required_by_date_past_400(self):
        self._login(self.hr_admin)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'status': 'draft',
            'required_by_date': yesterday,
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_blocks_approved_status_400(self):
        self._login(self.hr_admin)
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'status': 'approved',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_patch_mrf_200(self):
        self._login(self.hr_admin)
        mrf = _mrf(self.org, self.site, self.hr_admin)
        resp = self.api.patch(
            f'/api/mrf/requests/{mrf.pk}/',
            {'department': 'Updated Dept', 'status': 'submitted'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['department'], 'Updated Dept')
        self.assertEqual(resp.data['status'], 'submitted')

    def test_patch_blocked_without_mrf_update_403(self):
        # hr_executive has mrf.create but not mrf.update
        self._login(self.hr_exec)
        resp = self.api.patch(
            f'/api/mrf/requests/{self.mrf.pk}/',
            {'department': 'Should fail'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_mrf_204(self):
        self._login(self.hr_admin)
        mrf = _mrf(self.org, self.site, self.hr_admin)
        resp = self.api.delete(f'/api/mrf/requests/{mrf.pk}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ManpowerRequest.objects.filter(pk=mrf.pk).exists())

    def test_delete_blocked_without_mrf_delete_403(self):
        # hr_executive has no mrf.delete
        self._login(self.hr_exec)
        resp = self.api.delete(f'/api/mrf/requests/{self.mrf.pk}/')
        self.assertEqual(resp.status_code, 403)

    def test_filter_by_status(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/mrf/requests/', {'status': 'draft'})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['status'], 'draft')

    def test_filter_by_site(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/mrf/requests/', {'site': self.site.pk})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['site'], self.site.pk)

    def test_create_inaccessible_site_403(self):
        # Build a second org + site outside hr_admin's scope
        other_org = _org(code='mrf-other')
        other_n_co, _, other_client, other_site = _scope_tree(other_org)
        self._login(self.hr_admin)
        payload = {
            'site': other_site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': 'billable',
            'status': 'draft',
        }
        resp = self.api.post('/api/mrf/requests/', payload, format='json')
        self.assertEqual(resp.status_code, 403)


# ─── MRFLineItem Tests ────────────────────────────────────────────────────────

class TestMRFLineItemAPI(MRFTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.line_item = _line_item(cls.mrf, cls.job_role, cls.srr)

    def test_list_line_items_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/mrf/line-items/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_unauthenticated_401(self):
        resp = self.api.get('/api/mrf/line-items/')
        self.assertEqual(resp.status_code, 401)

    def test_list_no_mrf_read_capability_403(self):
        self._login(self.no_mrf_user)
        resp = self.api.get('/api/mrf/line-items/')
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/mrf/line-items/{self.line_item.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], self.line_item.pk)

    def test_retrieve_includes_expected_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/mrf/line-items/{self.line_item.pk}/')
        for field in [
            'id', 'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
        ]:
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_create_line_item_201(self):
        self._login(self.hr_admin)
        jr2 = JobRole.objects.create(
            org=self.org, name='Packer MRF', code='packer-mrf', skill_category='unskilled',
        )
        payload = {
            'mrf': self.mrf.pk,
            'job_role': jr2.pk,
            'headcount': 3,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['headcount'], 3)
        self.assertEqual(resp.data['job_role'], jr2.pk)

    def test_create_snapshots_wage_min_from_srr(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(
            org=self.org, name='Cleaner MRF', code='cleaner-mrf', skill_category='unskilled',
        )
        srr = _srr(self.site, jr, wage_min='300.00')
        payload = {
            'mrf': self.mrf.pk,
            'job_role': jr.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(str(resp.data['min_wage_snapshot']), '300.00')

    def test_create_blocked_without_mrf_update_403(self):
        # finance has mrf.read but not mrf.update
        self._login(self.finance_user)
        payload = {
            'mrf': self.mrf.pk,
            'job_role': self.job_role.pk,
            'headcount': 1,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_cross_site_srr_mismatch_400(self):
        # Create a site_role_requirement for a different site
        other_org = _org(code='mrf-x-site')
        _, _, other_client, other_site = _scope_tree(other_org)
        other_jr = JobRole.objects.create(
            org=other_org, name='Guard X', code='guard-x', skill_category='unskilled',
        )
        other_srr = _srr(other_site, other_jr)

        self._login(self.hr_admin)
        payload = {
            'mrf': self.mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': other_srr.pk,
            'headcount': 2,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_headcount_zero_blocked_400(self):
        self._login(self.hr_admin)
        payload = {
            'mrf': self.mrf.pk,
            'job_role': self.job_role.pk,
            'headcount': 0,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_wage_min_greater_than_max_400(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(
            org=self.org, name='Electrician MRF', code='elec-mrf', skill_category='skilled',
        )
        payload = {
            'mrf': self.mrf.pk,
            'job_role': jr.pk,
            'headcount': 1,
            'wage_min_requested': '500.00',
            'wage_max_requested': '200.00',
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_budget_min_greater_than_max_400(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(
            org=self.org, name='Plumber MRF', code='plumb-mrf', skill_category='skilled',
        )
        payload = {
            'mrf': self.mrf.pk,
            'job_role': jr.pk,
            'headcount': 1,
            'budget_min': '10000.00',
            'budget_max': '5000.00',
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_patch_line_item_200(self):
        self._login(self.hr_admin)
        resp = self.api.patch(
            f'/api/mrf/line-items/{self.line_item.pk}/',
            {'headcount': 5},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['headcount'], 5)

    def test_delete_line_item_204(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(
            org=self.org, name='Temp Role MRF', code='temp-mrf', skill_category='unskilled',
        )
        li = _line_item(self.mrf, jr)
        resp = self.api.delete(f'/api/mrf/line-items/{li.pk}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MRFLineItem.objects.filter(pk=li.pk).exists())

    def test_filter_by_mrf(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/mrf/line-items/', {'mrf': self.mrf.pk})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['mrf'], self.mrf.pk)

    # ── MRF scope guard tests ─────────────────────────────────────────────────

    def test_create_line_item_under_inaccessible_mrf_403(self):
        # Build an MRF for a site outside hr_admin's scope; hr_admin has mrf.update
        # but should be denied because the parent MRF's site is out of scope.
        other_org = _org(code='mrf-li-guard-a')
        _, _, _, other_site = _scope_tree(other_org)
        other_jr = JobRole.objects.create(
            org=other_org, name='Guard LI A', code='guard-li-a', skill_category='unskilled',
        )
        # Use superuser to create the out-of-scope MRF
        other_mrf = _mrf(other_org, other_site, self.superuser)

        self._login(self.hr_admin)
        payload = {
            'mrf': other_mrf.pk,
            'job_role': other_jr.pk,
            'headcount': 1,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_patch_line_item_to_inaccessible_mrf_403(self):
        # Create a line item under the accessible MRF, then PATCH mrf to an out-of-scope one.
        other_org = _org(code='mrf-li-guard-b')
        _, _, _, other_site = _scope_tree(other_org)
        other_mrf = _mrf(other_org, other_site, self.superuser)

        jr = JobRole.objects.create(
            org=self.org, name='Guard LI B', code='guard-li-b', skill_category='unskilled',
        )
        li = _line_item(self.mrf, jr)

        self._login(self.hr_admin)
        resp = self.api.patch(
            f'/api/mrf/line-items/{li.pk}/',
            {'mrf': other_mrf.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_create_line_item_under_any_mrf(self):
        # Superuser bypasses scope guard and can create under any MRF.
        other_org = _org(code='mrf-li-guard-c')
        _, _, _, other_site = _scope_tree(other_org)
        other_jr = JobRole.objects.create(
            org=other_org, name='Guard LI C', code='guard-li-c', skill_category='unskilled',
        )
        other_mrf = _mrf(other_org, other_site, self.superuser)

        self._login(self.superuser)
        payload = {
            'mrf': other_mrf.pk,
            'job_role': other_jr.pk,
            'headcount': 2,
        }
        resp = self.api.post('/api/mrf/line-items/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── Budget-C: MRF budget link ────────────────────────────────────────────────

def _budget_plan(org, nature='billable', client=None, site=None, department=None,
                 code='mrf-bgt', status='active', is_active=True, budget_type='manpower'):
    return BudgetPlan.objects.create(
        org=org, name=f'Budget {code}', code=code,
        budget_nature=nature, budget_type=budget_type,
        client=client, site=site, department=department,
        period_start=date(2025, 4, 1),
        amount='1000000.00', currency='INR',
        status=status, is_active=is_active,
    )


class TestMRFBudgetLink(MRFTestBase):
    """Phase Budget-C: budget_plan FK on ManpowerRequest."""

    MRF_URL = '/api/mrf/requests/'

    def _mrf_payload(self, budget_pk=None, billing_type='billable'):
        payload = {
            'site': self.site.pk,
            'mrf_type': 'new_hiring',
            'billing_type': billing_type,
            'status': 'draft',
        }
        if budget_pk is not None:
            payload['budget_plan'] = budget_pk
        return payload

    def test_billable_mrf_with_matching_client_budget_succeeds(self):
        """Billable MRF with client-level budget matching the MRF site's client."""
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-cli-ok')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], budget.pk)

    def test_billable_mrf_with_matching_site_budget_succeeds(self):
        """Billable MRF with site-level budget matching the MRF site."""
        budget = _budget_plan(self.org, client=self.client_obj, site=self.site, code='mrf-site-ok')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], budget.pk)

    def test_billable_mrf_with_wrong_site_budget_rejected(self):
        """Billable MRF with budget scoped to a different site → 400."""
        other_org = _org('mrf-bgt-xsite')
        _, _, _, other_site = _scope_tree(other_org)
        budget = _budget_plan(self.org, site=other_site, code='mrf-xsite-01')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_billable_mrf_with_wrong_client_budget_rejected(self):
        """Billable MRF with budget scoped to a different client → 400."""
        other_org = _org('mrf-bgt-xcli')
        _, _, other_client, _ = _scope_tree(other_org)
        budget = _budget_plan(self.org, client=other_client, code='mrf-xcli-01')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_non_billable_mrf_with_department_budget_succeeds(self):
        """Non-billable MRF with budget matching the required_department succeeds."""
        budget = _budget_plan(
            self.org, nature='non_billable',
            department=self.dept_housekeeping, code='mrf-nb-dept-ok',
            budget_type='hiring',
        )
        self._login(self.hr_admin)
        payload = self._mrf_payload(budget.pk, billing_type='non_billable')
        payload['required_department'] = self.dept_housekeeping.pk
        resp = self.api.post(self.MRF_URL, payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], budget.pk)

    def test_non_billable_mrf_with_non_hiring_department_budget_rejected(self):
        """Non-billable MRF requires an internal hiring budget, not a general department budget."""
        budget = _budget_plan(
            self.org, nature='non_billable',
            department=self.dept_housekeeping, code='mrf-nb-dept-general',
            budget_type='general',
        )
        self._login(self.hr_admin)
        payload = self._mrf_payload(budget.pk, billing_type='non_billable')
        payload['required_department'] = self.dept_housekeeping.pk
        resp = self.api.post(self.MRF_URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('hiring budget', str(resp.data).lower())

    def test_billing_type_nature_mismatch_rejected(self):
        """Billable MRF with non-billable budget → 400."""
        budget = _budget_plan(self.org, nature='non_billable',
                              department=self.dept_operations, code='mrf-mismatch-01')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk, billing_type='billable'), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_inactive_budget_rejected(self):
        """Inactive budget rejected on MRF create."""
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-inactive-01', is_active=False)
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_cross_org_budget_rejected(self):
        """Budget from different org rejected."""
        other_org = _org('mrf-bgt-xorg')
        budget = _budget_plan(other_org, code='mrf-xorg-01')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_no_budget_plan_allowed(self):
        """Omitting budget_plan is valid (not yet mandatory)."""
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data.get('budget_plan'))

    def test_response_includes_budget_display_fields(self):
        """MRF read response includes all budget_plan_* display fields."""
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-disp-01')
        self._login(self.hr_admin)
        resp = self.api.post(self.MRF_URL, self._mrf_payload(budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        for field in ['budget_plan', 'budget_plan_name', 'budget_plan_code',
                      'budget_plan_amount', 'budget_plan_currency',
                      'budget_plan_status', 'budget_plan_nature']:
            self.assertIn(field, resp.data, f'Missing field: {field}')
        self.assertEqual(resp.data['budget_plan_name'], budget.name)
        self.assertEqual(resp.data['budget_plan_nature'], 'billable')

    def test_patch_site_change_invalidates_existing_budget(self):
        """Changing MRF site to one where the existing budget doesn't match → 400."""
        # Build a second site in the same org with a different client
        n_co2 = ScopeNode.objects.create(
            org=self.org, code='mrf-xpatch-co', name='mrf-xpatch-co',
            node_type='company', parent=None, depth=0, path='mrf-xpatch-co', is_active=True,
        )
        other_client = Client.objects.create(
            org=self.org, name='Other Client Patch', code='mrf-xpatch-cli',
            scope_node=n_co2, is_active=True,
        )
        other_site = SiteProfile.objects.create(
            org=self.org, client=other_client, scope_node=n_co2,
            name='Other Site Patch', code='mrf-xpatch-site', city='Pune', state='MH', is_active=True,
        )
        # Budget scoped to original site's client
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-xpatch-01')
        mrf = _mrf(self.org, self.site, self.hr_admin, status='draft', billing_type='billable')
        mrf.budget_plan = budget
        mrf.save(update_fields=['budget_plan'])

        self._login(self.hr_admin)
        # PATCH site to other_site — budget no longer matches other_client
        resp = self.api.patch(
            f'{self.MRF_URL}{mrf.pk}/',
            {'site': other_site.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_patch_billing_type_change_invalidates_existing_budget(self):
        """Changing billing_type from billable to non_billable without updating budget → 400."""
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-btchange-01')
        mrf = _mrf(self.org, self.site, self.hr_admin, status='draft', billing_type='billable')
        mrf.budget_plan = budget
        mrf.save(update_fields=['budget_plan'])

        self._login(self.hr_admin)
        resp = self.api.patch(
            f'{self.MRF_URL}{mrf.pk}/',
            {'billing_type': 'non_billable'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_patch_explicit_null_clears_mrf_budget(self):
        """Explicit budget_plan=null on PATCH clears the FK and returns 200."""
        budget = _budget_plan(self.org, client=self.client_obj, code='mrf-clear-01')
        mrf = _mrf(self.org, self.site, self.hr_admin, status='draft', billing_type='billable')
        mrf.budget_plan = budget
        mrf.save(update_fields=['budget_plan'])

        self._login(self.hr_admin)
        resp = self.api.patch(
            f'{self.MRF_URL}{mrf.pk}/',
            {'budget_plan': None},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        mrf.refresh_from_db()
        self.assertIsNone(mrf.budget_plan_id)


# ─── Budget-C: MRF line item budget link ─────────────────────────────────────

class TestMRFLineItemBudgetLink(MRFTestBase):
    """Phase Budget-C: budget_plan FK on MRFLineItem with auto-default from MRF."""

    LI_URL = '/api/mrf/line-items/'
    MRF_URL = '/api/mrf/requests/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.budget_client = _budget_plan(
            cls.org, nature='billable', client=cls.client_obj, code='li-bgt-cli',
        )
        cls.mrf_with_budget = _mrf(
            cls.org, cls.site, cls.hr_admin, mrf_type='new_hiring',
            status='draft', billing_type='billable',
        )
        cls.mrf_with_budget.budget_plan = cls.budget_client
        cls.mrf_with_budget.save(update_fields=['budget_plan'])

        cls.mrf_no_budget = _mrf(
            cls.org, cls.site, cls.hr_admin, mrf_type='new_hiring',
            status='draft', billing_type='billable',
        )

    def _li_payload(self, mrf_pk, budget_pk=None):
        payload = {
            'mrf': mrf_pk,
            'job_role': self.job_role.pk,
            'headcount': 1,
        }
        if budget_pk is not None:
            payload['budget_plan'] = budget_pk
        return payload

    def test_line_item_auto_inherits_mrf_budget(self):
        """Line item without budget_plan auto-defaults to MRF's budget_plan."""
        self._login(self.hr_admin)
        resp = self.api.post(self.LI_URL, self._li_payload(self.mrf_with_budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], self.budget_client.pk)

    def test_line_item_no_budget_when_mrf_has_none(self):
        """Line item without budget_plan stays null when MRF has no budget."""
        self._login(self.hr_admin)
        resp = self.api.post(self.LI_URL, self._li_payload(self.mrf_no_budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data.get('budget_plan'))

    def test_line_item_can_override_with_valid_budget(self):
        """Line item can explicitly set a different valid budget."""
        budget2 = _budget_plan(self.org, client=self.client_obj, code='li-override-01')
        self._login(self.hr_admin)
        resp = self.api.post(
            self.LI_URL, self._li_payload(self.mrf_with_budget.pk, budget2.pk), format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_plan'], budget2.pk)

    def test_line_item_wrong_nature_rejected(self):
        """Non-billable budget on billable MRF line item → 400."""
        nb_budget = _budget_plan(
            self.org, nature='non_billable',
            department=self.dept_operations, code='li-nb-01',
        )
        self._login(self.hr_admin)
        resp = self.api.post(
            self.LI_URL, self._li_payload(self.mrf_with_budget.pk, nb_budget.pk), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_line_item_wrong_site_budget_rejected(self):
        """Budget scoped to a different site rejected on line item."""
        other_org = _org('li-bgt-xsite')
        _, _, _, other_site = _scope_tree(other_org)
        bad_budget = _budget_plan(self.org, site=other_site, code='li-xsite-01')
        self._login(self.hr_admin)
        resp = self.api.post(
            self.LI_URL, self._li_payload(self.mrf_with_budget.pk, bad_budget.pk), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_line_item_inactive_budget_rejected(self):
        """Inactive budget rejected on line item create."""
        inactive = _budget_plan(
            self.org, client=self.client_obj, code='li-inactive-01', is_active=False,
        )
        self._login(self.hr_admin)
        resp = self.api.post(
            self.LI_URL, self._li_payload(self.mrf_with_budget.pk, inactive.pk), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_line_item_cross_org_budget_rejected(self):
        """Budget from different org rejected on line item create."""
        other_org = _org('li-bgt-xorg')
        cross_budget = _budget_plan(other_org, code='li-xorg-01')
        self._login(self.hr_admin)
        resp = self.api.post(
            self.LI_URL, self._li_payload(self.mrf_with_budget.pk, cross_budget.pk), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_line_item_response_includes_budget_display_fields(self):
        """Line item read response includes all budget_plan_* display fields."""
        self._login(self.hr_admin)
        resp = self.api.post(self.LI_URL, self._li_payload(self.mrf_with_budget.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        for field in ['budget_plan', 'budget_plan_name', 'budget_plan_code',
                      'budget_plan_amount', 'budget_plan_currency',
                      'budget_plan_status', 'budget_plan_nature']:
            self.assertIn(field, resp.data, f'Missing field: {field}')
        self.assertEqual(resp.data['budget_plan_name'], self.budget_client.name)
        self.assertEqual(resp.data['budget_plan_nature'], 'billable')

    def test_patch_mrf_change_invalidates_existing_line_item_budget(self):
        """Moving a line item to an MRF on a different client invalidates the existing budget."""
        # Create a line item under mrf_with_budget (site=self.site, client=self.client_obj)
        li = _line_item(self.mrf_with_budget, self.job_role)
        li.budget_plan = self.budget_client
        li.save(update_fields=['budget_plan'])

        # Create another MRF for a different client's site
        n_other = ScopeNode.objects.create(
            org=self.org, code='li-xmrf-co', name='li-xmrf-co',
            node_type='company', parent=None, depth=0, path='li-xmrf-co', is_active=True,
        )
        other_client = Client.objects.create(
            org=self.org, name='LI XMrf Client', code='li-xmrf-cli',
            scope_node=n_other, is_active=True,
        )
        other_site = SiteProfile.objects.create(
            org=self.org, client=other_client, scope_node=n_other,
            name='LI XMrf Site', code='li-xmrf-site', city='Delhi', state='DL', is_active=True,
        )
        other_mrf = _mrf(self.org, other_site, self.hr_admin, billing_type='billable')

        self._login(self.hr_admin)
        resp = self.api.patch(
            f'{self.LI_URL}{li.pk}/',
            {'mrf': other_mrf.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('budget_plan', str(resp.data))

    def test_patch_line_item_explicit_null_clears_budget(self):
        """Explicit budget_plan=null on line item PATCH clears the FK and returns 200."""
        li = _line_item(self.mrf_with_budget, self.job_role)
        li.budget_plan = self.budget_client
        li.save(update_fields=['budget_plan'])

        self._login(self.hr_admin)
        resp = self.api.patch(
            f'{self.LI_URL}{li.pk}/',
            {'budget_plan': None},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        li.refresh_from_db()
        self.assertIsNone(li.budget_plan_id)

"""
apps/mrf/tests/test_mrf_srr_department_a.py

Phase MRF-SRR-Department-A: MRF line item + readiness integration.

Scenarios (M01–M18):
  M01  Line item rejects SRR from wrong department (both dept set)
  M02  Line item accepts SRR when MRF has no required_department
  M03  Line item accepts SRR when SRR has no department
  M04  Line item accepts SRR when departments match
  M05  Line item rejects SRR with mismatched job_role
  M06  Line item accepts SRR when job_roles match
  M07  Line item rejects SRR with mismatched wage_category (when SRR has one)
  M08  Line item accepts when SRR has no wage_category (no forced match)
  M09  Readiness error when SRR dept ≠ MRF required_department
  M10  Readiness ok when SRR dept matches MRF required_department
  M11  Readiness ok when SRR has no dept (dept check skipped)
  M12  Readiness ok when MRF has no required_department (check skipped)
  M13  Non-billable MRF with no SRR is still readiness-ok (no SRR required)
  M14  Read serializer exposes site_role_requirement_label
  M15  Read serializer exposes srr_department_name
  M16  Read serializer exposes srr_approved_headcount and srr_remaining_headcount
  M17  Read serializer exposes srr_wage_min/max/billing_rate/shift_hours
  M18  srr_remaining_headcount accounts for other active MRFs
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode, Department
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.mrf.services import check_mrf_readiness
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


def _mrf(org, site, requested_by, billing_type='billable', required_department=None, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status=status,
        billing_type=billing_type,
        required_department=required_department,
    )


def _srr(site, job_role, department=None, wage_category=None, billing_rate=None,
         wage_min=None, wage_max=None, shift_hours=None, approved_headcount=10):
    return SiteRoleRequirement.objects.create(
        site=site, job_role=job_role, department=department,
        approved_headcount=approved_headcount,
        billing_type='billable',
        billing_rate=billing_rate,
        wage_min=wage_min, wage_max=wage_max,
        shift_hours=shift_hours,
        wage_category=wage_category,
        effective_from=datetime.date.today(),
        is_active=True,
    )


def _li(mrf, job_role, srr=None, headcount=1, wage_category=None):
    return MRFLineItem.objects.create(
        mrf=mrf, job_role=job_role,
        site_role_requirement=srr,
        headcount=headcount,
        wage_category=wage_category,
    )


# ─── Base fixture ─────────────────────────────────────────────────────────────

class MRFSRRDeptBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='MRF SRR Dept Org', code='mrfd-org')

        cls.n_company = _node(cls.org, 'mrfd-org', 'company', None, 0, 'mrfd-org')
        cls.n_client = _node(cls.org, 'mrfd-cli', 'client', cls.n_company, 1, 'mrfd-org/mrfd-cli')
        cls.n_site = _node(cls.org, 'mrfd-site', 'site', cls.n_client, 2, 'mrfd-org/mrfd-cli/mrfd-site')

        cls.client = Client.objects.create(
            org=cls.org, name='MRFD Client', code='mrfd-cli', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client, name='MRFD Site', code='mrfd-site',
            scope_node=cls.n_site,
        )

        cls.dept_a = Department.objects.create(
            org=cls.org, client=cls.client, name='Dept A', code='mrfd-dept-a', is_active=True,
        )
        cls.dept_b = Department.objects.create(
            org=cls.org, client=cls.client, name='Dept B', code='mrfd-dept-b', is_active=True,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='mrfd-guard', skill_category='unskilled',
        )
        cls.job_role2 = JobRole.objects.create(
            org=cls.org, name='Cook', code='mrfd-cook', skill_category='unskilled',
        )

        cls.wage_cat = WageCategory.objects.create(
            name='Unskilled', code='mrfd-unsk',
        )
        cls.wage_cat2 = WageCategory.objects.create(
            name='Skilled', code='mrfd-sk',
        )

        cls.role_hr = _role(cls.org, 'hr_admin')
        bootstrap_role_permissions(cls.role_hr)
        cls.hr_user = _user('mrfd_hr', org=cls.org)
        _assign(cls.hr_user, cls.role_hr, cls.n_company)

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def _li_url(self):
        return '/api/mrf/line-items/'

    def _li_detail_url(self, pk):
        return f'/api/mrf/line-items/{pk}/'


# ─── M01–M08: Line item write validation ──────────────────────────────────────

class TestMRFLineItemSRRValidation(MRFSRRDeptBase):

    def test_m01_line_item_rejects_srr_department_mismatch(self):
        """SRR has dept_a, MRF required_department=dept_b → 400."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_b)
        srr = _srr(self.site, self.job_role, department=self.dept_a)

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('site_role_requirement', resp.data)

    def test_m02_line_item_accepts_srr_when_mrf_has_no_required_dept(self):
        """SRR has dept, MRF has no required_department → accepted."""
        mrf = _mrf(self.org, self.site, self.hr_user)  # no required_department
        srr = _srr(self.site, self.job_role, department=self.dept_a)

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_m03_line_item_accepts_srr_when_srr_has_no_department(self):
        """SRR has no dept, MRF has required_department → accepted (no dept check)."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_a)
        srr = _srr(self.site, self.job_role)  # no department

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_m04_line_item_accepts_matching_department(self):
        """SRR dept matches MRF required_department → accepted."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_a)
        srr = _srr(self.site, self.job_role, department=self.dept_a)

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_m05_line_item_rejects_mismatched_job_role(self):
        """job_role on line item ≠ SRR job_role → 400."""
        mrf = _mrf(self.org, self.site, self.hr_user)
        srr = _srr(self.site, self.job_role)  # srr for job_role

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role2.pk,  # different job role
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('job_role', resp.data)

    def test_m06_line_item_accepts_matching_job_role(self):
        """job_role matches SRR.job_role → accepted."""
        mrf = _mrf(self.org, self.site, self.hr_user)
        srr = _srr(self.site, self.job_role2)

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role2.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_m07_line_item_rejects_mismatched_wage_category(self):
        """SRR has wage_cat, line item has wage_cat2 → 400."""
        mrf = _mrf(self.org, self.site, self.hr_user)
        srr = _srr(self.site, self.job_role, wage_category=self.wage_cat)

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
            'wage_category': self.wage_cat2.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('wage_category', resp.data)

    def test_m08_line_item_accepted_when_srr_has_no_wage_category(self):
        """SRR has no wage_category → no forced match, any wage_category accepted."""
        mrf = _mrf(self.org, self.site, self.hr_user)
        srr = _srr(self.site, self.job_role)  # no wage_category

        resp = self._api(self.hr_user).post(self._li_url(), {
            'mrf': mrf.pk,
            'job_role': self.job_role.pk,
            'site_role_requirement': srr.pk,
            'headcount': 1,
            'wage_category': self.wage_cat.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── M09–M13: Readiness department checks ─────────────────────────────────────

class TestMRFReadinessDepartment(MRFSRRDeptBase):

    def test_m09_readiness_error_when_srr_dept_mismatches_mrf_required_dept(self):
        """Readiness error when SRR.department ≠ MRF.required_department."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_b)
        srr = _srr(self.site, self.job_role, department=self.dept_a)
        _li(mrf, self.job_role, srr=srr, headcount=1)

        result = check_mrf_readiness(mrf)
        self.assertFalse(result['ok'])
        self.assertTrue(any('department' in e.lower() for e in result['errors']))

    def test_m10_readiness_ok_when_departments_match(self):
        """Readiness ok when SRR.department == MRF.required_department."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_a)
        srr = _srr(self.site, self.job_role, department=self.dept_a, approved_headcount=20)
        _li(mrf, self.job_role, srr=srr, headcount=2)

        result = check_mrf_readiness(mrf)
        self.assertTrue(result['ok'], result['errors'])

    def test_m11_readiness_ok_when_srr_has_no_department(self):
        """When SRR has no department, dept check is skipped → readiness ok."""
        mrf = _mrf(self.org, self.site, self.hr_user, required_department=self.dept_a)
        srr = _srr(self.site, self.job_role, approved_headcount=20)  # no department
        _li(mrf, self.job_role, srr=srr, headcount=1)

        result = check_mrf_readiness(mrf)
        self.assertTrue(result['ok'], result['errors'])

    def test_m12_readiness_ok_when_mrf_has_no_required_dept(self):
        """When MRF has no required_department, dept check is skipped."""
        mrf = _mrf(self.org, self.site, self.hr_user)  # no required_department
        srr = _srr(self.site, self.job_role, department=self.dept_a, approved_headcount=20)
        _li(mrf, self.job_role, srr=srr, headcount=2)

        result = check_mrf_readiness(mrf)
        self.assertTrue(result['ok'], result['errors'])

    def test_m13_non_billable_mrf_without_srr_is_readiness_ok(self):
        """Non-billable MRF does not require SRR — readiness passes with no SRR."""
        mrf = _mrf(self.org, self.site, self.hr_user, billing_type='non_billable')
        srr = _srr(self.site, self.job_role)
        _li(mrf, self.job_role, srr=None, headcount=3)  # deliberately no SRR

        result = check_mrf_readiness(mrf)
        self.assertTrue(result['ok'], result['errors'])


# ─── M14–M18: Read serializer SRR display fields ──────────────────────────────

class TestMRFLineItemReadSRRFields(MRFSRRDeptBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.srr_full = _srr(
            cls.site, cls.job_role,
            department=cls.dept_a,
            billing_rate=Decimal('5000.00'),
            wage_min=Decimal('10000.00'),
            wage_max=Decimal('15000.00'),
            shift_hours=Decimal('8.0'),
            approved_headcount=10,
        )
        cls.mrf = _mrf(cls.org, cls.site, cls.hr_user)
        cls.li = _li(cls.mrf, cls.job_role, srr=cls.srr_full, headcount=2)

    def _li_detail(self):
        return self._api(self.hr_user).get(self._li_detail_url(self.li.pk))

    def test_m14_srr_label_in_response(self):
        """site_role_requirement_label is non-null and contains job role name."""
        resp = self._li_detail()
        self.assertEqual(resp.status_code, 200)
        label = resp.data['site_role_requirement_label']
        self.assertIsNotNone(label)
        self.assertIn(self.job_role.name, label)

    def test_m15_srr_department_name_in_response(self):
        """srr_department_name reflects SRR.department.name."""
        resp = self._li_detail()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['srr_department_name'], self.dept_a.name)

    def test_m16_srr_headcount_fields(self):
        """srr_approved_headcount and srr_remaining_headcount are correct."""
        resp = self._li_detail()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['srr_approved_headcount'], 10)
        # remaining = approved(10) - already_allocated_excluding_this_mrf(0) = 10
        self.assertEqual(resp.data['srr_remaining_headcount'], 10)

    def test_m17_srr_commercial_fields(self):
        """srr_wage_min, srr_wage_max, srr_billing_rate, srr_shift_hours are correct."""
        resp = self._li_detail()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data['srr_wage_min']), Decimal('10000.00'))
        self.assertEqual(Decimal(resp.data['srr_wage_max']), Decimal('15000.00'))
        self.assertEqual(Decimal(resp.data['srr_billing_rate']), Decimal('5000.00'))
        self.assertEqual(Decimal(resp.data['srr_shift_hours']), Decimal('8.0'))

    def test_m18_srr_remaining_accounts_for_other_mrfs(self):
        """srr_remaining_headcount subtracts headcount from other active MRFs."""
        # Create a second active MRF that also uses the same SRR/job_role at same site
        other_mrf = _mrf(self.org, self.site, self.hr_user)
        _li(other_mrf, self.job_role, srr=self.srr_full, headcount=3)

        resp = self._api(self.hr_user).get(self._li_detail_url(self.li.pk))
        self.assertEqual(resp.status_code, 200)
        # remaining = approved(10) - other_mrf_headcount(3) = 7
        # (this mrf's 2 is excluded via exclude_mrf)
        self.assertEqual(resp.data['srr_remaining_headcount'], 7)


# ─── Serializer label: no SRR ─────────────────────────────────────────────────

class TestMRFLineItemNoSRR(MRFSRRDeptBase):

    def test_no_srr_fields_are_null(self):
        """When line item has no SRR, all srr_* fields are None/null."""
        mrf = _mrf(self.org, self.site, self.hr_user, billing_type='non_billable')
        li = _li(mrf, self.job_role, srr=None, headcount=1)

        resp = self._api(self.hr_user).get(self._li_detail_url(li.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data['site_role_requirement_label'])
        self.assertIsNone(resp.data['srr_department_name'])
        self.assertIsNone(resp.data['srr_approved_headcount'])
        self.assertIsNone(resp.data['srr_remaining_headcount'])
        self.assertIsNone(resp.data['srr_wage_min'])
        self.assertIsNone(resp.data['srr_billing_rate'])

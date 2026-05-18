"""
apps/onboarding/tests/test_srr_department_finalization.py

Phase MRF-SRR-Department-A: onboarding finalization maps proposed_department
to real Department on SiteRoleRequirement.

Scenarios:
  F01  Proposed SRR with proposed_department → real SRR.department is set
  F02  Proposed SRR without proposed_department → real SRR.department is None
  F03  Multiple proposed SRRs with different departments → each maps correctly
"""

import datetime

from django.test import TestCase

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.onboarding.models import (
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingRequest,
)
from apps.onboarding.services import finalize_client_onboarding_request
from apps.sites.models import Client, SiteRoleRequirement


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


# ─── Base fixture ─────────────────────────────────────────────────────────────

class OnboardingSRRDeptBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Fin SRR Dept Org', code='fin-srrd-org')

        cls.n_company = _node(cls.org, 'fin-srrd-org', 'company', None, 0, 'fin-srrd-org')

        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)
        cls.admin = _user('finsrrd_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='finsrrd-guard', skill_category='unskilled',
        )
        cls.job_role2 = JobRole.objects.create(
            org=cls.org, name='Cook', code='finsrrd-cook', skill_category='unskilled',
        )

    def _new_client_request(self):
        return ClientOnboardingRequest.objects.create(
            org=self.org,
            requested_by=self.admin,
            onboarding_type='new_client',
            status='approved',
            proposed_client_name='Fin SRR Client',
            proposed_client_code=f'fsr-cli-{self._testMethodName[:20]}',
            proposed_contact_name='Test',
            proposed_contact_email='test@fin.com',
            proposed_contact_phone='1234567890',
        )

    def _p_site(self, req, code='fps-1'):
        return ClientOnboardingProposedSite.objects.create(
            request=req, name=f'Site {code}', code=code,
            city='Mumbai', state='MH',
            contact_person='A', contact_phone='1234567890', contact_email='a@b.com',
            is_active=True,
        )

    def _p_dept(self, req, p_site, code='fpd-1', scope_level='site'):
        return ClientOnboardingProposedDepartment.objects.create(
            request=req, proposed_site=p_site, scope_level=scope_level,
            name=f'Dept {code}', code=code, is_active=True,
        )

    def _p_srr(self, req, p_site, job_role, p_dept=None):
        return ClientOnboardingProposedSiteRoleRequirement.objects.create(
            request=req,
            proposed_site=p_site,
            proposed_department=p_dept,
            job_role=job_role,
            approved_headcount=5,
            billing_type='billable',
            effective_from=datetime.date.today(),
            is_active=True,
        )


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestSRRDepartmentFinalization(OnboardingSRRDeptBase):

    def test_f01_proposed_department_maps_to_real_srr_department(self):
        """Finalization sets SRR.department from proposed_department via dept_map."""
        req = self._new_client_request()
        p_site = self._p_site(req, code='f01-site')
        p_dept = self._p_dept(req, p_site, code='f01-dept')
        self._p_srr(req, p_site, self.job_role, p_dept=p_dept)

        finalize_client_onboarding_request(req, actor=self.admin)

        real_srr = SiteRoleRequirement.objects.filter(job_role=self.job_role).latest('created_at')
        self.assertIsNotNone(real_srr.department_id, 'SRR.department should be set after finalization.')
        # Verify the department belongs to the same org
        self.assertEqual(real_srr.department.org_id, self.org.pk)
        # The real department name should match the proposed department name
        self.assertEqual(real_srr.department.name, p_dept.name)

    def test_f02_no_proposed_department_leaves_srr_department_null(self):
        """Finalization leaves SRR.department=None when proposed SRR has no department."""
        req = self._new_client_request()
        p_site = self._p_site(req, code='f02-site')
        self._p_srr(req, p_site, self.job_role, p_dept=None)

        finalize_client_onboarding_request(req, actor=self.admin)

        real_srr = SiteRoleRequirement.objects.filter(job_role=self.job_role).latest('created_at')
        self.assertIsNone(real_srr.department_id, 'SRR.department should be None when no proposed_department.')

    def test_f03_multiple_srrs_map_departments_correctly(self):
        """Multiple SRRs with different departments each map to their correct real department."""
        req = self._new_client_request()
        p_site = self._p_site(req, code='f03-site')
        p_dept_a = self._p_dept(req, p_site, code='f03-dept-a')
        p_dept_b = self._p_dept(req, p_site, code='f03-dept-b')

        self._p_srr(req, p_site, self.job_role, p_dept=p_dept_a)
        self._p_srr(req, p_site, self.job_role2, p_dept=p_dept_b)

        finalize_client_onboarding_request(req, actor=self.admin)

        srr_guard = SiteRoleRequirement.objects.filter(job_role=self.job_role).latest('created_at')
        srr_cook = SiteRoleRequirement.objects.filter(job_role=self.job_role2).latest('created_at')

        self.assertIsNotNone(srr_guard.department_id)
        self.assertIsNotNone(srr_cook.department_id)
        # They should be different departments
        self.assertNotEqual(srr_guard.department_id, srr_cook.department_id)
        # Names match proposed departments
        self.assertEqual(srr_guard.department.name, p_dept_a.name)
        self.assertEqual(srr_cook.department.name, p_dept_b.name)

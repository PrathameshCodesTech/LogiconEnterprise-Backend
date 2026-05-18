"""
apps/onboarding/tests/test_client_onboarding_readiness_a.py

Phase Client-Onboarding-Readiness-A — 10 tests:

  01. new_client with no proposed data cannot start workflow (400).
  02. Missing proposed sites returns 400 with readable error.
  03. expected_site_count=2 but only 1 active site returns 400.
  04. Sites/depts/SRRs present but no budget returns 400.
  05. Complete new_client setup starts workflow successfully (201).
  06. new_site_expansion missing client cannot start workflow (400).
  07. Complete new_site_expansion starts workflow successfully (201).
  08. Serializer returns readiness_ok=false and errors for incomplete request.
  09. Serializer returns readiness_ok=true for complete request.
  10. MRF workflow start regression — unaffected by readiness gate.
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.onboarding.models import (
    ClientOnboardingRequest,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedUser,
)
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
)


# ─── URLs ─────────────────────────────────────────────────────────────────────

def _start_url(ob_id):
    return f'/api/workflow/client-onboarding/{ob_id}/start/'


def _readiness_url(ob_id):
    return f'/api/onboarding/client-requests/{ob_id}/readiness/'


def _detail_url(ob_id):
    return f'/api/onboarding/client-requests/{ob_id}/'


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass',
                                 is_superuser=is_superuser, is_staff=is_superuser)
    u.org = org
    u.save()
    return u


def _role(org, code):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    return r


def _assign(user, role, node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=node)


def _wf_template(org, trigger_type, code):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code, trigger_type=trigger_type, version=1, is_active=True,
    )


def _wf_step(template, order, code):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        requires_comment_on_reject=False, requires_comment_on_request_changes=False,
    )


def _wf_mapping(org, template):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type=template.trigger_type, template=template,
        client=None, site=None, is_active=True,
    )


def _wf_sac(org, trigger_type, step_code, user):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type=trigger_type, step_code=step_code,
        assignment_mode='named_user', named_user=user, is_active=True,
    )


# ─── Proposed-record helpers ──────────────────────────────────────────────────

def _p_site(req, code, *, contact=False):
    return ClientOnboardingProposedSite.objects.create(
        request=req,
        name=f'Site {code}',
        code=code,
        contact_person='Alice' if contact else '',
        contact_phone='9999900001' if contact else '',
        contact_email='alice@site.local' if contact else '',
        is_active=True,
    )


def _p_dept(req, proposed_site=None, code='pd-001'):
    return ClientOnboardingProposedDepartment.objects.create(
        request=req,
        proposed_site=proposed_site,
        name=f'Dept {code}',
        code=code,
        scope_level='site' if proposed_site else 'client',
        is_active=True,
    )


def _p_srr(req, proposed_site, job_role, *, billing=True):
    return ClientOnboardingProposedSiteRoleRequirement.objects.create(
        request=req,
        proposed_site=proposed_site,
        job_role=job_role,
        approved_headcount=5,
        billing_type='billable',
        billing_rate='18000.00' if billing else None,
        wage_min='10000.00' if billing else None,
        wage_max='14000.00' if billing else None,
        is_active=True,
    )


def _p_user(req, access_role, *, email=None, is_primary=True):
    return ClientOnboardingProposedUser.objects.create(
        request=req,
        full_name='Test Contact',
        email=email or f'contact+{req.pk}@testclient.local',
        user_type='client',
        access_role=access_role,
        scope_level='client',
        is_primary_contact=is_primary,
        is_active=True,
    )


def _p_budget(req, code='pb-001'):
    return ClientOnboardingProposedBudget.objects.create(
        request=req,
        name=f'Budget {code}',
        code=code,
        budget_nature='billable',
        budget_type='onboarding',
        scope_level='client',
        amount='500000.00',
        currency='INR',
        period_start=datetime.date.today(),
        is_active=True,
    )


def _new_client_request(org, user, *, expected_site_count=None):
    return ClientOnboardingRequest.objects.create(
        org=org,
        requested_by=user,
        onboarding_type='new_client',
        proposed_client_name='Readiness Test Client',
        proposed_client_code='rtc-001',
        expected_site_count=expected_site_count,
        status='draft',
    )


def _expansion_request(org, client, user):
    return ClientOnboardingRequest.objects.create(
        org=org,
        client=client,
        requested_by=user,
        onboarding_type='new_site_expansion',
        status='draft',
    )


def _add_full_proposed_setup(req, job_role):
    """Add one complete set of proposed items (site with contact, dept, SRR, budget)."""
    site = _p_site(req, 'rps-001', contact=True)
    _p_dept(req, proposed_site=site, code='rpd-001')
    _p_srr(req, site, job_role, billing=True)
    _p_budget(req, code='rpb-001')
    return site


# ─── Base ─────────────────────────────────────────────────────────────────────

class OBReadinessBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='OR Org', code='or-rdns')

        cls.n_co = _node(cls.org, 'or-rdns', 'company', None, 0, 'or-rdns')
        cls.n_cl = _node(cls.org, 'or-rdns-cl', 'client', cls.n_co, 1, 'or-rdns/or-rdns-cl')
        cls.n_site = _node(cls.org, 'or-rdns-site', 'site', cls.n_cl, 2, 'or-rdns/or-rdns-cl/or-rdns-site')

        cls.real_client = Client.objects.create(
            org=cls.org, name='OR Existing Client', code='or-rdns-cl',
            scope_node=cls.n_cl, is_active=True,
        )
        cls.real_site = SiteProfile.objects.create(
            org=cls.org, client=cls.real_client, name='OR Site', code='or-rdns-site',
            scope_node=cls.n_site, is_active=True,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='or-rdns-guard', skill_category='unskilled',
        )

        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = _user('or_rdns_admin', cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_co)

        cls.approver = _user('or_rdns_approver', cls.org)

        # Client-onboarding workflow config
        cls.co_template = _wf_template(cls.org, 'client_onboarding', 'or-rdns-co-tmpl')
        cls.co_step = _wf_step(cls.co_template, 1, 'or_rdns_co_review')
        _wf_mapping(cls.org, cls.co_template)
        _wf_sac(cls.org, 'client_onboarding', 'or_rdns_co_review', cls.approver)

        # MRF workflow config (for regression test)
        cls.mrf_template = _wf_template(cls.org, 'mrf', 'or-rdns-mrf-tmpl')
        cls.mrf_step = _wf_step(cls.mrf_template, 1, 'or_rdns_mrf_review')
        _wf_mapping(cls.org, cls.mrf_template)
        _wf_sac(cls.org, 'mrf', 'or_rdns_mrf_review', cls.approver)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestClientOnboardingReadinessA(OBReadinessBase):

    # ── test_01 ──────────────────────────────────────────────────────────────

    def test_01_new_client_no_proposed_data_cannot_start_workflow(self):
        """new_client with no proposed setup at all — workflow start returns 400."""
        req = _new_client_request(self.org, self.admin)
        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('errors', resp.data)
        self.assertTrue(len(resp.data['errors']) > 0)
        self.assertEqual(resp.data['detail'], 'Onboarding setup is incomplete.')

    # ── test_02 ──────────────────────────────────────────────────────────────

    def test_02_missing_proposed_sites_returns_readable_error(self):
        """Missing proposed sites gives a clear error message in 400 response."""
        req = _new_client_request(self.org, self.admin)
        # Add dept, SRR would need a site, budget — but no site
        _p_budget(req, code='rp02-budget')

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)
        errors = resp.data['errors']
        site_error = any('site' in e.lower() for e in errors)
        self.assertTrue(site_error, f"Expected site error in: {errors}")

    # ── test_03 ──────────────────────────────────────────────────────────────

    def test_03_expected_site_count_not_met_returns_400(self):
        """expected_site_count=2 but only 1 active site → blocked with site-count error."""
        req = _new_client_request(self.org, self.admin, expected_site_count=2)
        site = _p_site(req, 'rp03-s1', contact=True)
        _p_dept(req, proposed_site=site, code='rp03-d1')
        _p_srr(req, site, self.job_role, billing=True)
        _p_budget(req, code='rp03-b1')

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)
        errors = resp.data['errors']
        count_error = any('expected 2' in e.lower() or 'only 1' in e.lower() for e in errors)
        self.assertTrue(count_error, f"Expected site-count error in: {errors}")

    # ── test_04 ──────────────────────────────────────────────────────────────

    def test_04_no_budget_blocks_workflow_start(self):
        """Sites, departments and SRRs present but no proposed budget → 400."""
        req = _new_client_request(self.org, self.admin)
        site = _p_site(req, 'rp04-s1', contact=True)
        _p_dept(req, proposed_site=site, code='rp04-d1')
        _p_srr(req, site, self.job_role, billing=True)
        # no budget

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)
        errors = resp.data['errors']
        budget_error = any('budget' in e.lower() for e in errors)
        self.assertTrue(budget_error, f"Expected budget error in: {errors}")

    # ── test_05 ──────────────────────────────────────────────────────────────

    def test_05_complete_new_client_setup_starts_workflow(self):
        """Complete new_client proposed setup allows workflow to start (201)."""
        req = _new_client_request(self.org, self.admin)
        _add_full_proposed_setup(req, self.job_role)
        _p_user(req, self.role_admin)

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

    # ── test_06 ──────────────────────────────────────────────────────────────

    def test_06_new_site_expansion_missing_client_cannot_start(self):
        """new_site_expansion with client=None is blocked by readiness check."""
        # Create directly via ORM to bypass serializer validation
        req = ClientOnboardingRequest.objects.create(
            org=self.org,
            client=None,
            requested_by=self.admin,
            onboarding_type='new_site_expansion',
            status='draft',
        )
        site = _p_site(req, 'rp06-s1', contact=True)
        _p_dept(req, proposed_site=site, code='rp06-d1')
        _p_srr(req, site, self.job_role, billing=True)
        _p_budget(req, code='rp06-b1')

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        errors = resp.data['errors']
        client_error = any('client' in e.lower() for e in errors)
        self.assertTrue(client_error, f"Expected client error in: {errors}")

    # ── test_07 ──────────────────────────────────────────────────────────────

    def test_07_complete_new_site_expansion_starts_workflow(self):
        """Complete new_site_expansion setup allows workflow to start (201)."""
        req = _expansion_request(self.org, self.real_client, self.admin)
        _add_full_proposed_setup(req, self.job_role)

        resp = self.api.post(_start_url(req.pk), {}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

    # ── test_08 ──────────────────────────────────────────────────────────────

    def test_08_serializer_returns_readiness_false_for_incomplete(self):
        """Detail endpoint includes readiness_ok=false and non-empty errors for incomplete request."""
        req = _new_client_request(self.org, self.admin)

        resp = self.api.get(_detail_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['readiness_ok'])
        self.assertTrue(len(resp.data['readiness_errors']) > 0)
        self.assertIn('readiness_warnings', resp.data)

    # ── test_09 ──────────────────────────────────────────────────────────────

    def test_09_serializer_returns_readiness_true_for_complete(self):
        """Detail endpoint includes readiness_ok=true and empty errors for complete request."""
        req = _new_client_request(self.org, self.admin)
        _add_full_proposed_setup(req, self.job_role)
        _p_user(req, self.role_admin)

        resp = self.api.get(_detail_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['readiness_ok'])
        self.assertEqual(resp.data['readiness_errors'], [])

    # ── test_10 ──────────────────────────────────────────────────────────────

    def test_10_mrf_workflow_start_regression(self):
        """Readiness gate does NOT affect MRF workflow start — MRF still starts normally."""
        mrf = ManpowerRequest.objects.create(
            org=self.org,
            site=self.real_site,
            requested_by=self.admin,
            mrf_type='new_hiring',
            billing_type='billable',
            status='submitted',
        )
        srr = SiteRoleRequirement.objects.create(
            site=self.real_site,
            job_role=self.job_role,
            approved_headcount=10,
            billing_type='billable',
            effective_from=datetime.date.today(),
            is_active=True,
        )
        MRFLineItem.objects.create(
            mrf=mrf,
            job_role=self.job_role,
            site_role_requirement=srr,
            headcount=1,
        )
        resp = self.api.post(f'/api/workflow/mrf/{mrf.pk}/start/', {}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'hr_review')

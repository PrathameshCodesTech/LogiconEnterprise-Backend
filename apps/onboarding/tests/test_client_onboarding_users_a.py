"""
apps/onboarding/tests/test_client_onboarding_users_a.py

Phase Client-Onboarding-Users-A — 18 tests:

CRUD/validation:
  01. Create client-level proposed user — success (201)
  02. Site-level user without proposed_site — 400
  03. proposed_site from different onboarding request — 400
  04. access_role from wrong org — 400
  05. Duplicate active email in same request — 400
  06. Real User with same email already in org — 400
  07. Second active primary contact in same request — 400
  08. Proposed user edits blocked when request is in_review

Readiness:
  09. new_client with no proposed users → readiness_ok=False, error present
  10. new_client with proposed user → readiness_ok=True

Finalization:
  11. Finalization creates real User with correct fields
  12. Client-scope UserRoleAssignment created correctly
  13. Site-scope UserRoleAssignment uses real site's scope_node
  14. Finalization is idempotent (proposed user not duplicated on second call)
  15. Rejected request creates no real users
  16. invite_status='pending' when send_invite_on_finalization=True
  17. invite_status='not_required' when send_invite_on_finalization=False
  18. MRF workflow regression — unaffected
"""

import datetime

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.onboarding.models import (
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
    ClientOnboardingRequest,
)
from apps.onboarding.services import finalize_client_onboarding_request
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)
from apps.workflow.services import act_on_step, start_client_onboarding_workflow


# ─── Base ─────────────────────────────────────────────────────────────────────

class OBUsersBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='UTest Org', code='utest-org')

        cls.n_co = ScopeNode.objects.create(
            org=cls.org, code='utest-org', name='UTest Org', node_type='company',
            parent=None, depth=0, path='utest-org', is_active=True,
        )
        cls.n_cl = ScopeNode.objects.create(
            org=cls.org, code='utest-cl', name='UTest Client', node_type='client',
            parent=cls.n_co, depth=1, path='utest-org/utest-cl', is_active=True,
        )
        cls.n_site = ScopeNode.objects.create(
            org=cls.org, code='utest-site', name='UTest Site', node_type='site',
            parent=cls.n_cl, depth=2, path='utest-org/utest-cl/utest-site', is_active=True,
        )

        cls.existing_client = Client.objects.create(
            org=cls.org, name='UTest Existing Client', code='utest-cl',
            scope_node=cls.n_cl, is_active=True,
        )
        cls.existing_site = SiteProfile.objects.create(
            org=cls.org, client=cls.existing_client, name='UTest Site', code='utest-site',
            scope_node=cls.n_site, is_active=True,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Security Guard', code='utest-guard', skill_category='unskilled',
        )

        cls.role_admin = AccessRole.objects.get_or_create(
            org=cls.org, code='admin', defaults={'name': 'Admin'},
        )[0]
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = User.objects.create_user(username='utest_admin', password='pass')
        cls.admin.org = cls.org
        cls.admin.save()
        UserRoleAssignment.objects.create(
            user=cls.admin, role=cls.role_admin, scope_node=cls.n_co,
        )

        cls.approver = User.objects.create_user(username='utest_approver', password='pass')
        cls.approver.org = cls.org
        cls.approver.save()
        UserRoleAssignment.objects.create(
            user=cls.approver, role=cls.role_admin, scope_node=cls.n_co,
        )

        # Client-onboarding workflow: 1 step
        cls.co_template = WorkflowTemplate.objects.create(
            org=cls.org, name='utest-co-tmpl', code='utest-co-tmpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        cls.co_step = WorkflowStepTemplate.objects.create(
            template=cls.co_template, order=1, code='utest_co_review', name='Review',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='client_onboarding', template=cls.co_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='client_onboarding', step_code='utest_co_review',
            assignment_mode='named_user', named_user=cls.approver, is_active=True,
        )

        # MRF workflow (for regression test)
        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='utest-mrf-tmpl', code='utest-mrf-tmpl',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.mrf_step = WorkflowStepTemplate.objects.create(
            template=cls.mrf_template, order=1, code='utest_mrf_review', name='MRF Review',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.mrf_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='utest_mrf_review',
            assignment_mode='named_user', named_user=cls.approver, is_active=True,
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)

    # ── URL helpers ──────────────────────────────────────────────────────────

    def _users_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-users/'

    def _user_detail_url(self, req_pk, pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-users/{pk}/'

    def _detail_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/'

    def _start_url(self, ob_id):
        return f'/api/workflow/client-onboarding/{ob_id}/start/'

    # ── Object factories ─────────────────────────────────────────────────────

    def _new_client_request(self, **kwargs):
        defaults = dict(
            org=self.org, requested_by=self.admin,
            onboarding_type='new_client',
            proposed_client_name='UTest Corp',
            proposed_client_code='utcorp',
            status='draft',
        )
        defaults.update(kwargs)
        return ClientOnboardingRequest.objects.create(**defaults)

    def _p_site(self, req, code='ups-001'):
        return ClientOnboardingProposedSite.objects.create(
            request=req, name=f'Site {code}', code=code, is_active=True,
        )

    def _p_dept(self, req, p_site=None, code='upd-001'):
        return ClientOnboardingProposedDepartment.objects.create(
            request=req, proposed_site=p_site, name=f'Dept {code}', code=code,
            scope_level='site' if p_site else 'client', is_active=True,
        )

    def _p_srr(self, req, p_site):
        return ClientOnboardingProposedSiteRoleRequirement.objects.create(
            request=req, proposed_site=p_site, job_role=self.job_role,
            approved_headcount=5, billing_type='billable', is_active=True,
        )

    def _p_budget(self, req, code='upb-001'):
        return ClientOnboardingProposedBudget.objects.create(
            request=req, name=f'Budget {code}', code=code,
            budget_nature='billable', budget_type='onboarding',
            scope_level='client', amount='100000.00', currency='INR',
            period_start=datetime.date.today(), is_active=True,
        )

    def _p_user_orm(self, req, *, email, full_name='Test User', scope_level='client',
                    proposed_site=None, is_primary=False, send_invite=True, is_active=True):
        return ClientOnboardingProposedUser.objects.create(
            request=req, full_name=full_name, email=email,
            user_type='client', access_role=self.role_admin,
            scope_level=scope_level, proposed_site=proposed_site,
            is_primary_contact=is_primary, send_invite_on_finalization=send_invite,
            is_active=is_active,
        )

    def _full_approve(self, req):
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        step = instance.steps.order_by('step_order').first()
        act_on_step(step, actor=self.approver, action='approve')
        instance.refresh_from_db()
        return instance

    def _reject_final(self, req):
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        step = instance.steps.order_by('step_order').first()
        act_on_step(step, actor=self.approver, action='reject', comment='Not approved.')
        instance.refresh_from_db()
        return instance


# ─── Tests 01–08: CRUD / validation ──────────────────────────────────────────

class TestProposedUserCRUD(OBUsersBase):

    def test_01_create_client_level_user_success(self):
        """POST with scope_level=client and valid fields → 201 with correct data."""
        req = self._new_client_request()
        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Alice Smith',
            'email': 'alice@utestcorp.com',
            'user_type': 'client',
            'access_role': self.role_admin.pk,
            'scope_level': 'client',
            'is_primary_contact': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['email'], 'alice@utestcorp.com')
        self.assertEqual(resp.data['scope_level'], 'client')
        self.assertIsNone(resp.data['proposed_site'])
        self.assertTrue(resp.data['is_primary_contact'])
        self.assertEqual(resp.data['invite_status'], 'pending')

    def test_02_site_level_user_without_proposed_site_rejected(self):
        """scope_level=site without proposed_site → 400 with error on proposed_site."""
        req = self._new_client_request()
        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Bob Jones',
            'email': 'bob@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'site',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_site', resp.data)

    def test_03_proposed_site_from_different_request_rejected(self):
        """proposed_site belonging to a different onboarding request → 400."""
        req1 = self._new_client_request(proposed_client_code='utcorp-a', proposed_client_name='A')
        req2 = self._new_client_request(proposed_client_code='utcorp-b', proposed_client_name='B')
        p_site_req2 = self._p_site(req2, code='utp-site-other')

        resp = self.api.post(self._users_url(req1.pk), {
            'full_name': 'Carol White',
            'email': 'carol@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'site',
            'proposed_site': p_site_req2.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_site', resp.data)

    def test_04_access_role_from_wrong_org_rejected(self):
        """access_role belonging to a different organization → 400."""
        other_org = Organization.objects.create(name='Other Org', code='other-org-u')
        other_role = AccessRole.objects.create(
            org=other_org, code='other-role-u', name='Other Role',
        )
        req = self._new_client_request()
        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Dave Green',
            'email': 'dave@utestcorp.com',
            'access_role': other_role.pk,
            'scope_level': 'client',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('access_role', resp.data)

    def test_05_duplicate_active_email_in_same_request_rejected(self):
        """Two active proposed users with the same email on one request → 400."""
        req = self._new_client_request()
        self._p_user_orm(req, email='dup@utestcorp.com')

        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Duplicate User',
            'email': 'dup@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'client',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('email', resp.data)

    def test_06_real_user_with_same_email_in_org_rejected(self):
        """Real User already exists with same email in same org → 400."""
        real_user = User.objects.create_user(
            username='existing_ut', password='pass', email='existing@utestcorp.com',
        )
        real_user.org = self.org
        real_user.save()

        req = self._new_client_request()
        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Existing Email',
            'email': 'existing@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'client',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('email', resp.data)

    def test_07_second_active_primary_contact_rejected(self):
        """Two active primary contacts on the same request → 400."""
        req = self._new_client_request()
        self._p_user_orm(req, email='primary1@utestcorp.com', is_primary=True)

        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Second Primary',
            'email': 'primary2@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'client',
            'is_primary_contact': True,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('is_primary_contact', resp.data)

    def test_08_edits_blocked_when_request_in_review(self):
        """Cannot add proposed users when onboarding request status is in_review."""
        req = self._new_client_request()
        start_client_onboarding_workflow(req, actor=self.admin)
        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

        resp = self.api.post(self._users_url(req.pk), {
            'full_name': 'Blocked User',
            'email': 'blocked@utestcorp.com',
            'access_role': self.role_admin.pk,
            'scope_level': 'client',
        }, format='json')
        self.assertEqual(resp.status_code, 403)


# ─── Tests 09–10: Readiness ───────────────────────────────────────────────────

class TestProposedUserReadiness(OBUsersBase):

    def test_09_new_client_without_users_readiness_fails(self):
        """new_client request with all other setup but no users → readiness_ok=False."""
        req = self._new_client_request()
        p_site = self._p_site(req, code='rdns09-site')
        self._p_dept(req, p_site=p_site, code='rdns09-dept')
        self._p_srr(req, p_site)
        self._p_budget(req, code='rdns09-budget')
        # No proposed user

        resp = self.api.get(self._detail_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['readiness_ok'])
        user_error = any('user' in e.lower() for e in resp.data['readiness_errors'])
        self.assertTrue(user_error, f"Expected user error in: {resp.data['readiness_errors']}")

    def test_10_new_client_with_complete_setup_including_user_readiness_passes(self):
        """new_client request with full setup including a proposed user → readiness_ok=True."""
        req = self._new_client_request()
        p_site = self._p_site(req, code='rdns10-site')
        self._p_dept(req, p_site=p_site, code='rdns10-dept')
        self._p_srr(req, p_site)
        self._p_budget(req, code='rdns10-budget')
        self._p_user_orm(req, email='rdns10@utestcorp.com', is_primary=True)

        resp = self.api.get(self._detail_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['readiness_ok'])
        self.assertEqual(resp.data['readiness_errors'], [])


# ─── Tests 11–18: Finalization ────────────────────────────────────────────────

class TestProposedUserFinalization(OBUsersBase):

    def _approved_new_client_request(self, **kwargs):
        """Create a new_client request pre-set to approved status."""
        defaults = dict(
            org=self.org, requested_by=self.admin,
            onboarding_type='new_client',
            proposed_client_name='Fin Corp',
            proposed_client_code='fin-corp-u',
            status='approved',
        )
        defaults.update(kwargs)
        return ClientOnboardingRequest.objects.create(**defaults)

    def test_11_finalization_creates_real_user_with_correct_fields(self):
        """Finalization creates a real User with email, first_name, last_name, org."""
        req = self._approved_new_client_request(proposed_client_code='fintest11')
        self._p_user_orm(req, email='fintest11@corp.com', full_name='John Doe')

        finalize_client_onboarding_request(req, actor=self.admin)

        user = User.objects.get(email='fintest11@corp.com')
        self.assertEqual(user.first_name, 'John')
        self.assertEqual(user.last_name, 'Doe')
        self.assertEqual(user.org, self.org)
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_12_finalization_creates_client_scope_role_assignment(self):
        """Client-level proposed user gets a UserRoleAssignment on the client's scope_node."""
        req = self._approved_new_client_request(proposed_client_code='fintest12')
        self._p_user_orm(req, email='fintest12@corp.com', scope_level='client')

        finalize_client_onboarding_request(req, actor=self.admin)
        req.refresh_from_db()

        real_client = req.created_client
        self.assertIsNotNone(real_client)
        self.assertIsNotNone(real_client.scope_node)

        real_user = User.objects.get(email='fintest12@corp.com')
        assignment = UserRoleAssignment.objects.filter(
            user=real_user, role=self.role_admin,
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.scope_node, real_client.scope_node)

    def test_13_finalization_creates_site_scope_role_assignment(self):
        """Site-level proposed user gets UserRoleAssignment on the real site's scope_node."""
        req = self._approved_new_client_request(proposed_client_code='fintest13')
        p_site = self._p_site(req, code='ft13-site')
        self._p_user_orm(
            req, email='fintest13@corp.com', scope_level='site', proposed_site=p_site,
        )

        finalize_client_onboarding_request(req, actor=self.admin)

        real_user = User.objects.get(email='fintest13@corp.com')
        real_site = SiteProfile.objects.get(org=self.org, code='ft13-site')
        self.assertIsNotNone(real_site.scope_node)

        assignment = UserRoleAssignment.objects.filter(
            user=real_user, role=self.role_admin,
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.scope_node, real_site.scope_node)

    def test_14_finalization_is_idempotent(self):
        """Calling finalize a second time does not create a second real User."""
        req = self._approved_new_client_request(proposed_client_code='fintest14')
        self._p_user_orm(req, email='fintest14@corp.com')

        finalize_client_onboarding_request(req, actor=self.admin)
        user_count_after_first = User.objects.filter(
            org=self.org, email='fintest14@corp.com',
        ).count()
        self.assertEqual(user_count_after_first, 1)

        finalize_client_onboarding_request(req, actor=self.admin)
        self.assertEqual(
            User.objects.filter(org=self.org, email='fintest14@corp.com').count(),
            1,
        )

    def test_15_rejected_request_creates_no_real_users(self):
        """Rejected onboarding: no real User created from proposed users."""
        req = self._new_client_request()
        self._p_user_orm(req, email='rejected15@corp.com')

        self._reject_final(req)
        req.refresh_from_db()

        self.assertEqual(req.status, 'rejected')
        self.assertEqual(req.finalization_status, 'not_finalized')
        self.assertFalse(
            User.objects.filter(email='rejected15@corp.com').exists(),
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='noreply@test.local',
        FRONTEND_BASE_URL='http://testserver',
    )
    def test_16_invite_status_sent_when_send_invite_true(self):
        """send_invite_on_finalization=True → invite_status='sent' after finalization."""
        req = self._approved_new_client_request(proposed_client_code='fintest16')
        p_user = self._p_user_orm(req, email='invite16@corp.com', send_invite=True)

        finalize_client_onboarding_request(req, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'sent')

    def test_17_invite_status_not_required_when_send_invite_false(self):
        """send_invite_on_finalization=False → invite_status='not_required' after finalization."""
        req = self._approved_new_client_request(proposed_client_code='fintest17')
        p_user = self._p_user_orm(req, email='noinvite17@corp.com', send_invite=False)

        finalize_client_onboarding_request(req, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'not_required')

    def test_18_mrf_workflow_unaffected_by_proposed_user_changes(self):
        """MRF workflow start still works — not broken by proposed user finalization logic."""
        mrf = ManpowerRequest.objects.create(
            org=self.org,
            site=self.existing_site,
            requested_by=self.admin,
            mrf_type='new_hiring',
            billing_type='billable',
            status='submitted',
        )
        srr = SiteRoleRequirement.objects.create(
            site=self.existing_site,
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

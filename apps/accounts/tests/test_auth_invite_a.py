"""
apps/accounts/tests/test_auth_invite_a.py

Phase Auth-Invite-A — 17 tests:

Set-password endpoint:
  01. Valid uid+token+password → 200, user can authenticate
  02. Invalid uid → 400 with uid error
  03. Invalid token → 400 with token error
  04. Password mismatch → 400 with confirm_password error
  05. Weak password → 400 with password error
  06. Re-using a token after password is set → 400 (token invalidated)

Finalization invite integration:
  07. send_invite_on_finalization=True → invite_status='sent', email in outbox
  08. Invite email sent to correct recipient address
  09. Email body contains set-password URL with uid and token params
  10. send_invite_on_finalization=False → invite_status='not_required', no email
  11. Email failure during finalization → invite_status='failed', finalization still succeeds

Resend invite service:
  12. resend_onboarding_proposed_user_invite sends email, invite_status='sent'
  13. resend on unfinalized proposed user raises ValueError

Resend invite API action:
  14. POST resend-invite on finalized user → 200, email in outbox
  15. POST resend-invite on unfinalized user → 400
  16. POST resend-invite updates invite_status to 'sent'
  17. Token from invite email is valid for set-password endpoint
"""

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.accounts.services import send_user_invite_email
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.onboarding.models import (
    ClientOnboardingProposedSite,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
    ClientOnboardingRequest,
)
from apps.onboarding.services import (
    finalize_client_onboarding_request,
    resend_onboarding_proposed_user_invite,
)
from apps.sites.models import Client, SiteProfile
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)

SET_PASSWORD_URL = '/api/accounts/set-password/'

EMAIL_OVERRIDES = dict(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@test.local',
    FRONTEND_BASE_URL='http://testserver',
)


# ─── Base fixtures ────────────────────────────────────────────────────────────

class InviteBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Invite Org', code='inv-org')

        cls.n_co = ScopeNode.objects.create(
            org=cls.org, code='inv-org', name='Invite Org', node_type='company',
            parent=None, depth=0, path='inv-org', is_active=True,
        )
        cls.n_cl = ScopeNode.objects.create(
            org=cls.org, code='inv-cl', name='Invite Client', node_type='client',
            parent=cls.n_co, depth=1, path='inv-org/inv-cl', is_active=True,
        )

        cls.role_admin = AccessRole.objects.get_or_create(
            org=cls.org, code='admin', defaults={'name': 'Admin'},
        )[0]
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = User.objects.create_user(username='inv_admin', password='pass')
        cls.admin.org = cls.org
        cls.admin.save()
        UserRoleAssignment.objects.create(
            user=cls.admin, role=cls.role_admin, scope_node=cls.n_co,
        )

        cls.approver = User.objects.create_user(username='inv_approver', password='pass')
        cls.approver.org = cls.org
        cls.approver.save()
        UserRoleAssignment.objects.create(
            user=cls.approver, role=cls.role_admin, scope_node=cls.n_co,
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard', code='inv-guard', skill_category='unskilled',
        )

        cls.co_template = WorkflowTemplate.objects.create(
            org=cls.org, name='inv-co-tmpl', code='inv-co-tmpl',
            trigger_type='client_onboarding', version=1, is_active=True,
        )
        cls.co_step = WorkflowStepTemplate.objects.create(
            template=cls.co_template, order=1, code='inv_co_review', name='Review',
            assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='client_onboarding', template=cls.co_template,
            client=None, site=None, is_active=True,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='client_onboarding', step_code='inv_co_review',
            assignment_mode='named_user', named_user=cls.approver, is_active=True,
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)

    def _target_user(self, *, username='target_user', email='target@corp.test'):
        user = User.objects.create_user(username=username, password='OldPass123!')
        user.org = self.org
        user.save()
        return user

    def _approved_request(self, code):
        return ClientOnboardingRequest.objects.create(
            org=self.org,
            requested_by=self.admin,
            onboarding_type='new_client',
            proposed_client_name='Invite Corp',
            proposed_client_code=code,
            status='approved',
        )

    def _p_user(self, req, *, email, send_invite=True, full_name='Alice Smith'):
        return ClientOnboardingProposedUser.objects.create(
            request=req,
            full_name=full_name,
            email=email,
            user_type='client',
            access_role=self.role_admin,
            scope_level='client',
            is_primary_contact=True,
            send_invite_on_finalization=send_invite,
            is_active=True,
        )

    def _uid_token(self, user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = PasswordResetTokenGenerator().make_token(user)
        return uid, token


# ─── Tests 01–06: set-password endpoint ──────────────────────────────────────

class TestSetPassword(InviteBase):

    def test_01_valid_uid_token_sets_password(self):
        """Valid uid + token + matching password → 200, user can authenticate."""
        user = self._target_user(username='sp01', email='sp01@test.local')
        uid, token = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': token,
            'password': 'NewStr0ng#Pass', 'confirm_password': 'NewStr0ng#Pass',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        user.refresh_from_db()
        self.assertTrue(user.check_password('NewStr0ng#Pass'))

    def test_02_invalid_uid_returns_400(self):
        """Corrupted uid → 400 with error on uid field."""
        user = self._target_user(username='sp02', email='sp02@test.local')
        _, token = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': 'not-a-real-uid', 'token': token,
            'password': 'NewStr0ng#Pass', 'confirm_password': 'NewStr0ng#Pass',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('uid', resp.data)

    def test_03_invalid_token_returns_400(self):
        """Valid uid but wrong token → 400 with error on token field."""
        user = self._target_user(username='sp03', email='sp03@test.local')
        uid, _ = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': 'bad-token',
            'password': 'NewStr0ng#Pass', 'confirm_password': 'NewStr0ng#Pass',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('token', resp.data)

    def test_04_password_mismatch_returns_400(self):
        """password != confirm_password → 400 with error on confirm_password."""
        user = self._target_user(username='sp04', email='sp04@test.local')
        uid, token = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': token,
            'password': 'NewStr0ng#Pass', 'confirm_password': 'Mismatch!',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('confirm_password', resp.data)

    def test_05_weak_password_returns_400(self):
        """Password fails Django validators → 400 with error on password field."""
        user = self._target_user(username='sp05', email='sp05@test.local')
        uid, token = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': token,
            'password': '123', 'confirm_password': '123',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('password', resp.data)

    def test_06_token_invalid_after_password_set(self):
        """After setting password, the same token is no longer valid."""
        user = self._target_user(username='sp06', email='sp06@test.local')
        uid, token = self._uid_token(user)

        payload = {
            'uid': uid, 'token': token,
            'password': 'NewStr0ng#Pass', 'confirm_password': 'NewStr0ng#Pass',
        }
        self.api.post(SET_PASSWORD_URL, payload, format='json')

        resp = self.api.post(SET_PASSWORD_URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_06b_inactive_user_cannot_set_password(self):
        """Inactive users cannot use an otherwise valid set-password token."""
        user = self._target_user(username='sp06b', email='sp06b@test.local')
        user.is_active = False
        user.save(update_fields=['is_active'])
        uid, token = self._uid_token(user)

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': token,
            'password': 'NewStr0ng#Pass', 'confirm_password': 'NewStr0ng#Pass',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('uid', resp.data)


# ─── Tests 07–11: Finalization invite integration ─────────────────────────────

@override_settings(**EMAIL_OVERRIDES)
class TestFinalizationInvite(InviteBase):

    def test_07_send_invite_true_sets_invite_status_sent_and_queues_email(self):
        """send_invite_on_finalization=True → invite_status='sent', 1 email in outbox."""
        req = self._approved_request('inv07')
        p_user = self._p_user(req, email='inv07@corp.test', send_invite=True)

        finalize_client_onboarding_request(req, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_08_invite_email_sent_to_correct_recipient(self):
        """Invite email is addressed to the proposed user's email."""
        req = self._approved_request('inv08')
        self._p_user(req, email='inv08@corp.test', send_invite=True)

        finalize_client_onboarding_request(req, actor=self.admin)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('inv08@corp.test', mail.outbox[0].to)

    def test_09_invite_email_body_contains_set_password_url(self):
        """Email body contains the set-password URL with uid and token query params."""
        req = self._approved_request('inv09')
        self._p_user(req, email='inv09@corp.test', send_invite=True)

        finalize_client_onboarding_request(req, actor=self.admin)

        body = mail.outbox[0].body
        self.assertIn('/set-password?uid=', body)
        self.assertIn('&token=', body)

    def test_10_send_invite_false_skips_email(self):
        """send_invite_on_finalization=False → invite_status='not_required', no email."""
        req = self._approved_request('inv10')
        p_user = self._p_user(req, email='inv10@corp.test', send_invite=False)

        finalize_client_onboarding_request(req, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'not_required')
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND='apps.accounts.tests.test_auth_invite_a.FailingEmailBackend',
        DEFAULT_FROM_EMAIL='noreply@test.local',
        FRONTEND_BASE_URL='http://testserver',
    )
    def test_11_email_failure_does_not_abort_finalization(self):
        """SMTP failure → invite_status='failed', but finalization_status='finalized'."""
        req = self._approved_request('inv11')
        p_user = self._p_user(req, email='inv11@corp.test', send_invite=True)

        finalize_client_onboarding_request(req, actor=self.admin)

        req.refresh_from_db()
        p_user.refresh_from_db()
        self.assertEqual(req.finalization_status, 'finalized')
        self.assertEqual(p_user.invite_status, 'failed')


# ─── Tests 12–13: Resend invite service ───────────────────────────────────────

@override_settings(**EMAIL_OVERRIDES)
class TestResendInviteService(InviteBase):

    def test_12_resend_service_sends_email_and_sets_status_sent(self):
        """resend_onboarding_proposed_user_invite sends email and sets invite_status='sent'."""
        req = self._approved_request('inv12')
        p_user = self._p_user(req, email='inv12@corp.test', send_invite=False)
        finalize_client_onboarding_request(req, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'not_required')

        resend_onboarding_proposed_user_invite(p_user, actor=self.admin)

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'sent')
        self.assertGreaterEqual(len(mail.outbox), 1)

    def test_13_resend_service_raises_if_user_not_finalized(self):
        """resend on a proposed user with no created_user raises ValueError."""
        req = self._approved_request('inv13')
        p_user = self._p_user(req, email='inv13@corp.test', send_invite=True)

        with self.assertRaises(ValueError):
            resend_onboarding_proposed_user_invite(p_user, actor=self.admin)


# ─── Tests 14–17: Resend invite API action ────────────────────────────────────

@override_settings(**EMAIL_OVERRIDES)
class TestResendInviteAPI(InviteBase):

    def _resend_url(self, req_pk, p_user_pk):
        return (
            f'/api/onboarding/client-requests/{req_pk}'
            f'/proposed-users/{p_user_pk}/resend-invite/'
        )

    def test_14_resend_api_returns_200_and_sends_email(self):
        """POST resend-invite on a finalized proposed user → 200, email in outbox."""
        req = self._approved_request('inv14')
        p_user = self._p_user(req, email='inv14@corp.test', send_invite=False)
        finalize_client_onboarding_request(req, actor=self.admin)

        resp = self.api.post(self._resend_url(req.pk, p_user.pk), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertGreaterEqual(len(mail.outbox), 1)

    def test_15_resend_api_returns_400_for_unfinalized_user(self):
        """POST resend-invite on proposed user with no real user → 400."""
        req = self._approved_request('inv15')
        p_user = self._p_user(req, email='inv15@corp.test', send_invite=True)

        resp = self.api.post(self._resend_url(req.pk, p_user.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_16_resend_api_updates_invite_status_to_sent(self):
        """POST resend-invite updates the proposed user's invite_status to 'sent'."""
        req = self._approved_request('inv16')
        p_user = self._p_user(req, email='inv16@corp.test', send_invite=False)
        finalize_client_onboarding_request(req, actor=self.admin)

        self.api.post(self._resend_url(req.pk, p_user.pk), {}, format='json')

        p_user.refresh_from_db()
        self.assertEqual(p_user.invite_status, 'sent')

    def test_17_invite_email_token_is_valid_for_set_password(self):
        """The uid+token embedded in the invite email can be used to set a password."""
        req = self._approved_request('inv17')
        p_user = self._p_user(req, email='inv17@corp.test', send_invite=True)
        finalize_client_onboarding_request(req, actor=self.admin)

        body = mail.outbox[0].body
        # Parse uid and token from the URL in the email body
        import urllib.parse
        url_start = body.find('/set-password?')
        self.assertGreater(url_start, -1, "set-password URL not found in email body")
        query_str = body[url_start:].split('\n')[0].split('?', 1)[1].strip()
        params = dict(urllib.parse.parse_qsl(query_str))
        uid = params['uid']
        token = params['token']

        resp = self.api.post(SET_PASSWORD_URL, {
            'uid': uid, 'token': token,
            'password': 'NewStr0ng#Pass!', 'confirm_password': 'NewStr0ng#Pass!',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        real_user = User.objects.get(email='inv17@corp.test')
        self.assertTrue(real_user.check_password('NewStr0ng#Pass!'))


# ─── Helper: failing email backend for test 11 ────────────────────────────────

from django.core.mail.backends.base import BaseEmailBackend


class FailingEmailBackend(BaseEmailBackend):
    """Always raises OSError — simulates SMTP connection failure."""

    def send_messages(self, email_messages):
        raise OSError("Simulated SMTP failure")

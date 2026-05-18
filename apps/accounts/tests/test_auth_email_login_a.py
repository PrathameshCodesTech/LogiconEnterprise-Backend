"""
apps/accounts/tests/test_auth_email_login_a.py

Phase Auth-Email-Login-A — 8 tests covering:
  01. Correct email + password returns access and refresh tokens
  02. Email lookup is case-insensitive
  03. Wrong password returns 401
  04. Unknown email returns 401
  05. Inactive user returns 401
  06. Username-only payload (no email field) returns 400
  07. Duplicate email on user create is rejected with 400
  08. Token obtained via email login grants access to authenticated endpoint
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode

TOKEN_URL = '/api/token/'
USERS_URL = '/api/accounts/users/'


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code,
        node_type='company', parent=None, depth=0,
        path=org.code, is_active=True,
    )


def _role(org, code='admin'):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    bootstrap_role_permissions(r)
    return r


def _user(username, email, password, org, scope_node, role, is_active=True):
    u = User.objects.create_user(username=username, password=password)
    u.email = email
    u.org = org
    u.is_active = is_active
    u.save()
    UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


# ─── Base ────────────────────────────────────────────────────────────────────

class AuthEmailLoginBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('auth-a')
        cls.node = _scope_node(cls.org)
        cls.role = _role(cls.org, 'admin')
        cls.active_user = _user(
            'active.user', 'active@logicon.local', 'Pass@1234',
            cls.org, cls.node, cls.role,
        )
        cls.inactive_user = _user(
            'inactive.user', 'inactive@logicon.local', 'Pass@1234',
            cls.org, cls.node, cls.role, is_active=False,
        )

    def setUp(self):
        self.api = APIClient()

    def _login(self, email, password):
        return self.api.post(TOKEN_URL, {'email': email, 'password': password}, format='json')


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestAuthEmailLoginA(AuthEmailLoginBase):

    # ── test_01 ──────────────────────────────────────────────────────────────

    def test_01_correct_credentials_return_tokens(self):
        """Correct email + password returns both access and refresh tokens."""
        resp = self._login('active@logicon.local', 'Pass@1234')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        self.assertTrue(resp.data['access'])
        self.assertTrue(resp.data['refresh'])

    # ── test_02 ──────────────────────────────────────────────────────────────

    def test_02_email_lookup_is_case_insensitive(self):
        """Email matching ignores case — ACTIVE@LOGICON.LOCAL should work."""
        resp = self._login('ACTIVE@LOGICON.LOCAL', 'Pass@1234')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('access', resp.data)

    # ── test_03 ──────────────────────────────────────────────────────────────

    def test_03_wrong_password_returns_401(self):
        """Correct email but wrong password is rejected with 401."""
        resp = self._login('active@logicon.local', 'WrongPassword!')
        self.assertEqual(resp.status_code, 401)

    # ── test_04 ──────────────────────────────────────────────────────────────

    def test_04_unknown_email_returns_401(self):
        """Email not in the system returns 401 (not 400 or 404)."""
        resp = self._login('nobody@logicon.local', 'Pass@1234')
        self.assertEqual(resp.status_code, 401)

    # ── test_05 ──────────────────────────────────────────────────────────────

    def test_05_inactive_user_returns_401(self):
        """An inactive account returns 401 even with correct credentials."""
        resp = self._login('inactive@logicon.local', 'Pass@1234')
        self.assertEqual(resp.status_code, 401)

    # ── test_06 ──────────────────────────────────────────────────────────────

    def test_06_username_only_payload_rejected(self):
        """Sending username instead of email returns 400 (email field required)."""
        resp = self.api.post(
            TOKEN_URL,
            {'username': 'active.user', 'password': 'Pass@1234'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    # ── test_07 ──────────────────────────────────────────────────────────────

    def test_07_duplicate_email_on_create_rejected(self):
        """Creating a second user with an already-taken email returns 400."""
        self.api.force_authenticate(user=self.active_user)
        payload = {
            'username': 'another.user',
            'email': 'active@logicon.local',
            'user_type': 'internal',
            'org': self.org.pk,
        }
        resp = self.api.post(USERS_URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('email', resp.data)

    # ── test_08 ──────────────────────────────────────────────────────────────

    def test_08_token_grants_access_to_authenticated_endpoint(self):
        """Token obtained via email login allows access to a protected endpoint."""
        login_resp = self._login('active@logicon.local', 'Pass@1234')
        self.assertEqual(login_resp.status_code, 200)

        self.api.credentials(HTTP_AUTHORIZATION=f'Bearer {login_resp.data["access"]}')
        resp = self.api.get(USERS_URL)
        self.assertIn(resp.status_code, [200, 403])

from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.access.capabilities import ASSET_VAULT_ACCESS
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.asset_vault import ASSET_VAULT_SSO_SALT
from apps.core.models import Organization, ScopeNode


class AssetVaultLoginLinkTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Logicon Test', code='logicon-test')
        cls.company_node = ScopeNode.objects.create(
            org=cls.org,
            code='logicon-test',
            name='Logicon Test',
            node_type='company',
            path='logicon-test',
            depth=0,
            is_active=True,
        )
        cls.asset_role = AccessRole.objects.create(
            org=cls.org,
            code='operations_manager',
            name='Operations Manager',
            is_active=True,
        )
        bootstrap_role_permissions(cls.asset_role, [ASSET_VAULT_ACCESS])

        cls.allowed_user = User.objects.create_user(
            username='ops.manager',
            email='ops.manager@example.com',
            password='pass123',
            first_name='Ops',
            last_name='Manager',
            org=cls.org,
            user_type='internal',
        )
        UserRoleAssignment.objects.create(
            user=cls.allowed_user,
            role=cls.asset_role,
            scope_node=cls.company_node,
        )

        cls.denied_user = User.objects.create_user(
            username='no.asset',
            email='no.asset@example.com',
            password='pass123',
            org=cls.org,
            user_type='internal',
        )

    def _login(self, user):
        self.client.force_authenticate(user=user)

    @override_settings(
        ASSET_VAULT_BASE_URL='https://assets.example.com',
        ASSET_VAULT_SSO_SECRET='asset-secret',
        ASSET_VAULT_SSO_CONSUME_PATH='/sso/logicon',
        ASSET_VAULT_SSO_TTL_SECONDS=60,
    )
    def test_creates_signed_asset_vault_login_link(self):
        self._login(self.allowed_user)

        response = self.client.post(reverse('asset_vault_login_link'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['expires_in'], 60)
        parsed = urlparse(response.data['url'])
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', 'https://assets.example.com/sso/logicon')

        token = parse_qs(parsed.query)['token'][0]
        payload = signing.loads(
            token,
            key='asset-secret',
            salt=ASSET_VAULT_SSO_SALT,
            max_age=60,
        )
        self.assertEqual(payload['email'], 'ops.manager@example.com')
        self.assertEqual(payload['name'], 'Ops Manager')
        self.assertEqual(payload['org']['code'], 'logicon-test')
        self.assertEqual(payload['role_codes'], ['operations_manager'])
        self.assertEqual(payload['portal_mode'], 'internal')
        self.assertEqual(payload['nav_persona'], 'operations')

    @override_settings(
        ASSET_VAULT_BASE_URL='https://assets.example.com',
        ASSET_VAULT_SSO_SECRET='asset-secret',
    )
    def test_requires_asset_vault_access_capability(self):
        self._login(self.denied_user)

        response = self.client.post(reverse('asset_vault_login_link'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(ASSET_VAULT_BASE_URL='', ASSET_VAULT_SSO_SECRET='asset-secret')
    def test_missing_asset_vault_base_url_fails_closed(self):
        self._login(self.allowed_user)

        response = self.client.post(reverse('asset_vault_login_link'))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['detail'], 'Asset Vault base URL is not configured.')

    @override_settings(ASSET_VAULT_BASE_URL='https://assets.example.com', ASSET_VAULT_SSO_SECRET='')
    def test_missing_asset_vault_secret_fails_closed(self):
        self._login(self.allowed_user)

        response = self.client.post(reverse('asset_vault_login_link'))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['detail'], 'Asset Vault SSO secret is not configured.')

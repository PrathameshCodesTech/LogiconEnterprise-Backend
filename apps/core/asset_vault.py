from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.capabilities import ASSET_VAULT_ACCESS, get_active_role_codes, get_user_access_profile
from apps.access.permissions import HasCapability


ASSET_VAULT_SSO_SALT = 'asset-vault-sso-v1'


class AssetVaultLoginLinkView(APIView):
    """
    POST /api/integrations/asset-vault/login-link/

    Creates a short-lived signed handoff URL for Asset Vault.
    Logicon remains the identity source; Asset Vault must validate the token,
    map the email/org/roles to its own user permissions, and issue its own
    session/JWT.
    """

    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = ASSET_VAULT_ACCESS

    def post(self, request, *args, **kwargs):
        base_url = getattr(settings, 'ASSET_VAULT_BASE_URL', '').strip().rstrip('/')
        secret = getattr(settings, 'ASSET_VAULT_SSO_SECRET', '').strip()
        consume_path = getattr(settings, 'ASSET_VAULT_SSO_CONSUME_PATH', '/sso/logicon').strip()
        ttl_seconds = int(getattr(settings, 'ASSET_VAULT_SSO_TTL_SECONDS', 60))

        if not base_url:
            return Response(
                {'detail': 'Asset Vault base URL is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not secret:
            return Response(
                {'detail': 'Asset Vault SSO secret is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if ttl_seconds <= 0:
            return Response(
                {'detail': 'Asset Vault SSO TTL must be greater than zero.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        user = request.user
        email = (user.email or '').strip().lower()
        if not email:
            return Response(
                {'detail': 'Your Logicon account must have an email before opening Asset Vault.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.org_id:
            return Response(
                {'detail': 'Your Logicon account must be linked to an organization before opening Asset Vault.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        access_profile = get_user_access_profile(user)
        org = user.org
        full_name = (user.get_full_name() or user.username or email).strip()

        payload = {
            'iss': 'logicon',
            'aud': 'asset_vault',
            'sub': str(user.id),
            'email': email,
            'name': full_name,
            'username': user.username,
            'org': {
                'id': org.id,
                'code': org.code,
                'name': org.name,
            },
            'role_codes': get_active_role_codes(user),
            'portal_mode': access_profile.get('portal_mode'),
            'nav_persona': access_profile.get('nav_persona'),
            'iat': int(now.timestamp()),
            'exp': int(expires_at.timestamp()),
        }
        token = signing.dumps(
            payload,
            key=secret,
            salt=ASSET_VAULT_SSO_SALT,
            compress=True,
        )

        if not consume_path.startswith('/'):
            consume_path = f'/{consume_path}'
        launch_url = f'{base_url}{consume_path}?{urlencode({"token": token})}'

        return Response({
            'url': launch_url,
            'expires_in': ttl_seconds,
            'expires_at': expires_at.isoformat(),
        })

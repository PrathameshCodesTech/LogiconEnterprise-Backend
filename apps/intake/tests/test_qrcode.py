"""
apps/intake/tests/test_qrcode.py

Tests for GET /api/intake/campaigns/<pk>/qrcode/
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.sites.models import Client, SiteProfile
from apps.intake.models import QRCampaign


def _org():
    return Organization.objects.create(name='Test Org QR', code='to-qr')


def _site(org):
    n_co = ScopeNode.objects.create(
        org=org, code='to-qr', name='to-qr', node_type='company',
        parent=None, depth=0, path='to-qr', is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client QR', code='client-qr',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='client-qr', name='client-qr', node_type='client',
        parent=n_co, depth=1, path='to-qr/client-qr', is_active=True,
    )
    return SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site QR', code='site-qr', is_active=True,
    )


class TestCampaignQRCode(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.site = _site(cls.org)
        cls.campaign = QRCampaign.objects.create(
            org=cls.org, site=cls.site,
            name='QR Test Campaign', title='QR Test',
            code='QR-TEST',
            is_active=True,
            default_language='en',
            enabled_languages=['en'],
        )
        cls.user = User.objects.create_user(
            username='qr_tester', password='pass123',
        )
        cls.user.org = cls.org
        cls.user.save()

    def setUp(self):
        self.api = APIClient()

    def test_unauthenticated_returns_401(self):
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_returns_png_or_503(self):
        self.api.force_authenticate(user=self.user)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        # 200 if qrcode is installed, 503 if missing
        self.assertIn(resp.status_code, [200, 503])

    def test_response_has_correct_content_type_when_qrcode_installed(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode package not installed")
        self.api.force_authenticate(user=self.user)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')

    def test_response_has_x_apply_url_header(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode package not installed")
        self.api.force_authenticate(user=self.user)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        self.assertIn('X-Apply-URL', resp)
        self.assertIn(self.campaign.token, resp['X-Apply-URL'])

    def test_nonexistent_campaign_returns_404(self):
        self.api.force_authenticate(user=self.user)
        resp = self.api.get('/api/intake/campaigns/99999/qrcode/')
        self.assertEqual(resp.status_code, 404)

    def test_token_auto_generated_on_campaign_create(self):
        self.assertTrue(len(self.campaign.token) > 0)

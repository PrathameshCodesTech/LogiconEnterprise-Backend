"""
apps/intake/tests/test_public_campaign.py

Tests for GET /api/public/campaigns/<token>/
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile
from apps.intake.models import QRCampaign, CampaignJobRole, FormField


def _make_org():
    return Organization.objects.create(name='Test Org PC', code='to-pc')


def _make_site(org):
    n_co = ScopeNode.objects.create(
        org=org, code='to-pc', name='to-pc', node_type='company',
        parent=None, depth=0, path='to-pc', is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client PC', code='client-pc',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='client-pc', name='client-pc', node_type='client',
        parent=n_co, depth=1, path='to-pc/client-pc', is_active=True,
    )
    return SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site PC', code='site-pc', city='Mumbai', state='MH', is_active=True,
    )


def _make_campaign(org, site, **kwargs):
    defaults = {
        'name': 'Demo Campaign',
        'title': 'Demo Hiring Drive',
        'code': 'DEMO-PC',
        'site': site,
        'is_active': True,
        'allow_duplicates': True,
        'requires_otp': False,
        'shuffle_fields': False,
        'default_language': 'en',
        'enabled_languages': ['en', 'hi'],
    }
    defaults.update(kwargs)
    return QRCampaign.objects.create(org=org, **defaults)


class TestPublicCampaignDetail(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _make_org()
        cls.site = _make_site(cls.org)
        cls.campaign = _make_campaign(cls.org, cls.site)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard PC', code='guard-pc',
        )
        CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.job_role, is_active=True,
        )
        FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Age', field_key='age', field_type='number',
            sort_order=0, is_required=True, is_active=True,
            min_value=18, max_value=60,
            options=[],
            translations={'hi': {'label': 'आयु'}},
        )
        FormField.objects.create(
            campaign=cls.campaign, role=cls.job_role,
            label='Height (cm)', field_key='height', field_type='number',
            sort_order=0, is_required=False, is_active=True,
            min_value=150, max_value=220,
            options=[],
            translations={},
        )

    def setUp(self):
        self.api = APIClient()

    # Happy path

    def test_get_campaign_by_token_returns_200(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_response_contains_title_and_token(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertEqual(resp.data['title'], 'Demo Hiring Drive')
        self.assertEqual(resp.data['token'], self.campaign.token)

    def test_response_contains_roles(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertEqual(len(resp.data['roles']), 1)
        self.assertEqual(resp.data['roles'][0]['name'], 'Guard PC')

    def test_response_contains_common_fields(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertEqual(len(resp.data['common_fields']), 1)
        self.assertEqual(resp.data['common_fields'][0]['field_key'], 'age')

    def test_response_contains_role_fields_keyed_by_role_id(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        role_fields = resp.data['role_fields']
        key = str(self.job_role.id)
        self.assertIn(key, role_fields)
        self.assertEqual(role_fields[key][0]['field_key'], 'height')

    def test_response_contains_languages(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        codes = [lang['code'] for lang in resp.data['languages']]
        self.assertIn('en', codes)
        self.assertIn('hi', codes)

    def test_response_contains_settings(self):
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertIn('settings', resp.data)
        self.assertFalse(resp.data['settings']['requires_otp'])
        self.assertTrue(resp.data['settings']['allow_duplicates'])

    def test_no_auth_required(self):
        # Must work without any token
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        self.assertEqual(resp.status_code, 200)

    # Error cases

    def test_invalid_token_returns_404(self):
        resp = self.api.get('/api/public/campaigns/nonexistent-token-xyz/')
        self.assertEqual(resp.status_code, 404)

    def test_inactive_campaign_returns_404(self):
        inactive = _make_campaign(self.org, self.site, code='DEMO-INACTIVE', is_active=False)
        resp = self.api.get(f'/api/public/campaigns/{inactive.token}/')
        self.assertEqual(resp.status_code, 404)

    def test_future_campaign_returns_404(self):
        future = _make_campaign(
            self.org, self.site,
            code='DEMO-FUTURE',
            starts_at=timezone.now() + timezone.timedelta(hours=1),
        )
        resp = self.api.get(f'/api/public/campaigns/{future.token}/')
        self.assertEqual(resp.status_code, 404)

    def test_expired_campaign_returns_404(self):
        expired = _make_campaign(
            self.org, self.site,
            code='DEMO-EXPIRED',
            ends_at=timezone.now() - timezone.timedelta(hours=1),
        )
        resp = self.api.get(f'/api/public/campaigns/{expired.token}/')
        self.assertEqual(resp.status_code, 404)

    def test_inactive_job_role_not_in_roles_list(self):
        extra_role = JobRole.objects.create(org=self.org, name='Inactive Role PC', code='inactive-pc')
        CampaignJobRole.objects.create(
            campaign=self.campaign, job_role=extra_role, is_active=False,
        )
        resp = self.api.get(f'/api/public/campaigns/{self.campaign.token}/')
        names = [r['name'] for r in resp.data['roles']]
        self.assertNotIn('Inactive Role PC', names)

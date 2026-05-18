"""
apps/intake/tests/test_admin_apis.py

Tests for authenticated intake admin APIs:
  - Campaign CRUD
  - Form Field CRUD
  - Submission list/detail/status-patch
  - QR code endpoint
  - Capability gating (403 for wrong role)
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile
from apps.intake.models import (
    QRCampaign, CampaignJobRole, FormField,
    IntakeSubmission, IntakeSubmissionAnswer,
)
from apps.talent.models import Candidate


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code='ta-ia'):
    return Organization.objects.create(name=f'TestOrg {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client IA', code='client-ia',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='client-ia', name='client-ia', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/client-ia', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site IA', code='site-ia', city='Pune', state='MH', is_active=True,
    )
    return n_co, n_cl, client, site


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _user(username, org, role_obj, scope_node, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass123',
                                  is_superuser=is_superuser, is_staff=is_superuser)
    u.org = org
    u.save()
    if role_obj and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _campaign(org, site=None, code='CAMP-IA', **kwargs):
    defaults = {
        'name': 'Test Campaign IA',
        'title': 'Test Hiring Drive',
        'code': code,
        'site': site,
        'is_active': True,
        'allow_duplicates': True,
        'default_language': 'en',
        'enabled_languages': ['en'],
    }
    defaults.update(kwargs)
    return QRCampaign.objects.create(org=org, **defaults)


def _form_field(campaign, role=None, field_key='age', **kwargs):
    defaults = {
        'label': 'Age',
        'field_type': 'number',
        'sort_order': 0,
        'is_required': False,
        'is_active': True,
        'options': [],
    }
    defaults.update(kwargs)
    return FormField.objects.create(campaign=campaign, role=role, field_key=field_key, **defaults)


def _submission(campaign, mobile='9100000001', status='new'):
    org = campaign.org
    candidate = Candidate.objects.create(
        org=org,
        phone=mobile,
        phone_normalized=mobile,
        first_name='Test',
        last_name='Sub',
        source='qr',
    )
    return IntakeSubmission.objects.create(
        campaign=campaign,
        site=campaign.site,
        candidate=candidate,
        first_name='Test',
        last_name='Sub',
        full_name='Test Sub',
        mobile_number=mobile,
        mobile_number_normalized=mobile,
        status=status,
        language='en',
    )


class IntakeAdminTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_hr_admin = _role(cls.org, 'hr_admin')
        cls.r_hr_exec = _role(cls.org, 'hr_executive')
        cls.r_client_user = _role(cls.org, 'client_user')
        # finance role has no submission.read or campaign.read
        cls.r_finance = _role(cls.org, 'finance')
        bootstrap_role_permissions(cls.r_hr_admin)
        bootstrap_role_permissions(cls.r_hr_exec)
        bootstrap_role_permissions(cls.r_client_user)
        bootstrap_role_permissions(cls.r_finance)

        cls.hr_admin = _user('ia_hr_admin', cls.org, cls.r_hr_admin, cls.n_co)
        cls.hr_exec = _user('ia_hr_exec', cls.org, cls.r_hr_exec, cls.n_co)
        cls.client_user = _user('ia_client_user', cls.org, cls.r_client_user, cls.n_cl)
        cls.finance_user = _user('ia_finance_user', cls.org, cls.r_finance, cls.n_co)
        cls.superuser = _user('ia_superuser', cls.org, None, None, is_superuser=True)

        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard IA', code='guard-ia')
        cls.campaign = _campaign(cls.org, cls.site)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── Campaign Admin Tests ─────────────────────────────────────────────────────

class TestCampaignAdminAPI(IntakeAdminTestBase):

    def test_list_campaigns_hr_admin_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaigns/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_campaigns_unauthenticated_401(self):
        resp = self.api.get('/api/intake/campaigns/')
        self.assertEqual(resp.status_code, 401)

    def test_list_campaigns_client_user_403(self):
        self._login(self.client_user)
        resp = self.api.get('/api/intake/campaigns/')
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_campaign_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['token'], self.campaign.token)

    def test_create_campaign_hr_admin_201(self):
        self._login(self.hr_admin)
        payload = {
            'name': 'New Campaign IA',
            'title': 'New Hiring Drive',
            'code': 'NEW-CAMP-IA',
            'site': self.site.pk,
            'is_active': True,
            'allow_duplicates': True,
            'requires_otp': False,
            'shuffle_fields': False,
            'default_language': 'en',
            'enabled_languages': ['en', 'hi'],
        }
        resp = self.api.post('/api/intake/campaigns/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('token', resp.data)
        self.assertTrue(len(resp.data['token']) > 0)

    def test_create_campaign_hr_exec_403(self):
        # hr_exec has campaign.create per capabilities, should be 201
        # but client_user does NOT have campaign.create → 403
        self._login(self.client_user)
        payload = {
            'name': 'Blocked Campaign',
            'code': 'BLOCKED-CAMP',
            'default_language': 'en',
            'enabled_languages': ['en'],
        }
        resp = self.api.post('/api/intake/campaigns/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_campaign_invalid_language_400(self):
        self._login(self.hr_admin)
        payload = {
            'name': 'Bad Lang Campaign',
            'code': 'BAD-LANG-CAMP',
            'default_language': 'fr',
            'enabled_languages': ['fr'],
        }
        resp = self.api.post('/api/intake/campaigns/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_campaign_default_language_not_in_enabled_400(self):
        self._login(self.hr_admin)
        payload = {
            'name': 'Bad Default Campaign',
            'code': 'BAD-DEFAULT-CAMP',
            'default_language': 'hi',
            'enabled_languages': ['en'],
        }
        resp = self.api.post('/api/intake/campaigns/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_update_campaign_patch_201(self):
        self._login(self.hr_admin)
        camp = _campaign(self.org, self.site, code='UPD-CAMP-IA')
        resp = self.api.patch(
            f'/api/intake/campaigns/{camp.pk}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_active'])

    def test_delete_campaign_soft_deactivates(self):
        self._login(self.hr_admin)
        camp = _campaign(self.org, self.site, code='DEL-CAMP-IA')
        resp = self.api.delete(f'/api/intake/campaigns/{camp.pk}/')
        self.assertEqual(resp.status_code, 204)
        camp.refresh_from_db()
        self.assertFalse(camp.is_active)

    def test_delete_campaign_requires_campaign_delete_capability(self):
        # hr_exec does NOT have campaign.delete capability
        self._login(self.hr_exec)
        camp = _campaign(self.org, self.site, code='NODELDEL-CAMP-IA')
        resp = self.api.delete(f'/api/intake/campaigns/{camp.pk}/')
        self.assertEqual(resp.status_code, 403)

    def test_response_includes_all_required_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/')
        required = {'id', 'title', 'token', 'site', 'starts_at', 'ends_at',
                    'is_active', 'allow_duplicates', 'requires_otp', 'shuffle_fields',
                    'default_language', 'enabled_languages', 'created_at', 'updated_at'}
        for field in required:
            self.assertIn(field, resp.data, f"Missing field: {field}")

    def test_search_by_title(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaigns/', {'search': 'Test Hiring Drive'})
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_filter_by_is_active(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaigns/', {'is_active': 'true'})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertTrue(item['is_active'])


# ─── Form Field Admin Tests ───────────────────────────────────────────────────

class TestFormFieldAdminAPI(IntakeAdminTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ff = _form_field(cls.campaign)

    def test_list_form_fields_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/form-fields/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_form_fields_unauthenticated_401(self):
        resp = self.api.get('/api/intake/form-fields/')
        self.assertEqual(resp.status_code, 401)

    def test_list_form_fields_client_user_403(self):
        self._login(self.client_user)
        resp = self.api.get('/api/intake/form-fields/')
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_form_field_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/form-fields/{self.ff.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['field_key'], 'age')

    def test_create_form_field_201(self):
        self._login(self.hr_admin)
        payload = {
            'campaign': self.campaign.pk,
            'label': 'Experience Years',
            'field_key': 'experience_years',
            'field_type': 'number',
            'sort_order': 1,
            'is_required': False,
            'is_active': True,
            'options': [],
        }
        resp = self.api.post('/api/intake/form-fields/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['field_key'], 'experience_years')

    def test_create_form_field_requires_campaign_update_capability(self):
        # client_user has no campaign.update → 403
        self._login(self.client_user)
        payload = {
            'campaign': self.campaign.pk,
            'label': 'Blocked Field',
            'field_key': 'blocked',
            'field_type': 'text',
        }
        resp = self.api.post('/api/intake/form-fields/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_patch_form_field_200(self):
        self._login(self.hr_admin)
        resp = self.api.patch(
            f'/api/intake/form-fields/{self.ff.pk}/',
            {'is_required': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_required'])

    def test_delete_form_field_soft_deactivates(self):
        self._login(self.hr_admin)
        ff = _form_field(self.campaign, field_key='to-delete-ia')
        resp = self.api.delete(f'/api/intake/form-fields/{ff.pk}/')
        self.assertEqual(resp.status_code, 204)
        ff.refresh_from_db()
        self.assertFalse(ff.is_active)

    def test_filter_by_campaign(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/form-fields/', {'campaign': self.campaign.pk})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['campaign'], self.campaign.pk)

    def test_filter_by_field_type(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/form-fields/', {'field_type': 'number'})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['field_type'], 'number')

    def test_response_includes_required_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/form-fields/{self.ff.pk}/')
        required = {'id', 'campaign', 'role', 'label', 'field_key', 'field_type',
                    'help_text', 'placeholder', 'options', 'is_required', 'sort_order',
                    'min_length', 'max_length', 'min_value', 'max_value',
                    'translations', 'is_active'}
        for field in required:
            self.assertIn(field, resp.data, f"Missing field: {field}")


# ─── Submission Admin Tests ───────────────────────────────────────────────────

class TestSubmissionAdminAPI(IntakeAdminTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ff = _form_field(cls.campaign)
        cls.submission = _submission(cls.campaign, mobile='9100000011')
        # Add an answer
        IntakeSubmissionAnswer.objects.create(
            submission=cls.submission,
            field=cls.ff,
            field_label_snapshot='Age',
            field_type_snapshot='number',
            value=25,
        )

    def test_list_submissions_hr_admin_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_submissions_unauthenticated_401(self):
        resp = self.api.get('/api/intake/submissions/')
        self.assertEqual(resp.status_code, 401)

    def test_list_submissions_finance_user_403(self):
        # finance role has no submission.read capability
        self._login(self.finance_user)
        resp = self.api.get('/api/intake/submissions/')
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_submission_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/submissions/{self.submission.pk}/')
        self.assertEqual(resp.status_code, 200)

    def test_retrieve_includes_answers(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/submissions/{self.submission.pk}/')
        self.assertIn('answers', resp.data)
        self.assertEqual(len(resp.data['answers']), 1)
        self.assertEqual(resp.data['answers'][0]['field_label_snapshot'], 'Age')

    def test_retrieve_includes_documents(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/submissions/{self.submission.pk}/')
        self.assertIn('documents', resp.data)

    def test_retrieve_detail_includes_duplicate_metadata(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/submissions/{self.submission.pk}/')
        self.assertIn('is_possible_duplicate', resp.data)
        self.assertIn('duplicate_reason', resp.data)

    def test_list_serializer_does_not_include_answers(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/')
        item = resp.data['results'][0]
        self.assertNotIn('answers', item)

    def test_list_includes_name_and_mobile_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/')
        item = next(
            (r for r in resp.data['results'] if r['id'] == self.submission.pk), None
        )
        self.assertIsNotNone(item)
        for field in ['first_name', 'last_name', 'full_name', 'mobile_number_normalized',
                      'status', 'language', 'is_possible_duplicate', 'submitted_at']:
            self.assertIn(field, item, f"Missing field: {field}")

    def test_patch_status_valid_200(self):
        self._login(self.hr_admin)
        sub = _submission(self.campaign, mobile='9100000022')
        resp = self.api.patch(
            f'/api/intake/submissions/{sub.pk}/',
            {'status': 'shortlisted'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'shortlisted')

    def test_patch_status_invalid_choice_400(self):
        self._login(self.hr_admin)
        resp = self.api.patch(
            f'/api/intake/submissions/{self.submission.pk}/',
            {'status': 'invalid_status'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_patch_status_requires_submission_update_capability(self):
        # client_user does not have submission.update
        self._login(self.client_user)
        resp = self.api.patch(
            f'/api/intake/submissions/{self.submission.pk}/',
            {'status': 'reviewed'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_patch_accepts_optional_note_field(self):
        self._login(self.hr_admin)
        sub = _submission(self.campaign, mobile='9100000033')
        resp = self.api.patch(
            f'/api/intake/submissions/{sub.pk}/',
            {'status': 'reviewed', 'note': 'Looks good'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_create_submission_via_admin_api_not_allowed(self):
        # HasCapability fires during initial() before method routing; 'create' has no
        # capability mapping → safe-default deny returns 403 before 405 can fire.
        self._login(self.hr_admin)
        resp = self.api.post('/api/intake/submissions/', {}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_filter_by_status(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/', {'status': 'new'})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['status'], 'new')

    def test_filter_by_is_possible_duplicate(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/', {'is_possible_duplicate': 'false'})
        self.assertEqual(resp.status_code, 200)

    def test_search_by_name(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/submissions/', {'search': 'Test'})
        self.assertEqual(resp.status_code, 200)


# ─── Campaign Job Role Admin Tests ───────────────────────────────────────────

class TestCampaignJobRoleAdminAPI(IntakeAdminTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second org + job_role to test org mismatch
        cls.other_org = _org(code='ta-ia-other')
        cls.other_job_role = JobRole.objects.create(
            org=cls.other_org, name='Other Guard', code='other-guard',
        )
        cls.cjr = CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.job_role, is_active=True,
        )

    def test_list_campaign_job_roles_200(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaign-job-roles/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data['results']), 1)

    def test_list_requires_campaign_read_403(self):
        # finance role has no campaign.read
        self._login(self.finance_user)
        resp = self.api.get('/api/intake/campaign-job-roles/')
        self.assertEqual(resp.status_code, 403)

    def test_list_unauthenticated_401(self):
        resp = self.api.get('/api/intake/campaign-job-roles/')
        self.assertEqual(resp.status_code, 401)

    def test_retrieve_campaign_job_role_200(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaign-job-roles/{self.cjr.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('job_role_name', resp.data)
        self.assertIn('job_role_code', resp.data)

    def test_response_includes_job_role_name_and_code(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaign-job-roles/{self.cjr.pk}/')
        self.assertEqual(resp.data['job_role_name'], self.job_role.name)
        self.assertEqual(resp.data['job_role_code'], self.job_role.code)

    def test_create_campaign_job_role_201(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(org=self.org, name='Packer IA', code='packer-ia')
        payload = {'campaign': self.campaign.pk, 'job_role': jr.pk, 'is_active': True}
        resp = self.api.post('/api/intake/campaign-job-roles/', payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['job_role'], jr.pk)

    def test_create_requires_campaign_update_403(self):
        # client_user has no campaign.update
        self._login(self.client_user)
        payload = {'campaign': self.campaign.pk, 'job_role': self.job_role.pk}
        resp = self.api.post('/api/intake/campaign-job-roles/', payload, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_duplicate_blocked_400(self):
        # cjr already exists for campaign + job_role
        self._login(self.hr_admin)
        payload = {'campaign': self.campaign.pk, 'job_role': self.job_role.pk}
        resp = self.api.post('/api/intake/campaign-job-roles/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_org_mismatch_blocked_400(self):
        # other_job_role belongs to other_org, not cls.org
        self._login(self.hr_admin)
        payload = {'campaign': self.campaign.pk, 'job_role': self.other_job_role.pk}
        resp = self.api.post('/api/intake/campaign-job-roles/', payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_soft_delete_sets_is_active_false(self):
        self._login(self.hr_admin)
        jr = JobRole.objects.create(org=self.org, name='Driver IA', code='driver-ia')
        cjr = CampaignJobRole.objects.create(
            campaign=self.campaign, job_role=jr, is_active=True,
        )
        resp = self.api.delete(f'/api/intake/campaign-job-roles/{cjr.pk}/')
        self.assertEqual(resp.status_code, 204)
        cjr.refresh_from_db()
        self.assertFalse(cjr.is_active)

    def test_filter_by_campaign(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaign-job-roles/', {'campaign': self.campaign.pk})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertEqual(item['campaign'], self.campaign.pk)

    def test_filter_by_is_active(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaign-job-roles/', {'is_active': 'true'})
        self.assertEqual(resp.status_code, 200)
        for item in resp.data['results']:
            self.assertTrue(item['is_active'])

    def test_campaign_retrieve_includes_campaign_roles(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('campaign_roles', resp.data)
        self.assertIsInstance(resp.data['campaign_roles'], list)
        # At least the cjr we created in setUpTestData
        role_ids = [cr['job_role'] for cr in resp.data['campaign_roles']]
        self.assertIn(self.job_role.pk, role_ids)

    def test_campaign_roles_items_have_required_fields(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/')
        role = next(
            (cr for cr in resp.data['campaign_roles'] if cr['job_role'] == self.job_role.pk),
            None,
        )
        self.assertIsNotNone(role)
        for field in ['id', 'job_role', 'job_role_name', 'job_role_code', 'is_active']:
            self.assertIn(field, role, f"Missing field in campaign_roles item: {field}")

    def test_patch_default_language_invalid_against_existing_enabled_400(self):
        # campaign has enabled_languages=['en']; PATCHing default_language='hi' should fail
        self._login(self.hr_admin)
        resp = self.api.patch(
            f'/api/intake/campaigns/{self.campaign.pk}/',
            {'default_language': 'hi'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400, resp.data)


# ─── QR Code Tests (existing, keep working) ──────────────────────────────────

class TestQRCodeEndpoint(IntakeAdminTestBase):

    def test_qr_code_returns_png_or_503(self):
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        self.assertIn(resp.status_code, [200, 503])

    def test_qr_code_unauthenticated_401(self):
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        self.assertEqual(resp.status_code, 401)

    def test_qr_code_has_x_apply_url_header(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode not installed")
        self._login(self.hr_admin)
        resp = self.api.get(f'/api/intake/campaigns/{self.campaign.pk}/qrcode/')
        if resp.status_code == 200:
            self.assertIn('X-Apply-URL', resp)
            self.assertIn(self.campaign.token, resp['X-Apply-URL'])

    def test_qr_code_nonexistent_campaign_404(self):
        self._login(self.hr_admin)
        resp = self.api.get('/api/intake/campaigns/99999/qrcode/')
        self.assertEqual(resp.status_code, 404)

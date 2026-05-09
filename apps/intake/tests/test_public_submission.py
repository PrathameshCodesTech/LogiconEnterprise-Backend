"""
apps/intake/tests/test_public_submission.py

Tests for POST /api/public/submissions/
"""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile
from apps.intake.models import (
    QRCampaign, CampaignJobRole, FormField, IntakeSubmission, IntakeDocument,
)


def _org():
    return Organization.objects.create(name='Test Org Sub', code='to-sub')


def _site(org):
    n_co = ScopeNode.objects.create(
        org=org, code='to-sub', name='to-sub', node_type='company',
        parent=None, depth=0, path='to-sub', is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client Sub', code='client-sub',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='client-sub', name='client-sub', node_type='client',
        parent=n_co, depth=1, path='to-sub/client-sub', is_active=True,
    )
    return SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site Sub', code='site-sub', is_active=True,
    )


class TestPublicSubmission(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.site = _site(cls.org)

        cls.campaign = QRCampaign.objects.create(
            org=cls.org,
            site=cls.site,
            name='Sub Test Campaign',
            title='Sub Test Hiring Drive',
            code='SUB-CAMP',
            is_active=True,
            allow_duplicates=True,
            requires_otp=False,
            shuffle_fields=False,
            default_language='en',
            enabled_languages=['en', 'hi'],
        )

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard Sub', code='guard-sub',
        )
        CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.job_role, is_active=True,
        )

        # Common fields
        cls.ff_age = FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Age', field_key='age', field_type='number',
            sort_order=0, is_required=True, is_active=True,
            min_value=18, max_value=60, options=[],
        )
        FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Gender', field_key='gender', field_type='select',
            sort_order=1, is_required=False, is_active=True,
            options=['Male', 'Female', 'Other'],
        )
        FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Experience Years', field_key='experience_years', field_type='number',
            sort_order=2, is_required=False, is_active=True,
            min_value=0, max_value=40, options=[],
        )
        FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Expected Salary', field_key='expected_salary', field_type='number',
            sort_order=3, is_required=False, is_active=True,
            options=[],
        )
        FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Joining Availability', field_key='joining_availability', field_type='date',
            sort_order=4, is_required=False, is_active=True,
            options=[],
        )

    def setUp(self):
        self.api = APIClient()
        self.valid_payload = {
            'campaign_token': self.campaign.token,
            'job_role_id': self.job_role.id,
            'first_name': 'Rahul',
            'last_name': 'Sharma',
            'mobile_number': '9876543210',
            'language': 'en',
            'answers': [
                {'field_id': self.ff_age.id, 'value': 25},
            ],
        }

    def _post(self, payload):
        return self.api.post('/api/public/submissions/', payload, format='json')

    # Happy path

    def test_valid_submission_returns_201(self):
        resp = self._post(self.valid_payload)
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_submission_creates_intake_submission_record(self):
        self._post(self.valid_payload)
        self.assertTrue(
            IntakeSubmission.objects.filter(
                campaign=self.campaign,
                mobile_number_normalized='9876543210',
            ).exists()
        )

    def test_submission_creates_candidate_in_talent(self):
        from apps.talent.models import Candidate
        self._post(self.valid_payload)
        self.assertTrue(
            Candidate.objects.filter(
                org=self.org, phone_normalized='9876543210',
            ).exists()
        )

    def test_submission_stores_first_last_name(self):
        self._post(self.valid_payload)
        sub = IntakeSubmission.objects.get(mobile_number_normalized='9876543210')
        self.assertEqual(sub.first_name, 'Rahul')
        self.assertEqual(sub.last_name, 'Sharma')

    def test_submission_with_other_role_title(self):
        payload = {
            'campaign_token': self.campaign.token,
            'first_name': 'Priya',
            'last_name': 'Patil',
            'mobile_number': '8765432109',
            'other_role_title': 'Welder',
            'answers': [{'field_id': self.ff_age.id, 'value': 28}],
        }
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201, resp.data)
        sub = IntakeSubmission.objects.get(mobile_number_normalized='8765432109')
        self.assertEqual(sub.other_role_title, 'Welder')

    def test_duplicate_within_24h_is_flagged(self):
        self._post(self.valid_payload)
        # Second submission for same mobile + role
        resp2 = self._post(self.valid_payload)
        # With allow_duplicates=True it succeeds but is flagged
        self.assertEqual(resp2.status_code, 201)
        self.assertTrue(
            IntakeSubmission.objects.filter(
                mobile_number_normalized='9876543210',
                is_possible_duplicate=True,
            ).exists()
        )

    def test_no_auth_required(self):
        resp = self._post(self.valid_payload)
        self.assertIn(resp.status_code, [201, 400])

    def test_response_contains_submission_id(self):
        resp = self._post(self.valid_payload)
        self.assertIn('id', resp.data)

    def test_required_resume_file_missing_rejected_without_creating_submission(self):
        FormField.objects.create(
            campaign=self.campaign, role=None,
            label='Resume', field_key='resume', field_type='file',
            sort_order=5, is_required=True, is_active=True,
            options=[],
        )
        before = IntakeSubmission.objects.count()

        resp = self._post(self.valid_payload)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(IntakeSubmission.objects.count(), before)
        self.assertIn('resume', str(resp.data).lower())

    def test_required_resume_file_upload_creates_document(self):
        resume_field = FormField.objects.create(
            campaign=self.campaign, role=None,
            label='Resume', field_key='resume', field_type='file',
            sort_order=5, is_required=True, is_active=True,
            options=[],
        )
        upload = SimpleUploadedFile(
            'resume.pdf',
            b'%PDF-1.4\nresume\n',
            content_type='application/pdf',
        )
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9000000003'
        payload['answers'] = json.dumps(payload['answers'])
        payload['resume'] = upload

        resp = self.api.post('/api/public/submissions/', payload, format='multipart')

        self.assertEqual(resp.status_code, 201, resp.data)
        document = IntakeDocument.objects.get(submission_id=resp.data['id'])
        self.assertEqual(document.field, resume_field)
        self.assertEqual(document.document_type, 'resume')

    # Validation: name

    def test_first_name_too_short_rejected(self):
        payload = dict(self.valid_payload)
        payload['first_name'] = 'A'
        payload['mobile_number'] = '9000000001'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_name_with_digits_rejected(self):
        payload = dict(self.valid_payload)
        payload['first_name'] = 'R4hul'
        payload['mobile_number'] = '9000000002'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # Validation: mobile

    def test_invalid_mobile_rejected(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '12345'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_mobile_starting_with_0_rejected(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '0123456789'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_plus91_prefix_is_stripped(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '+919111111111'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(
            IntakeSubmission.objects.filter(mobile_number_normalized='9111111111').exists()
        )

    # Validation: role

    def test_no_role_and_no_other_title_rejected(self):
        payload = {
            'campaign_token': self.campaign.token,
            'first_name': 'Test',
            'last_name': 'User',
            'mobile_number': '9222222222',
            'answers': [{'field_id': self.ff_age.id, 'value': 22}],
        }
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_campaign_token_rejected(self):
        payload = dict(self.valid_payload)
        payload['campaign_token'] = 'bad-token-xyz'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_language_rejected(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9333333333'
        payload['language'] = 'fr'
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # Business rules

    def test_age_below_18_rejected(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9444444444'
        payload['answers'] = [{'field_id': self.ff_age.id, 'value': 15}]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('18', str(resp.data))

    def test_age_above_60_rejected(self):
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9555555555'
        payload['answers'] = [{'field_id': self.ff_age.id, 'value': 65}]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_experience_exceeds_age_minus_14_rejected(self):
        exp_field = FormField.objects.get(campaign=self.campaign, field_key='experience_years')
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9666666666'
        payload['answers'] = [
            {'field_id': self.ff_age.id, 'value': 20},
            {'field_id': exp_field.id, 'value': 10},
        ]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_salary_over_500000_rejected(self):
        salary_field = FormField.objects.get(campaign=self.campaign, field_key='expected_salary')
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9777777777'
        payload['answers'] = [
            {'field_id': self.ff_age.id, 'value': 25},
            {'field_id': salary_field.id, 'value': 600000},
        ]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_past_joining_date_rejected(self):
        date_field = FormField.objects.get(campaign=self.campaign, field_key='joining_availability')
        payload = dict(self.valid_payload)
        payload['mobile_number'] = '9888888888'
        payload['answers'] = [
            {'field_id': self.ff_age.id, 'value': 25},
            {'field_id': date_field.id, 'value': '2020-01-01'},
        ]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # Duplicate blocking

    def test_duplicate_blocked_when_allow_duplicates_false(self):
        strict_campaign = QRCampaign.objects.create(
            org=self.org, site=self.site,
            name='Strict Campaign', title='Strict',
            code='STRICT-SUB',
            is_active=True, allow_duplicates=False,
            default_language='en', enabled_languages=['en'],
        )
        CampaignJobRole.objects.create(
            campaign=strict_campaign, job_role=self.job_role, is_active=True,
        )
        FormField.objects.create(
            campaign=strict_campaign, role=None,
            label='Age', field_key='age', field_type='number',
            sort_order=0, is_required=True, is_active=True,
            min_value=18, max_value=60, options=[],
        )
        payload1 = {
            'campaign_token': strict_campaign.token,
            'job_role_id': self.job_role.id,
            'first_name': 'Dup',
            'last_name': 'Tester',
            'mobile_number': '9000000099',
            'answers': [{'field_id': FormField.objects.get(campaign=strict_campaign, field_key='age').id, 'value': 25}],
        }
        resp1 = self._post(payload1)
        self.assertEqual(resp1.status_code, 201)

        resp2 = self._post(payload1)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('already submitted', str(resp2.data).lower())

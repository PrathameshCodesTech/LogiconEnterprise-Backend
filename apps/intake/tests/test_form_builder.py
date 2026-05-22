"""
apps/intake/tests/test_form_builder.py

Tests for Phase Form-Builder-Backend-A:
  - FormTemplate / FormSection / FormTemplateField models
  - PublicCampaignSerializer dispatches on form_template presence
  - Submission via template fields (template_field_id in answers)
  - File upload via template field prefix (template_field_<id>)
  - Required template field validation
  - Backward compat: campaigns without form_template unchanged
  - CRUD endpoints: form-templates, form-sections, template-fields
"""

import json

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile
from apps.intake.models import (
    QRCampaign, CampaignJobRole, FormField,
    FormTemplate, FormSection, FormTemplateField,
    IntakeSubmission, IntakeDocument, IntakeSubmissionAnswer,
)


# ── Shared helpers ──────────────────────────────────────────────────────────────

def _org(name='FBOrg', code='fb-org'):
    return Organization.objects.create(name=name, code=code)


def _site(org):
    n_co = ScopeNode.objects.create(
        org=org, code='fb-co', name='fb-co', node_type='company',
        parent=None, depth=0, path='fb-co', is_active=True,
    )
    client = Client.objects.create(
        org=org, name='FB Client', code='fb-client',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code='fb-cl', name='fb-cl', node_type='client',
        parent=n_co, depth=1, path='fb-co/fb-cl', is_active=True,
    )
    return SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='FB Site', code='fb-site', is_active=True,
    )


def _campaign(org, site=None, **kwargs):
    defaults = dict(
        name='FB Campaign',
        title='Form Builder Campaign',
        code='FB-CAMP',
        is_active=True,
        allow_duplicates=True,
        requires_otp=False,
        shuffle_fields=False,
        default_language='en',
        enabled_languages=['en'],
    )
    defaults.update(kwargs)
    return QRCampaign.objects.create(org=org, site=site, **defaults)


def _template(org, name='Test Template', code=None):
    return FormTemplate.objects.create(org=org, name=name, code=code, is_active=True)


def _section(template, name='Section A', code='', sort_order=0):
    return FormSection.objects.create(template=template, name=name, code=code, sort_order=sort_order)


def _tmpl_field(section, label, key, ftype='text', order=0, is_required=False, **kwargs):
    defaults = dict(
        label=label,
        field_key=key,
        field_type=ftype,
        sort_order=order,
        is_required=is_required,
        is_active=True,
        options=kwargs.pop('options', []),
        min_value=kwargs.pop('min_value', None),
        max_value=kwargs.pop('max_value', None),
    )
    defaults.update(kwargs)
    return FormTemplateField.objects.create(section=section, **defaults)


PUBLIC_CAMPAIGN_URL = '/api/public/campaigns/{token}/'
PUBLIC_SUBMISSION_URL = '/api/public/submissions/'


def _post(data, files=None):
    client = APIClient()
    if files:
        return client.post(PUBLIC_SUBMISSION_URL, {**data, **files}, format='multipart')
    return client.post(PUBLIC_SUBMISSION_URL, data, format='json')


def _get_campaign(token):
    return APIClient().get(PUBLIC_CAMPAIGN_URL.format(token=token))


# ── Model tests ─────────────────────────────────────────────────────────────────

class TestFormTemplateModels(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()

    def test_form_template_str(self):
        t = _template(self.org, 'My Template')
        self.assertEqual(str(t), 'My Template')

    def test_form_section_str(self):
        t = _template(self.org)
        s = _section(t, 'Section X')
        self.assertIn('Section X', str(s))
        self.assertIn(str(t), str(s))

    def test_form_template_field_str(self):
        t = _template(self.org)
        s = _section(t)
        f = _tmpl_field(s, 'Age', 'age', 'number')
        self.assertIn('Age', str(f))
        self.assertIn(t.name, str(f))

    def test_template_belongs_to_org(self):
        t = _template(self.org)
        self.assertEqual(t.org, self.org)

    def test_section_cascade_on_template_delete(self):
        t = _template(self.org, name='DelTemplate')
        s = _section(t)
        tmpl_id = t.id
        t.delete()
        self.assertFalse(FormSection.objects.filter(template_id=tmpl_id).exists())

    def test_template_field_cascade_on_section_delete(self):
        t = _template(self.org)
        s = _section(t)
        f = _tmpl_field(s, 'Test', 'test')
        s.delete()
        self.assertFalse(FormTemplateField.objects.filter(pk=f.pk).exists())

    def test_qrcampaign_form_template_nullable(self):
        c = _campaign(self.org)
        self.assertIsNone(c.form_template)

    def test_qrcampaign_form_template_can_be_set(self):
        t = _template(self.org)
        c = _campaign(self.org, code='FB2')
        c.form_template = t
        c.save(update_fields=['form_template'])
        c.refresh_from_db()
        self.assertEqual(c.form_template_id, t.id)


# ── Public campaign API: field dispatch ─────────────────────────────────────────

class TestPublicCampaignTemplateDispatch(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='DispatchOrg', code='dp-org')
        cls.site = _site(cls.org)
        cls.role = JobRole.objects.create(org=cls.org, name='Guard', code='GUARD')

        # Campaign WITHOUT template — uses form_fields
        cls.c_no_tmpl = _campaign(cls.org, cls.site, code='NO-TMPL')
        CampaignJobRole.objects.create(
            campaign=cls.c_no_tmpl, job_role=cls.role, is_active=True,
        )
        cls.ff_age = FormField.objects.create(
            campaign=cls.c_no_tmpl, role=None,
            label='Age', field_key='age', field_type='number',
            sort_order=0, is_required=True, is_active=True,
        )
        cls.ff_role_hgt = FormField.objects.create(
            campaign=cls.c_no_tmpl, role=cls.role,
            label='Height', field_key='height', field_type='number',
            sort_order=0, is_required=False, is_active=True,
        )

        # Campaign WITH template
        cls.template = _template(cls.org, 'Dispatch Template')
        cls.sec_gen = _section(cls.template, 'General', 0)
        cls.sec_doc = _section(cls.template, 'Documents', 1)
        cls.tf_age = _tmpl_field(cls.sec_gen, 'Age', 'age', 'number', 0, is_required=True,
                                  min_value=18, max_value=60)
        cls.tf_gender = _tmpl_field(cls.sec_gen, 'Gender', 'gender', 'select', 1,
                                     options=['Male', 'Female'])
        cls.tf_resume = _tmpl_field(cls.sec_doc, 'Resume', 'resume', 'file', 0, is_required=True)
        cls.tf_role_hgt = _tmpl_field(cls.sec_gen, 'Height', 'height', 'number', 2,
                                       role=cls.role)

        cls.c_with_tmpl = _campaign(cls.org, cls.site, code='WITH-TMPL')
        cls.c_with_tmpl.form_template = cls.template
        cls.c_with_tmpl.save(update_fields=['form_template'])
        CampaignJobRole.objects.create(
            campaign=cls.c_with_tmpl, job_role=cls.role, is_active=True,
        )

    def test_no_template_returns_campaign_fields(self):
        resp = _get_campaign(self.c_no_tmpl.token)
        self.assertEqual(resp.status_code, 200)
        fields = resp.data['common_fields']
        keys = [f['field_key'] for f in fields]
        self.assertIn('age', keys)

    def test_no_template_common_fields_have_source_campaign(self):
        resp = _get_campaign(self.c_no_tmpl.token)
        fields = resp.data['common_fields']
        self.assertTrue(all(f['field_source'] == 'campaign' for f in fields))

    def test_template_returns_template_fields(self):
        resp = _get_campaign(self.c_with_tmpl.token)
        self.assertEqual(resp.status_code, 200)
        fields = resp.data['common_fields']
        self.assertGreater(len(fields), 0)
        field_keys = [f['field_key'] for f in fields]
        self.assertIn('age', field_keys)
        self.assertIn('gender', field_keys)
        # resume is a file field — not in common_fields but should appear
        self.assertNotIn('height', field_keys)  # height is role-specific

    def test_template_fields_have_source_template(self):
        resp = _get_campaign(self.c_with_tmpl.token)
        fields = resp.data['common_fields']
        self.assertTrue(all(f['field_source'] == 'template' for f in fields))

    def test_template_role_fields_keyed_by_role_id(self):
        resp = _get_campaign(self.c_with_tmpl.token)
        role_fields = resp.data['role_fields']
        self.assertIn(str(self.role.id), role_fields)
        keys = [f['field_key'] for f in role_fields[str(self.role.id)]]
        self.assertIn('height', keys)

    def test_no_template_role_fields_keyed_by_role_id(self):
        resp = _get_campaign(self.c_no_tmpl.token)
        role_fields = resp.data['role_fields']
        self.assertIn(str(self.role.id), role_fields)

    def test_template_role_field_not_in_common_fields(self):
        # height is a role-specific template field — must not appear in common_fields
        resp = _get_campaign(self.c_with_tmpl.token)
        all_keys = [f['field_key'] for f in resp.data['common_fields']]
        self.assertNotIn('height', all_keys)

    def test_template_file_fields_returned_in_common_fields(self):
        resp = _get_campaign(self.c_with_tmpl.token)
        all_keys = [f['field_key'] for f in resp.data['common_fields']]
        self.assertIn('resume', all_keys)


# ── Public submission with template fields ──────────────────────────────────────

class TestSubmissionWithTemplateFields(TestCase):

    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='TmplSubmitOrg', code='ts-org')
        cls.site = _site(cls.org)
        cls.role = JobRole.objects.create(org=cls.org, name='Worker', code='WORKER')

        cls.template = _template(cls.org, 'Submit Template')
        cls.sec = _section(cls.template, 'Info', 0)
        cls.tf_age = _tmpl_field(
            cls.sec, 'Age', 'age', 'number', 0, is_required=True,
            min_value=18, max_value=60,
        )
        cls.tf_gender = _tmpl_field(
            cls.sec, 'Gender', 'gender', 'select', 1,
            options=['Male', 'Female', 'Other'],
        )

        cls.doc_sec = _section(cls.template, 'Documents', 1)
        cls.tf_resume = _tmpl_field(cls.doc_sec, 'Resume', 'resume', 'file', 0, is_required=True)

        cls.campaign = _campaign(cls.org, cls.site, code='TMPL-SUB')
        cls.campaign.form_template = cls.template
        cls.campaign.save(update_fields=['form_template'])
        CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.role, is_active=True,
        )

    def _base_payload(self):
        return {
            'campaign_token': self.campaign.token,
            'job_role_id': self.role.id,
            'first_name': 'Ravi',
            'last_name': 'Patil',
            'mobile_number': '9876543210',
        }

    def test_submission_with_template_field_id_succeeds(self):
        payload = self._base_payload()
        payload['answers'] = json.dumps([
            {'template_field_id': self.tf_age.id, 'value': 25},
        ])
        resume = SimpleUploadedFile('resume.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {'resume': resume})
        self.assertEqual(resp.status_code, 201)

    def test_submission_creates_answer_with_template_field_source(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9876543211'
        payload['answers'] = json.dumps([
            {'template_field_id': self.tf_age.id, 'value': 30},
        ])
        resume = SimpleUploadedFile('r.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {'resume': resume})
        self.assertEqual(resp.status_code, 201)
        sub = IntakeSubmission.objects.get(id=resp.data['id'])
        ans = sub.answers.filter(template_field=self.tf_age).first()
        self.assertIsNotNone(ans)
        self.assertEqual(ans.field_source, 'template')
        self.assertEqual(ans.field_label_snapshot, 'Age')

    def test_required_template_field_missing_returns_400(self):
        # Age is required — submit without it
        payload = self._base_payload()
        payload['mobile_number'] = '9876543212'
        payload['answers'] = json.dumps([])
        resume = SimpleUploadedFile('r.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {'resume': resume})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_template_field_id_returns_400(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9876543213'
        payload['answers'] = json.dumps([
            {'template_field_id': 99999, 'value': 25},
        ])
        resume = SimpleUploadedFile('r.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {'resume': resume})
        self.assertEqual(resp.status_code, 400)

    def test_required_file_via_template_field_prefix(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9876543214'
        payload['answers'] = json.dumps([
            {'template_field_id': self.tf_age.id, 'value': 25},
        ])
        resume = SimpleUploadedFile('r.pdf', b'%PDF-1', content_type='application/pdf')
        # Use template_field_<id> as file key
        resp = _post(payload, {f'template_field_{self.tf_resume.id}': resume})
        self.assertEqual(resp.status_code, 201)
        sub = IntakeSubmission.objects.get(id=resp.data['id'])
        doc = sub.documents.first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.template_field_id, self.tf_resume.id)
        self.assertEqual(doc.field_source, 'template')

    def test_required_file_missing_for_template_campaign_returns_400(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9876543215'
        payload['answers'] = json.dumps([
            {'template_field_id': self.tf_age.id, 'value': 25},
        ])
        # No file uploaded
        resp = _post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_select_option_for_template_field_returns_400(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9876543216'
        payload['answers'] = json.dumps([
            {'template_field_id': self.tf_age.id, 'value': 25},
            {'template_field_id': self.tf_gender.id, 'value': 'InvalidGender'},
        ])
        resume = SimpleUploadedFile('r.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {'resume': resume})
        self.assertEqual(resp.status_code, 400)


# ── Backward compat: campaign without form_template ─────────────────────────────

class TestBackwardCompatNoCampaignTemplate(TestCase):

    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='BwdOrg', code='bwd-org')
        cls.site = _site(cls.org)
        cls.role = JobRole.objects.create(org=cls.org, name='Driver', code='DRIVER')

        cls.campaign = _campaign(cls.org, cls.site, code='BWD-CAMP')
        CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.role, is_active=True,
        )
        cls.ff_age = FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Age', field_key='age', field_type='number',
            sort_order=0, is_required=True, is_active=True,
            min_value=18, max_value=60,
        )
        cls.ff_resume = FormField.objects.create(
            campaign=cls.campaign, role=None,
            label='Resume', field_key='resume', field_type='file',
            sort_order=1, is_required=True, is_active=True,
        )

    def _base_payload(self):
        return {
            'campaign_token': self.campaign.token,
            'job_role_id': self.role.id,
            'first_name': 'Suresh',
            'last_name': 'Kumar',
            'mobile_number': '9123456780',
        }

    def test_campaign_without_template_uses_form_fields(self):
        resp = _get_campaign(self.campaign.token)
        self.assertEqual(resp.status_code, 200)
        fields = resp.data['common_fields']
        keys = [f['field_key'] for f in fields]
        self.assertIn('age', keys)
        self.assertTrue(all(f['field_source'] == 'campaign' for f in fields))

    def test_old_style_field_id_submission_still_works(self):
        payload = self._base_payload()
        payload['answers'] = json.dumps([
            {'field_id': self.ff_age.id, 'value': 28},
        ])
        resume = SimpleUploadedFile('cv.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {f'field_{self.ff_resume.id}': resume})
        self.assertEqual(resp.status_code, 201)
        sub = IntakeSubmission.objects.get(id=resp.data['id'])
        ans = sub.answers.first()
        self.assertEqual(ans.field_source, 'campaign')
        self.assertEqual(ans.field_id, self.ff_age.id)

    def test_document_field_source_is_campaign(self):
        payload = self._base_payload()
        payload['mobile_number'] = '9123456781'
        payload['answers'] = json.dumps([
            {'field_id': self.ff_age.id, 'value': 28},
        ])
        resume = SimpleUploadedFile('cv.pdf', b'%PDF-1', content_type='application/pdf')
        resp = _post(payload, {f'field_{self.ff_resume.id}': resume})
        self.assertEqual(resp.status_code, 201)
        sub = IntakeSubmission.objects.get(id=resp.data['id'])
        doc = sub.documents.first()
        self.assertEqual(doc.field_source, 'campaign')
        self.assertEqual(doc.field_id, self.ff_resume.id)
        self.assertIsNone(doc.template_field)


# ── CRUD: FormTemplate endpoints ─────────────────────────────────────────────────

class TestFormTemplateCRUD(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='CRUDOrg', code='crud-org')
        cls.user = User.objects.create_user(
            username='cruduser',
            password='pass',
            org=cls.org,
            is_active=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_form_template(self):
        resp = self.client.post('/api/intake/form-templates/', {
            'name': 'Test Template',
            'description': 'A test template',
            'is_active': True,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], 'Test Template')
        self.assertEqual(resp.data['org'], self.org.id)

    def test_list_form_templates(self):
        FormTemplate.objects.create(org=self.org, name='Listed Template')
        resp = self.client.get('/api/intake/form-templates/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data['results']), 0)

    def test_update_form_template(self):
        t = FormTemplate.objects.create(org=self.org, name='Old Name')
        resp = self.client.patch(f'/api/intake/form-templates/{t.id}/', {'name': 'New Name'})
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.name, 'New Name')

    def test_soft_delete_form_template(self):
        t = FormTemplate.objects.create(org=self.org, name='Del Template', is_active=True)
        resp = self.client.delete(f'/api/intake/form-templates/{t.id}/')
        self.assertEqual(resp.status_code, 204)
        t.refresh_from_db()
        self.assertFalse(t.is_active)

    def test_create_form_section(self):
        t = FormTemplate.objects.create(org=self.org, name='Section Test Template')
        resp = self.client.post('/api/intake/form-sections/', {
            'template': t.id,
            'name': 'General Info',
            'sort_order': 0,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], 'General Info')

    def test_create_template_field(self):
        t = FormTemplate.objects.create(org=self.org, name='Field Test Template')
        s = FormSection.objects.create(template=t, name='Section', sort_order=0)
        resp = self.client.post('/api/intake/template-fields/', {
            'section': s.id,
            'label': 'Age',
            'field_key': 'age',
            'field_type': 'number',
            'sort_order': 0,
            'is_required': True,
            'options': [],
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['label'], 'Age')

    def test_soft_delete_template_field(self):
        t = FormTemplate.objects.create(org=self.org, name='FdDelTemplate')
        s = FormSection.objects.create(template=t, name='S', sort_order=0)
        f = FormTemplateField.objects.create(
            section=s, label='Test', field_key='test',
            field_type='text', sort_order=0, is_active=True,
        )
        resp = self.client.delete(f'/api/intake/template-fields/{f.id}/')
        self.assertEqual(resp.status_code, 204)
        f.refresh_from_db()
        self.assertFalse(f.is_active)

    def test_retrieve_template_includes_sections_and_fields(self):
        t = FormTemplate.objects.create(org=self.org, name='Nested Template')
        s = FormSection.objects.create(template=t, name='Section', sort_order=0)
        FormTemplateField.objects.create(
            section=s, label='Name', field_key='name',
            field_type='text', sort_order=0, is_active=True,
        )
        resp = self.client.get(f'/api/intake/form-templates/{t.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['sections']), 1)
        self.assertEqual(len(resp.data['sections'][0]['template_fields']), 1)


# ── Fixup tests: code field, section metadata, ordering, role fields ────────────

class TestFormBuilderFixup(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='FixupOrg', code='fixup-org')
        cls.site = _site(cls.org)
        cls.role_guard = JobRole.objects.create(org=cls.org, name='Guard', code='FX-GUARD')
        cls.role_elec = JobRole.objects.create(org=cls.org, name='Electrician', code='FX-ELEC')

        # Template with sections and role fields
        cls.template = _template(cls.org, 'Fixup Template', code='fixup_standard')
        cls.sec_gen = _section(cls.template, 'General Information', 'general_information', 0)
        cls.sec_doc = _section(cls.template, 'Documents', 'documents', 1)

        # Common gen fields (sort_order 0, 1 within section sort_order 0)
        cls.tf_age = _tmpl_field(cls.sec_gen, 'Age', 'age', 'number', 0, is_required=True)
        cls.tf_gender = _tmpl_field(
            cls.sec_gen, 'Gender', 'gender', 'select', 1,
            options=['Male', 'Female'],
        )
        cls.tf_joining = _tmpl_field(
            cls.sec_gen, 'Joining Availability', 'joining_availability', 'select', 2,
            options=[
                'Immediate', 'Within 3 days', 'Within 7 days', 'Within 15 days',
                'Within 1 month', 'Within 3 months', 'After 3 months',
            ],
        )
        # Doc field (sort_order 0 within section sort_order 1)
        cls.tf_resume = _tmpl_field(cls.sec_doc, 'Resume', 'resume', 'file', 0, is_required=True)

        # Role-specific fields
        cls.tf_guard_height = _tmpl_field(
            cls.sec_gen, 'Height', 'height', 'number', 0, role=cls.role_guard,
        )
        cls.tf_elec_cert = _tmpl_field(
            cls.sec_gen, 'Certification', 'certification', 'text', 0, role=cls.role_elec,
        )

        cls.campaign = _campaign(cls.org, cls.site, code='FX-CAMP')
        cls.campaign.form_template = cls.template
        cls.campaign.save(update_fields=['form_template'])
        CampaignJobRole.objects.create(campaign=cls.campaign, job_role=cls.role_guard, is_active=True)
        CampaignJobRole.objects.create(campaign=cls.campaign, job_role=cls.role_elec, is_active=True)

    # ── code field ─────────────────────────────────────────────────────────────

    def test_template_code_stored(self):
        self.assertEqual(self.template.code, 'fixup_standard')

    def test_template_code_nullable(self):
        t = _template(self.org, 'No Code Template')
        self.assertIsNone(t.code)

    def test_template_code_unique_per_org(self):
        from django.db import IntegrityError
        _template(self.org, 'Unique Code One', code='uniq_code_fx')
        with self.assertRaises(Exception):
            # Same org + same code should fail
            _template(self.org, 'Unique Code Two', code='uniq_code_fx')

    def test_template_code_same_code_different_org_allowed(self):
        org2 = _org(name='OtherOrg', code='other-org-fx')
        t1 = _template(self.org, 'Cross Org A', code='shared_code_fx')
        t2 = _template(org2, 'Cross Org B', code='shared_code_fx')
        self.assertEqual(t1.code, t2.code)

    def test_section_has_code(self):
        self.assertEqual(self.sec_gen.code, 'general_information')
        self.assertEqual(self.sec_doc.code, 'documents')

    def test_crud_template_returns_code(self):
        user = User.objects.create_user(
            username='fixupuser', password='pass',
            org=self.org, is_active=True, is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post('/api/intake/form-templates/', {
            'name': 'Code Test Template',
            'code': 'code_test_fx',
            'description': '',
            'is_active': True,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['code'], 'code_test_fx')

    # ── Section metadata in public API ────────────────────────────────────────

    def test_public_template_fields_include_section_metadata(self):
        resp = _get_campaign(self.campaign.token)
        self.assertEqual(resp.status_code, 200)
        fields = resp.data['common_fields']
        self.assertGreater(len(fields), 0)
        for f in fields:
            self.assertIn('section_id', f)
            self.assertIn('section_name', f)
            self.assertIn('section_code', f)
            self.assertIn('section_sort_order', f)
            self.assertIsNotNone(f['section_id'])

    def test_public_template_gen_field_has_correct_section_code(self):
        resp = _get_campaign(self.campaign.token)
        common = resp.data['common_fields']
        age_field = next(f for f in common if f['field_key'] == 'age')
        self.assertEqual(age_field['section_code'], 'general_information')
        self.assertEqual(age_field['section_sort_order'], 0)

    def test_public_template_doc_field_has_correct_section_code(self):
        resp = _get_campaign(self.campaign.token)
        common = resp.data['common_fields']
        resume_field = next(f for f in common if f['field_key'] == 'resume')
        self.assertEqual(resume_field['section_code'], 'documents')
        self.assertEqual(resume_field['section_sort_order'], 1)

    def test_public_campaign_fields_have_null_section_metadata(self):
        # Campaign without template — FormFields have null section metadata
        c = _campaign(self.org, self.site, code='FX-NO-TMPL')
        FormField.objects.create(
            campaign=c, role=None, label='Age', field_key='age',
            field_type='number', sort_order=0, is_active=True,
        )
        CampaignJobRole.objects.create(campaign=c, job_role=self.role_guard, is_active=True)
        resp = _get_campaign(c.token)
        fields = resp.data['common_fields']
        age_field = next(f for f in fields if f['field_key'] == 'age')
        self.assertIsNone(age_field['section_id'])
        self.assertIsNone(age_field['section_code'])

    # ── Ordering ──────────────────────────────────────────────────────────────

    def test_common_fields_ordered_by_section_then_sort_order(self):
        resp = _get_campaign(self.campaign.token)
        common = resp.data['common_fields']
        # All gen section fields (sort_order=0) should come before doc fields (sort_order=1)
        gen_indices = [i for i, f in enumerate(common) if f['section_code'] == 'general_information']
        doc_indices = [i for i, f in enumerate(common) if f['section_code'] == 'documents']
        if gen_indices and doc_indices:
            self.assertLess(max(gen_indices), min(doc_indices))

    # ── Role fields ───────────────────────────────────────────────────────────

    def test_role_fields_not_empty_when_template_has_role_fields(self):
        resp = _get_campaign(self.campaign.token)
        role_fields = resp.data['role_fields']
        self.assertGreater(len(role_fields), 0)

    def test_role_fields_contain_guard_fields(self):
        resp = _get_campaign(self.campaign.token)
        role_fields = resp.data['role_fields']
        guard_key = str(self.role_guard.id)
        self.assertIn(guard_key, role_fields)
        keys = [f['field_key'] for f in role_fields[guard_key]]
        self.assertIn('height', keys)

    def test_role_fields_contain_electrician_fields(self):
        resp = _get_campaign(self.campaign.token)
        role_fields = resp.data['role_fields']
        elec_key = str(self.role_elec.id)
        self.assertIn(elec_key, role_fields)
        keys = [f['field_key'] for f in role_fields[elec_key]]
        self.assertIn('certification', keys)

    def test_role_fields_have_source_template(self):
        resp = _get_campaign(self.campaign.token)
        role_fields = resp.data['role_fields']
        guard_key = str(self.role_guard.id)
        for f in role_fields.get(guard_key, []):
            self.assertEqual(f['field_source'], 'template')

    # ── Joining availability options ─────────────────────────────────────────

    def test_joining_availability_options_in_template(self):
        expected = [
            'Immediate', 'Within 3 days', 'Within 7 days', 'Within 15 days',
            'Within 1 month', 'Within 3 months', 'After 3 months',
        ]
        resp = _get_campaign(self.campaign.token)
        common = resp.data['common_fields']
        joining = next((f for f in common if f['field_key'] == 'joining_availability'), None)
        self.assertIsNotNone(joining)
        self.assertEqual(joining['options'], expected)


# ── Phase Form-Builder-Backend-B: FormSection contract ─────────────────────────

class TestFormSectionContractB(TestCase):
    """Tests for FormSection description/is_active/created_at/updated_at + soft-delete."""

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(name='SecContractOrg', code='sc-org')
        cls.template = _template(cls.org, 'SC Template')
        cls.user = User.objects.create_user(
            username='scuser', password='pass',
            org=cls.org, is_active=True, is_superuser=True,
        )

    def _client(self):
        c = APIClient()
        c.force_authenticate(user=self.user)
        return c

    # 1. Model fields present

    def test_section_has_description_field(self):
        sec = FormSection.objects.create(
            template=self.template, name='Desc Test', code='desc_test',
            sort_order=0, description='A detailed description.',
        )
        sec.refresh_from_db()
        self.assertEqual(sec.description, 'A detailed description.')

    def test_section_is_active_defaults_true(self):
        sec = FormSection.objects.create(
            template=self.template, name='Active Default', code='active_default', sort_order=0,
        )
        self.assertTrue(sec.is_active)

    def test_section_has_created_at_and_updated_at(self):
        sec = FormSection.objects.create(
            template=self.template, name='Timestamps', code='timestamps', sort_order=0,
        )
        self.assertIsNotNone(sec.created_at)
        self.assertIsNotNone(sec.updated_at)

    # 2. Unique constraint on template+code (non-blank)

    def test_section_code_unique_per_template(self):
        FormSection.objects.create(
            template=self.template, name='Unique A', code='uq_sec_code', sort_order=0,
        )
        with self.assertRaises(Exception):
            FormSection.objects.create(
                template=self.template, name='Unique B', code='uq_sec_code', sort_order=1,
            )

    def test_section_blank_code_allows_duplicates(self):
        FormSection.objects.create(
            template=self.template, name='Blank Code A', code='', sort_order=0,
        )
        FormSection.objects.create(
            template=self.template, name='Blank Code B', code='', sort_order=1,
        )
        count = FormSection.objects.filter(template=self.template, code='').count()
        self.assertGreaterEqual(count, 2)

    # 3. Serializer exposes new fields

    def test_section_api_returns_description(self):
        FormSection.objects.create(
            template=self.template, name='API Desc', code='api_desc',
            sort_order=0, description='Returned from API.',
        )
        resp = self._client().get(
            f'/api/intake/form-sections/?template={self.template.id}'
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.data['items'] if 'items' in resp.data else resp.data.get('results', resp.data)
        matched = [s for s in items if s.get('code') == 'api_desc']
        self.assertTrue(len(matched) > 0)
        self.assertEqual(matched[0]['description'], 'Returned from API.')

    def test_section_api_returns_is_active(self):
        FormSection.objects.create(
            template=self.template, name='Active Field', code='active_field', sort_order=0,
        )
        resp = self._client().get(
            f'/api/intake/form-sections/?template={self.template.id}'
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.data['items'] if 'items' in resp.data else resp.data.get('results', resp.data)
        matched = [s for s in items if s.get('code') == 'active_field']
        self.assertIn('is_active', matched[0])

    def test_section_api_returns_timestamps(self):
        FormSection.objects.create(
            template=self.template, name='Timestamp Field', code='ts_field', sort_order=0,
        )
        resp = self._client().get(
            f'/api/intake/form-sections/?template={self.template.id}'
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.data['items'] if 'items' in resp.data else resp.data.get('results', resp.data)
        matched = [s for s in items if s.get('code') == 'ts_field']
        self.assertIn('created_at', matched[0])
        self.assertIn('updated_at', matched[0])

    # 4. is_active filter on list endpoint

    def test_section_list_filter_by_is_active(self):
        t2 = _template(self.org, 'Filter Template', code='filter_tmpl')
        FormSection.objects.create(
            template=t2, name='Active Sec', code='active_sec', sort_order=0, is_active=True,
        )
        FormSection.objects.create(
            template=t2, name='Inactive Sec', code='inactive_sec', sort_order=1, is_active=False,
        )
        resp = self._client().get(
            f'/api/intake/form-sections/?template={t2.id}&is_active=true'
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.data['items'] if 'items' in resp.data else resp.data.get('results', resp.data)
        codes = [s['code'] for s in items]
        self.assertIn('active_sec', codes)
        self.assertNotIn('inactive_sec', codes)

    # 5. Soft-delete (DELETE sets is_active=False, does not remove row)

    def test_section_delete_soft_deactivates(self):
        sec = FormSection.objects.create(
            template=self.template, name='Soft Del', code='soft_del', sort_order=0,
        )
        resp = self._client().delete(f'/api/intake/form-sections/{sec.id}/')
        self.assertEqual(resp.status_code, 204)
        sec.refresh_from_db()
        self.assertFalse(sec.is_active)

    def test_section_soft_deleted_row_still_exists(self):
        sec = FormSection.objects.create(
            template=self.template, name='Still Exists', code='still_exists', sort_order=0,
        )
        self._client().delete(f'/api/intake/form-sections/{sec.id}/')
        self.assertTrue(FormSection.objects.filter(pk=sec.pk).exists())

    # 6. Public API excludes fields from inactive sections

    def test_public_api_excludes_fields_from_inactive_section(self):
        org2 = _org(name='PubSecOrg', code='pub-sec-org')
        site2 = _site(org2)
        tmpl2 = _template(org2, 'Pub Sec Template')
        active_sec = FormSection.objects.create(
            template=tmpl2, name='Active', code='act_pub', sort_order=0, is_active=True,
        )
        inactive_sec = FormSection.objects.create(
            template=tmpl2, name='Inactive', code='inact_pub', sort_order=1, is_active=False,
        )
        _tmpl_field(active_sec, 'Visible Field', 'visible_field', order=0)
        _tmpl_field(inactive_sec, 'Hidden Field', 'hidden_field', order=0)
        camp = _campaign(org2, site2, code='PUB-SEC-CAMP')
        camp.form_template = tmpl2
        camp.save(update_fields=['form_template'])
        resp = _get_campaign(camp.token)
        self.assertEqual(resp.status_code, 200)
        keys = [f['field_key'] for f in resp.data['common_fields']]
        self.assertIn('visible_field', keys)
        self.assertNotIn('hidden_field', keys)

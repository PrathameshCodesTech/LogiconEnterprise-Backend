"""
apps/mrf/tests/test_mrf_client_form_a.py

Phase MRF-Client-Form-A — 12 tests covering:
  01. Create MRF with new header fields
  02. Create MRF with nested support_requirement
  03. Retrieve MRF returns support_requirement object
  04. PATCH header field updates correctly
  05. PATCH nested support_requirement upserts correctly
  06. Missing support_requirement is allowed
  07. Negative experience_min_years rejected
  08. experience_min_years > experience_max_years rejected
  09. Duplicate request_number in same org rejected
  10. Same request_number in different org allowed
  11. Empty request_number can repeat
  12. Existing MRF workflow tests still pass (regression)
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.mrf.models import ManpowerRequest, MRFSupportRequirement
from apps.sites.models import Client, SiteProfile


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name=f'Client {org.code}', code=f'cl-{org.code}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name=f'Site {org.code}', code=f'site-{org.code}', is_active=True,
    )
    return n_co, site


def _role(org, code):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    bootstrap_role_permissions(r)
    return r


def _user(username, org, role_obj, scope_node):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _base_payload(site):
    return {
        'site': site.pk,
        'mrf_type': 'new_hiring',
        'billing_type': 'billable',
    }


# ─── Base test class ──────────────────────────────────────────────────────────

class MRFClientFormBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org_a = _org('cfa')
        cls.n_co_a, cls.site_a = _scope_tree(cls.org_a)
        cls.r_hr = _role(cls.org_a, 'hr_admin')
        cls.hr = _user('cfa_hr', cls.org_a, cls.r_hr, cls.n_co_a)

        cls.org_b = _org('cfb')
        cls.n_co_b, cls.site_b = _scope_tree(cls.org_b)
        cls.r_hr_b = _role(cls.org_b, 'hr_admin')
        cls.hr_b = _user('cfb_hr', cls.org_b, cls.r_hr_b, cls.n_co_b)

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user=None):
        self.api.force_authenticate(user=user or self.hr)

    def _post(self, payload):
        return self.api.post('/api/mrf/requests/', payload, format='json')

    def _patch(self, pk, payload):
        return self.api.patch(f'/api/mrf/requests/{pk}/', payload, format='json')

    def _get(self, pk):
        return self.api.get(f'/api/mrf/requests/{pk}/')


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestMRFClientFormA(MRFClientFormBase):

    # ── test_01 ──────────────────────────────────────────────────────────────

    def test_01_create_mrf_with_header_fields(self):
        """Header fields are saved and returned on create."""
        self._auth()
        payload = {
            **_base_payload(self.site_a),
            'request_number': 'RN-2026-001',
            'experience_min_years': '1.50',
            'experience_max_years': '5.00',
            'reporting_to': 'Operations Manager',
            'mis_requirement': 'Weekly report',
            'education_requirement': '12th pass preferred',
            'gender_preference': 'Any',
            'special_requirement': 'Night shift ready',
            'salary_range_text': '15000-25000',
            'ctc_budget_text': '3L-5L PA',
            'reference_note': 'Client request',
            'other_remarks': 'Urgent',
        }
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201, resp.data)
        data = resp.data
        self.assertEqual(data['request_number'], 'RN-2026-001')
        self.assertEqual(Decimal(data['experience_min_years']), Decimal('1.50'))
        self.assertEqual(Decimal(data['experience_max_years']), Decimal('5.00'))
        self.assertEqual(data['reporting_to'], 'Operations Manager')
        self.assertEqual(data['education_requirement'], '12th pass preferred')
        self.assertEqual(data['special_requirement'], 'Night shift ready')
        self.assertEqual(data['salary_range_text'], '15000-25000')
        self.assertEqual(data['ctc_budget_text'], '3L-5L PA')
        self.assertEqual(data['reference_note'], 'Client request')
        self.assertEqual(data['other_remarks'], 'Urgent')

    # ── test_02 ──────────────────────────────────────────────────────────────

    def test_02_create_mrf_with_nested_support_requirement(self):
        """Creating MRF with support_requirement dict creates the linked row."""
        self._auth()
        payload = {
            **_base_payload(self.site_a),
            'support_requirement': {
                'uniform_required': True,
                'mail_id_required': True,
                'seating_required': True,
                'admin_location': 'Head Office',
            },
        }
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201, resp.data)
        sr = resp.data.get('support_requirement')
        self.assertIsNotNone(sr)
        self.assertTrue(sr['uniform_required'])
        self.assertTrue(sr['mail_id_required'])
        self.assertTrue(sr['seating_required'])
        self.assertEqual(sr['admin_location'], 'Head Office')
        self.assertFalse(sr['laptop_required'])

        # Confirm DB row exists
        mrf_id = resp.data['id']
        self.assertTrue(MRFSupportRequirement.objects.filter(mrf_id=mrf_id).exists())

    # ── test_03 ──────────────────────────────────────────────────────────────

    def test_03_retrieve_mrf_returns_support_requirement(self):
        """GET /api/mrf/requests/{id}/ includes support_requirement in response."""
        self._auth()
        resp = self._post({
            **_base_payload(self.site_a),
            'support_requirement': {'laptop_required': True, 'hrms_login_required': True},
        })
        self.assertEqual(resp.status_code, 201)
        pk = resp.data['id']

        get_resp = self._get(pk)
        self.assertEqual(get_resp.status_code, 200)
        sr = get_resp.data.get('support_requirement')
        self.assertIsNotNone(sr)
        self.assertTrue(sr['laptop_required'])
        self.assertTrue(sr['hrms_login_required'])

    # ── test_04 ──────────────────────────────────────────────────────────────

    def test_04_patch_header_field_updates_correctly(self):
        """PATCH on a header field updates the field without touching others."""
        self._auth()
        create_resp = self._post({
            **_base_payload(self.site_a),
            'reporting_to': 'Site Manager',
            'gender_preference': 'Male',
        })
        self.assertEqual(create_resp.status_code, 201)
        pk = create_resp.data['id']

        patch_resp = self._patch(pk, {'reporting_to': 'Regional Director'})
        self.assertEqual(patch_resp.status_code, 200, patch_resp.data)
        self.assertEqual(patch_resp.data['reporting_to'], 'Regional Director')
        self.assertEqual(patch_resp.data['gender_preference'], 'Male')

    # ── test_05 ──────────────────────────────────────────────────────────────

    def test_05_patch_nested_support_requirement_upserts(self):
        """PATCH with support_requirement upserts the linked row."""
        self._auth()
        # Create without support requirement
        create_resp = self._post(_base_payload(self.site_a))
        self.assertEqual(create_resp.status_code, 201)
        pk = create_resp.data['id']
        self.assertIsNone(create_resp.data.get('support_requirement'))

        # First PATCH creates the row
        patch1 = self._patch(pk, {'support_requirement': {'sim_card_required': True}})
        self.assertEqual(patch1.status_code, 200)
        sr1 = patch1.data.get('support_requirement')
        self.assertIsNotNone(sr1)
        self.assertTrue(sr1['sim_card_required'])
        self.assertFalse(sr1['data_card_required'])

        # Second PATCH updates the existing row
        patch2 = self._patch(pk, {'support_requirement': {'data_card_required': True}})
        self.assertEqual(patch2.status_code, 200)
        sr2 = patch2.data.get('support_requirement')
        self.assertTrue(sr2['data_card_required'])

    # ── test_06 ──────────────────────────────────────────────────────────────

    def test_06_missing_support_requirement_is_allowed(self):
        """Creating MRF without support_requirement is valid; field is null in response."""
        self._auth()
        resp = self._post(_base_payload(self.site_a))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data.get('support_requirement'))

    # ── test_07 ──────────────────────────────────────────────────────────────

    def test_07_negative_experience_rejected(self):
        """Negative experience values return 400."""
        self._auth()
        resp = self._post({
            **_base_payload(self.site_a),
            'experience_min_years': '-1.00',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('experience_min_years', resp.data)

    # ── test_08 ──────────────────────────────────────────────────────────────

    def test_08_min_experience_exceeds_max_rejected(self):
        """experience_min_years > experience_max_years returns 400."""
        self._auth()
        resp = self._post({
            **_base_payload(self.site_a),
            'experience_min_years': '5.00',
            'experience_max_years': '2.00',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('experience_min_years', resp.data)

    # ── test_09 ──────────────────────────────────────────────────────────────

    def test_09_duplicate_request_number_same_org_rejected(self):
        """Two MRFs with the same non-empty request_number in the same org returns 400."""
        self._auth()
        resp1 = self._post({
            **_base_payload(self.site_a),
            'request_number': 'DUP-001',
        })
        self.assertEqual(resp1.status_code, 201)

        resp2 = self._post({
            **_base_payload(self.site_a),
            'request_number': 'DUP-001',
        })
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('request_number', resp2.data)

    # ── test_10 ──────────────────────────────────────────────────────────────

    def test_10_same_request_number_different_org_allowed(self):
        """Same request_number in two different orgs is allowed."""
        self._auth(self.hr)
        resp1 = self._post({
            **_base_payload(self.site_a),
            'request_number': 'SHARED-001',
        })
        self.assertEqual(resp1.status_code, 201)

        self._auth(self.hr_b)
        resp2 = self._post({
            **_base_payload(self.site_b),
            'request_number': 'SHARED-001',
        })
        self.assertEqual(resp2.status_code, 201, resp2.data)

    # ── test_11 ──────────────────────────────────────────────────────────────

    def test_11_empty_request_number_can_repeat(self):
        """Multiple MRFs with empty request_number are allowed in the same org."""
        self._auth()
        resp1 = self._post(_base_payload(self.site_a))
        resp2 = self._post(_base_payload(self.site_a))
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 201)

    # ── test_12 ──────────────────────────────────────────────────────────────

    def test_12_existing_mrf_create_still_works(self):
        """Basic MRF creation (pre-existing behaviour) is unaffected by the new fields."""
        self._auth()
        resp = self._post({
            'site': self.site_a.pk,
            'mrf_type': 'replacement',
            'billing_type': 'billable',
            'reason': 'Attrition',
            'department': 'Security',
        })
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['mrf_type'], 'replacement')
        self.assertEqual(resp.data['department'], 'Security')
        # new fields default to empty / null
        self.assertEqual(resp.data['request_number'], '')
        self.assertIsNone(resp.data['experience_min_years'])
        self.assertIsNone(resp.data['experience_max_years'])
        self.assertIsNone(resp.data['support_requirement'])

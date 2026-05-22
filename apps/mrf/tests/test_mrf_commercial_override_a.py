"""
apps/mrf/tests/test_mrf_commercial_override_a.py

Phase MRF-Commercial-Override-A tests.

Scenarios:
  1.  Master snapshots stored from SRR on line item create.
  2.  Normal user cannot override wage_min_requested.
  3.  Normal user cannot override wage_max_requested.
  4.  Normal user cannot override billing_rate_snapshot.
  5.  User with mrf.override_commercials can override with reason.
  6.  Override with capability but no reason returns 400.
  7.  PATCH commercial fields blocked for normal user.
  8.  PATCH non-commercial field (headcount) allowed for normal user.
  9.  Posting values matching master → no override flag set.
  10. Override audit fields (overridden_by, overridden_at) set correctly.
  11. PATCH to master value clears override state.
  12. Read serializer exposes all new commercial override fields.
  13. effective_wage_min uses wage_min_requested when set, else master snapshot.
  14. Readiness check emits warning when a line item has override enabled.
  15. Non-billable MRF: override check is skipped (any user can set wage).
  16. Line item without SRR: override check is skipped.
  17. Workflow drawer serializer includes override fields.
  18. Superuser can override commercials without mrf.override_commercials capability.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.capabilities import (
    MRF_CREATE, MRF_READ, MRF_UPDATE,
    MRF_OVERRIDE_COMMERCIALS,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions, get_or_create_permission
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _org(code='co-a'):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name='Client', code=f'cl-{org.code}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name='Site', code=f'si-{org.code}', city='City', state='ST', is_active=True,
    )
    return n_co, n_cl, client, site


def _role(org, code, caps):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    bootstrap_role_permissions(role, caps)
    return role


def _user(username, org, role, scope_node, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass', is_superuser=is_superuser)
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _job_role(org, name='Guard'):
    return JobRole.objects.get_or_create(org=org, name=name, defaults={'code': name.lower()})[0]


def _srr(site, job_role, wage_min='250.00', wage_max='350.00', billing_rate='500.00', shift_hours='8.0'):
    return SiteRoleRequirement.objects.create(
        site=site,
        job_role=job_role,
        approved_headcount=10,
        billing_rate=billing_rate,
        wage_min=wage_min,
        wage_max=wage_max,
        shift_hours=shift_hours,
        billing_type='billable',
        effective_from=date.today(),
        is_active=True,
    )


def _mrf(org, site, user, billing_type='billable'):
    return ManpowerRequest.objects.create(
        org=org,
        site=site,
        requested_by=user,
        mrf_type='new_hiring',
        billing_type=billing_type,
        status='draft',
    )


LINE_ITEMS_URL = '/api/mrf/line-items/'


# ─── Test class ───────────────────────────────────────────────────────────────

class TestMRFCommercialOverrideA(TestCase):

    def setUp(self):
        self.org = _org('ovr')
        self.n_co, self.n_cl, self.client, self.site = _scope_tree(self.org)
        self.job_role = _job_role(self.org)

        # Role without override capability
        self.role_basic = _role(self.org, 'mrf_basic', [MRF_READ, MRF_CREATE, MRF_UPDATE])
        # Role with override capability
        self.role_override = _role(
            self.org, 'mrf_override',
            [MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_OVERRIDE_COMMERCIALS],
        )

        self.user_basic = _user('basic', self.org, self.role_basic, self.n_co)
        self.user_override = _user('overrider', self.org, self.role_override, self.n_co)
        self.user_super = _user('super', self.org, None, None, is_superuser=True)

        self.srr = _srr(self.site, self.job_role)
        self.mrf = _mrf(self.org, self.site, self.user_basic)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _payload(self, **overrides):
        base = {
            'mrf': self.mrf.pk,
            'site_role_requirement': self.srr.pk,
            'job_role': self.job_role.pk,
            'headcount': 2,
        }
        base.update(overrides)
        return base

    # ── 1. Master snapshots stored ────────────────────────────────────────────

    def test_master_snapshots_stored_on_create(self):
        c = self._client(self.user_basic)
        resp = c.post(LINE_ITEMS_URL, self._payload(), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertEqual(li.master_wage_min_snapshot, Decimal('250.00'))
        self.assertEqual(li.master_wage_max_snapshot, Decimal('350.00'))
        self.assertEqual(li.master_billing_rate_snapshot, Decimal('500.00'))
        self.assertEqual(li.master_shift_hours_snapshot, Decimal('8.0'))
        self.assertFalse(li.commercial_override_enabled)

    # ── 2-4. Normal user cannot override commercial values ────────────────────

    def test_normal_user_cannot_override_wage_min(self):
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(wage_min_requested='999.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    def test_normal_user_cannot_override_wage_max(self):
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(wage_max_requested='999.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    def test_normal_user_cannot_override_billing_rate(self):
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(billing_rate_snapshot='999.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    # ── 5. Override with capability + reason succeeds ─────────────────────────

    def test_override_with_capability_and_reason_succeeds(self):
        c = self._client(self.user_override)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                wage_min_requested='999.00',
                commercial_override_reason='Client negotiated special rate',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertTrue(li.commercial_override_enabled)
        self.assertEqual(li.commercial_override_reason, 'Client negotiated special rate')
        self.assertEqual(li.commercial_overridden_by, self.user_override)
        self.assertIsNotNone(li.commercial_overridden_at)
        # Master snapshot still holds original SRR value
        self.assertEqual(li.master_wage_min_snapshot, Decimal('250.00'))

    # ── 6. Override without reason returns 400 ────────────────────────────────

    def test_override_with_capability_no_reason_returns_400(self):
        c = self._client(self.user_override)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(wage_min_requested='999.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('commercial_override_reason', str(resp.data))

    # ── 7. PATCH commercial blocked for normal user ───────────────────────────

    def test_patch_commercial_blocked_for_normal_user(self):
        # First create without override
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
        )
        c = self._client(self.user_basic)
        resp = c.patch(
            f'{LINE_ITEMS_URL}{li.pk}/',
            {'billing_rate_snapshot': '999.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    # ── 8. PATCH non-commercial allowed for normal user ───────────────────────

    def test_patch_non_commercial_allowed_for_normal_user(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
        )
        c = self._client(self.user_basic)
        resp = c.patch(
            f'{LINE_ITEMS_URL}{li.pk}/',
            {'headcount': 3},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        li.refresh_from_db()
        self.assertEqual(li.headcount, 3)
        self.assertFalse(li.commercial_override_enabled)

    # ── 9. Matching master value → no override ────────────────────────────────

    def test_matching_master_values_no_override(self):
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                wage_min_requested='250.00',
                wage_max_requested='350.00',
                billing_rate_snapshot='500.00',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertFalse(li.commercial_override_enabled)

    # ── 10. Override audit fields set correctly ───────────────────────────────

    def test_override_audit_fields_set_correctly(self):
        before = timezone.now()
        c = self._client(self.user_override)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                billing_rate_snapshot='750.00',
                commercial_override_reason='Approved by finance',
            ),
            format='json',
        )
        after = timezone.now()
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertEqual(li.commercial_overridden_by_id, self.user_override.pk)
        self.assertGreaterEqual(li.commercial_overridden_at, before)
        self.assertLessEqual(li.commercial_overridden_at, after)

    # ── 11. PATCH to master value clears override ─────────────────────────────

    def test_patch_to_master_value_clears_override(self):
        # Create with override
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('750.00'),
            commercial_override_enabled=True,
            commercial_override_reason='old reason',
            commercial_overridden_by=self.user_override,
        )
        # Patch back to master value
        c = self._client(self.user_override)
        resp = c.patch(
            f'{LINE_ITEMS_URL}{li.pk}/',
            {'billing_rate_snapshot': '500.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        li.refresh_from_db()
        self.assertFalse(li.commercial_override_enabled)
        self.assertEqual(li.commercial_override_reason, '')
        self.assertIsNone(li.commercial_overridden_by_id)
        self.assertIsNone(li.commercial_overridden_at)

    # ── 12. Read serializer exposes all new fields ────────────────────────────

    def test_read_serializer_exposes_override_fields(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
            master_wage_min_snapshot=Decimal('250.00'),
            master_wage_max_snapshot=Decimal('350.00'),
            master_billing_rate_snapshot=Decimal('500.00'),
            master_shift_hours_snapshot=Decimal('8.0'),
        )
        c = self._client(self.user_basic)
        resp = c.get(f'{LINE_ITEMS_URL}{li.pk}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn('master_wage_min_snapshot', data)
        self.assertIn('master_wage_max_snapshot', data)
        self.assertIn('master_billing_rate_snapshot', data)
        self.assertIn('master_shift_hours_snapshot', data)
        self.assertIn('commercial_override_enabled', data)
        self.assertIn('commercial_override_reason', data)
        self.assertIn('commercial_overridden_by', data)
        self.assertIn('commercial_overridden_at', data)
        self.assertIn('effective_wage_min', data)
        self.assertIn('effective_wage_max', data)
        self.assertEqual(data['commercial_override_enabled'], False)

    # ── 13. effective_wage_min resolution ─────────────────────────────────────

    def test_effective_wage_min_uses_requested_when_set(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=1,
            master_wage_min_snapshot=Decimal('250.00'),
            wage_min_requested=Decimal('999.00'),
        )
        c = self._client(self.user_basic)
        resp = c.get(f'{LINE_ITEMS_URL}{li.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data['effective_wage_min']), Decimal('999.00'))

    def test_effective_wage_min_falls_back_to_master(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=1,
            master_wage_min_snapshot=Decimal('250.00'),
            wage_min_requested=None,
        )
        c = self._client(self.user_basic)
        resp = c.get(f'{LINE_ITEMS_URL}{li.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data['effective_wage_min']), Decimal('250.00'))

    # ── 14. Readiness warning when override enabled ───────────────────────────

    def test_readiness_warning_when_override_enabled(self):
        MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
            commercial_override_enabled=True,
        )
        from apps.mrf.services import check_mrf_readiness
        result = check_mrf_readiness(self.mrf)
        self.assertTrue(any('override' in w.lower() for w in result['warnings']))

    def test_readiness_no_warning_when_no_override(self):
        MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
            commercial_override_enabled=False,
        )
        from apps.mrf.services import check_mrf_readiness
        result = check_mrf_readiness(self.mrf)
        self.assertFalse(any('override' in w.lower() for w in result['warnings']))

    # ── 15. Non-billable MRF: override check skipped ─────────────────────────

    def test_non_billable_mrf_override_check_skipped(self):
        nb_mrf = _mrf(self.org, self.site, self.user_basic, billing_type='non_billable')
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': nb_mrf.pk,
                'site_role_requirement': self.srr.pk,
                'job_role': self.job_role.pk,
                'headcount': 2,
                'wage_min_requested': '999.00',
            },
            format='json',
        )
        # Non-billable: override enforcement skipped, no 400 from commercial check
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertFalse(li.commercial_override_enabled)

    # ── 16. Line item without SRR: override check skipped ────────────────────

    def test_line_item_without_srr_override_check_skipped(self):
        c = self._client(self.user_basic)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': self.mrf.pk,
                'job_role': self.job_role.pk,
                'headcount': 2,
                'wage_min_requested': '999.00',
            },
            format='json',
        )
        # No SRR → no override enforcement
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertFalse(li.commercial_override_enabled)

    # ── 17. Workflow drawer serializer includes override fields ───────────────

    def test_workflow_drawer_includes_override_fields(self):
        from apps.workflow.serializers import _serialize_mrf_line_item_drawer
        li = MRFLineItem.objects.create(
            mrf=self.mrf, site_role_requirement=self.srr,
            job_role=self.job_role, headcount=2,
            master_wage_min_snapshot=Decimal('250.00'),
            master_wage_max_snapshot=Decimal('350.00'),
            master_billing_rate_snapshot=Decimal('500.00'),
            commercial_override_enabled=True,
            commercial_override_reason='Approved by finance',
        )
        # select_related attributes accessed in serializer
        li.job_role  # preloaded via FK
        result = _serialize_mrf_line_item_drawer(li)
        self.assertIn('master_wage_min_snapshot', result)
        self.assertIn('master_wage_max_snapshot', result)
        self.assertIn('master_billing_rate_snapshot', result)
        self.assertIn('commercial_override_enabled', result)
        self.assertIn('commercial_override_reason', result)
        self.assertIn('commercial_overridden_at', result)
        self.assertTrue(result['commercial_override_enabled'])
        self.assertEqual(result['commercial_override_reason'], 'Approved by finance')
        self.assertEqual(result['master_wage_min_snapshot'], '250.00')

    # ── 18. Superuser can override without capability ─────────────────────────

    def test_superuser_can_override_without_capability(self):
        c = self._client(self.user_super)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                wage_min_requested='999.00',
                commercial_override_reason='Superuser approval',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertTrue(li.commercial_override_enabled)

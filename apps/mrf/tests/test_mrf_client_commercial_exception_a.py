"""
apps/mrf/tests/test_mrf_client_commercial_exception_a.py

Phase MRF-Client-Commercial-Exception-A tests.

Verifies that client-facing roles (client_admin / client_site_user / site_supervisor)
can request commercial exceptions on billable MRF line items, subject to the same
override-enforcement rules (reason required, audit trail, budget uses overridden value).

Scenarios:
  1.  client_admin role has mrf.override_commercials in ROLE_CAPABILITIES.
  2.  client_site_user role has mrf.override_commercials in ROLE_CAPABILITIES.
  3.  site_supervisor role has mrf.override_commercials in ROLE_CAPABILITIES.
  4.  client_admin user can POST line item with changed billing_rate + reason → 201.
  5.  Same request without reason returns 400.
  6.  User without mrf.override_commercials rejected when values differ → 400.
  7.  Master snapshots remain unchanged after client override.
  8.  Budget reservation uses overridden billing_rate_snapshot, not master.
  9.  Readiness warning present when client override is active.
  10. Workflow drawer payload includes override / exception fields.
  11. client_admin PATCH: override existing line item commercials with reason → 200.
  12. client_admin PATCH: changing headcount (non-commercial) does not flag override.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    MRF_CREATE, MRF_READ, MRF_UPDATE, MRF_OVERRIDE_COMMERCIALS,
    ROLE_CAPABILITIES,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.budgets.services import calculate_mrf_reservation_amount
from apps.budgets.exceptions import BudgetReservationError
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement


LINE_ITEMS_URL = '/api/mrf/line-items/'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org(code='cli-exc'):
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
    n_site = ScopeNode.objects.create(
        org=org, code=f'si-{org.code}', name=f'si-{org.code}', node_type='site',
        parent=n_cl, depth=2, path=f'{org.code}/cl-{org.code}/si-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_site,
        name='Site', code=f'si-{org.code}', city='City', state='ST', is_active=True,
    )
    return n_co, n_cl, n_site, client, site


def _role(org, code):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    bootstrap_role_permissions(role)
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
        org=org, site=site, requested_by=user,
        mrf_type='new_hiring', billing_type=billing_type, status='draft',
    )


def _budget_plan(org, client, site=None, amount='1000000.00'):
    from datetime import date as _date
    return BudgetPlan.objects.create(
        org=org,
        code=f'bp-{org.code}',
        name='Test Budget',
        budget_nature='billable',
        budget_type='headcount',
        client=client,
        site=site,
        amount=amount,
        currency='INR',
        status='active',
        is_active=True,
        period_start=_date(2025, 4, 1),
        period_end=_date(2026, 3, 31),
    )


# ─── Test class ───────────────────────────────────────────────────────────────

class TestMRFClientCommercialExceptionA(TestCase):

    def setUp(self):
        self.org = _org('cli-exc')
        self.n_co, self.n_cl, self.n_site, self.client, self.site = _scope_tree(self.org)
        self.job_role = _job_role(self.org)
        self.srr = _srr(self.site, self.job_role)

        # Bootstrap all three client roles from ROLE_CAPABILITIES
        self.role_client_admin = _role(self.org, 'client_admin')
        self.role_client_site = _role(self.org, 'client_site_user')
        self.role_site_supervisor = _role(self.org, 'site_supervisor')

        # A role without override capability
        self.role_no_override = AccessRole.objects.create(
            org=self.org, code='no_override_role', name='No Override',
        )
        bootstrap_role_permissions(self.role_no_override, [MRF_READ, MRF_CREATE, MRF_UPDATE])

        self.user_client_admin = _user('cli_admin', self.org, self.role_client_admin, self.n_cl)
        self.user_client_site = _user('cli_site', self.org, self.role_client_site, self.n_site)
        self.user_site_sup = _user('site_sup', self.org, self.role_site_supervisor, self.n_site)
        self.user_no_cap = _user('no_cap', self.org, self.role_no_override, self.n_co)

        self.mrf = _mrf(self.org, self.site, self.user_client_admin)
        self.budget = _budget_plan(self.org, self.client)

    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _payload(self, user=None, **overrides):
        base = {
            'mrf': self.mrf.pk,
            'site_role_requirement': self.srr.pk,
            'job_role': self.job_role.pk,
            'headcount': 2,
        }
        base.update(overrides)
        return base

    # ── 1-3. Role capability assertions ───────────────────────────────────────

    def test_client_admin_role_has_override_commercials_capability(self):
        caps = ROLE_CAPABILITIES.get('client_admin', [])
        self.assertIn(MRF_OVERRIDE_COMMERCIALS, caps)

    def test_client_site_user_role_has_override_commercials_capability(self):
        caps = ROLE_CAPABILITIES.get('client_site_user', [])
        self.assertIn(MRF_OVERRIDE_COMMERCIALS, caps)

    def test_site_supervisor_role_has_override_commercials_capability(self):
        caps = ROLE_CAPABILITIES.get('site_supervisor', [])
        self.assertIn(MRF_OVERRIDE_COMMERCIALS, caps)

    # ── 4. client_admin can submit exception with reason ──────────────────────

    def test_client_admin_can_override_billing_rate_with_reason(self):
        c = self._api(self.user_client_admin)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                billing_rate_snapshot='750.00',
                commercial_override_reason='Client negotiated higher rate for Q1',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertTrue(li.commercial_override_enabled)
        self.assertEqual(li.commercial_override_reason, 'Client negotiated higher rate for Q1')
        self.assertEqual(li.commercial_overridden_by, self.user_client_admin)
        self.assertIsNotNone(li.commercial_overridden_at)

    def test_client_site_user_can_override_wage_with_reason(self):
        mrf = _mrf(self.org, self.site, self.user_client_site)
        c = self._api(self.user_client_site)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': mrf.pk,
                'site_role_requirement': self.srr.pk,
                'job_role': self.job_role.pk,
                'headcount': 1,
                'wage_min_requested': '400.00',
                'commercial_override_reason': 'Site specific rate adjustment',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertTrue(li.commercial_override_enabled)

    # ── 5. Without reason → 400 ───────────────────────────────────────────────

    def test_client_admin_override_without_reason_returns_400(self):
        c = self._api(self.user_client_admin)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(billing_rate_snapshot='750.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('commercial_override_reason', str(resp.data))

    # ── 6. User without capability rejected ───────────────────────────────────

    def test_user_without_override_cap_rejected_when_values_differ(self):
        mrf = _mrf(self.org, self.site, self.user_no_cap)
        c = self._api(self.user_no_cap)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': mrf.pk,
                'site_role_requirement': self.srr.pk,
                'job_role': self.job_role.pk,
                'headcount': 2,
                'billing_rate_snapshot': '750.00',
                'commercial_override_reason': 'I am trying',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    def test_client_requested_mrf_can_edit_billing_rate_without_reason(self):
        mrf = _mrf(self.org, self.site, self.user_no_cap)
        mrf.requested_by_type = 'client'
        mrf.save(update_fields=['requested_by_type'])

        c = self._api(self.user_no_cap)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': mrf.pk,
                'site_role_requirement': self.srr.pk,
                'job_role': self.job_role.pk,
                'headcount': 2,
                'billing_rate_snapshot': '750.00',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        self.assertTrue(li.commercial_override_enabled)
        self.assertEqual(
            li.commercial_override_reason,
            'Client requested billing rate differs from approved budget rate.',
        )
        self.assertEqual(li.commercial_overridden_by, self.user_no_cap)
        self.assertEqual(li.master_billing_rate_snapshot, Decimal('500.00'))
        self.assertEqual(li.billing_rate_snapshot, Decimal('750.00'))

    def test_client_requested_mrf_cannot_edit_wage_without_override_permission(self):
        mrf = _mrf(self.org, self.site, self.user_no_cap)
        mrf.requested_by_type = 'client'
        mrf.save(update_fields=['requested_by_type'])

        c = self._api(self.user_no_cap)
        resp = c.post(
            LINE_ITEMS_URL,
            {
                'mrf': mrf.pk,
                'site_role_requirement': self.srr.pk,
                'job_role': self.job_role.pk,
                'headcount': 2,
                'wage_min_requested': '400.00',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('override', str(resp.data).lower())

    def test_line_item_read_exposes_approved_requested_and_variance_amounts(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('750.00'),
            commercial_override_enabled=True,
        )

        c = self._api(self.user_client_admin)
        resp = c.get(f'{LINE_ITEMS_URL}{li.pk}/')

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['approved_billing_rate'], '500.00')
        self.assertEqual(resp.data['requested_billing_rate'], '750.00')
        self.assertEqual(resp.data['billing_rate_variance'], '250.00')
        self.assertTrue(resp.data['is_over_approved_billing_rate'])
        self.assertEqual(resp.data['line_approved_amount'], '1000.00')
        self.assertEqual(resp.data['line_requested_amount'], '1500.00')

    # ── 7. Master snapshots unchanged after override ──────────────────────────

    def test_master_snapshots_unchanged_after_client_override(self):
        c = self._api(self.user_client_admin)
        resp = c.post(
            LINE_ITEMS_URL,
            self._payload(
                billing_rate_snapshot='750.00',
                wage_min_requested='400.00',
                commercial_override_reason='Exception approved',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        li = MRFLineItem.objects.get(pk=resp.data['id'])
        # Master snapshots must still reflect original SRR values
        self.assertEqual(li.master_billing_rate_snapshot, Decimal('500.00'))
        self.assertEqual(li.master_wage_min_snapshot, Decimal('250.00'))
        self.assertEqual(li.master_wage_max_snapshot, Decimal('350.00'))
        # Override values are on the line item fields
        self.assertEqual(li.billing_rate_snapshot, Decimal('750.00'))
        self.assertEqual(li.wage_min_requested, Decimal('400.00'))

    # ── 8. Budget reservation uses overridden billing_rate_snapshot ───────────

    def test_budget_uses_overridden_billing_rate(self):
        """When billing_rate_snapshot is overridden to 750, budget = 2 × 750 = 1500."""
        li = MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('750.00'),
            commercial_override_enabled=True,
            commercial_override_reason='Test override',
            commercial_overridden_by=self.user_client_admin,
        )
        amount = calculate_mrf_reservation_amount(self.mrf, self.budget)
        # 2 headcount × 750.00 overridden billing rate
        self.assertEqual(amount, Decimal('1500.00'))

    def test_budget_uses_master_billing_rate_when_no_override(self):
        """Without override, budget = 2 × 500 = 1000."""
        MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('500.00'),
            commercial_override_enabled=False,
        )
        amount = calculate_mrf_reservation_amount(self.mrf, self.budget)
        self.assertEqual(amount, Decimal('1000.00'))

    # ── 9. Readiness warning when client override present ─────────────────────

    def test_readiness_warning_present_when_client_override_active(self):
        MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            commercial_override_enabled=True,
        )
        from apps.mrf.services import check_mrf_readiness
        result = check_mrf_readiness(self.mrf)
        self.assertTrue(any('override' in w.lower() for w in result['warnings']))
        # Warning must not block submission (it is non-blocking)
        self.assertNotIn(
            True,
            [True for e in result['errors'] if 'override' in e.lower()],
        )

    # ── 10. Workflow drawer includes override fields ──────────────────────────

    def test_workflow_drawer_payload_includes_exception_fields(self):
        from apps.workflow.serializers import _serialize_mrf_line_item_drawer
        li = MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('750.00'),
            commercial_override_enabled=True,
            commercial_override_reason='Client exception Q2',
        )
        result = _serialize_mrf_line_item_drawer(li)
        self.assertEqual(result['commercial_override_enabled'], True)
        self.assertEqual(result['commercial_override_reason'], 'Client exception Q2')
        self.assertEqual(result['master_billing_rate_snapshot'], '500.00')
        self.assertEqual(result['billing_rate_snapshot'], '750.00')
        self.assertIn('master_wage_min_snapshot', result)
        self.assertIn('commercial_overridden_at', result)

    # ── 11. PATCH override via client role ────────────────────────────────────

    def test_client_admin_patch_override_with_reason_succeeds(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('500.00'),
        )
        c = self._api(self.user_client_admin)
        resp = c.patch(
            f'{LINE_ITEMS_URL}{li.pk}/',
            {
                'billing_rate_snapshot': '750.00',
                'commercial_override_reason': 'Revised after client discussion',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        li.refresh_from_db()
        self.assertTrue(li.commercial_override_enabled)
        self.assertEqual(li.billing_rate_snapshot, Decimal('750.00'))

    # ── 12. PATCH headcount (non-commercial) never flags override ─────────────

    def test_patch_headcount_does_not_flag_override(self):
        li = MRFLineItem.objects.create(
            mrf=self.mrf,
            site_role_requirement=self.srr,
            job_role=self.job_role,
            headcount=2,
            master_billing_rate_snapshot=Decimal('500.00'),
            billing_rate_snapshot=Decimal('500.00'),
        )
        c = self._api(self.user_client_admin)
        resp = c.patch(
            f'{LINE_ITEMS_URL}{li.pk}/',
            {'headcount': 5},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        li.refresh_from_db()
        self.assertEqual(li.headcount, 5)
        self.assertFalse(li.commercial_override_enabled)

"""
apps/budgets/tests/test_budget_apis.py

Phase Budget-A API tests.

Test cases:
  1.  budget.read can list budget plans (200).
  2.  no budget.read gets 403.
  3.  unauthenticated gets 401.
  4.  budget.create can create billable budget with client (201).
  5.  billable without client returns 400.
  6.  billable with site auto-fills client.
  7.  site/client mismatch returns 400.
  8.  non-billable with department succeeds (201).
  9.  non-billable with client/site returns 400.
  10. non-billable without department returns 400.
  11. amount <= 0 returns 400.
  12. period_end before period_start returns 400.
  13. cross-org client returns 400.
  14. cross-org site returns 400.
  15. cross-org department returns 400.
  16. budget.update can patch amount and status (200).
  17. budget.delete soft-deactivates (204, is_active=False, status=inactive).
  18. non-superuser sees only own org.
  19. superuser sees all orgs.
  20. filter by budget_nature works.
  21. filter by status works.
  22. search by name works.
  23. read response includes denormalized client_name, site_name, department_name.
  24. create response includes read-only fields (created_by_username).
  25. non-superuser create forces actor.org.
  26. seed permissions include budget.update and budget.delete.
  27. finance role has budget.read/create/update/delete after seed.
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, AccessRolePermission, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions, get_or_create_permission
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan, BudgetReservation
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest
from apps.sales.models import (
    ProposalBreakupLine,
    ProposalBudgetLine,
    ProposalVersion,
    SalesLead,
    SalesLeadSite,
    SalesRoleRequirement,
)
from apps.sites.models import Client, SiteProfile


URL = '/api/budgets/plans/'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(
        username=username, password='pass',
        is_superuser=is_superuser, is_staff=is_superuser,
    )
    if org:
        u.org = org
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


def _client(org, code, scope_node):
    return Client.objects.create(org=org, name=code, code=code, scope_node=scope_node)


def _site(org, client, code, scope_node):
    return SiteProfile.objects.create(org=org, client=client, name=code, code=code, scope_node=scope_node)


def _dept(org, name, code):
    return Department.objects.create(org=org, name=name, code=code)


def _billable_payload(client_pk, **kwargs):
    base = {
        'name': 'Test Billable',
        'code': 'test-bill-01',
        'budget_nature': 'billable',
        'budget_type': 'manpower',
        'client': client_pk,
        'period_start': '2025-04-01',
        'amount': '500000.00',
        'currency': 'INR',
        'status': 'draft',
    }
    base.update(kwargs)
    return base


def _nonbillable_payload(dept_pk, **kwargs):
    base = {
        'name': 'Test NonBillable',
        'code': 'test-nonbill-01',
        'budget_nature': 'non_billable',
        'budget_type': 'hiring',
        'department': dept_pk,
        'period_start': '2025-04-01',
        'amount': '100000.00',
        'currency': 'INR',
        'status': 'draft',
    }
    base.update(kwargs)
    return base


# ─── Base ─────────────────────────────────────────────────────────────────────

class BudgetTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Budget Org', code='bgt-org')
        cls.org2 = Organization.objects.create(name='Other Org', code='bgt-org2')

        cls.n_co = _node(cls.org, 'bgt-org', 'company', None, 0, 'bgt-org')
        cls.n_cl = _node(cls.org, 'bgt-cl', 'client', cls.n_co, 1, 'bgt-org/bgt-cl')
        cls.n_site = _node(cls.org, 'bgt-site', 'site', cls.n_cl, 2, 'bgt-org/bgt-cl/bgt-site')
        cls.n_co2 = _node(cls.org2, 'bgt-org2', 'company', None, 0, 'bgt-org2')
        cls.n_cl2 = _node(cls.org2, 'bgt-cl2', 'client', cls.n_co2, 1, 'bgt-org2/bgt-cl2')

        cls.budget_client = _client(cls.org, 'bgt-client', cls.n_cl)
        cls.site = _site(cls.org, cls.budget_client, 'bgt-site', cls.n_site)
        cls.budget_client2 = _client(cls.org2, 'bgt-client2', cls.n_cl2)

        cls.dept = _dept(cls.org, 'Finance', 'bgt-finance')
        cls.dept2 = _dept(cls.org2, 'Finance Org2', 'bgt-finance2')

        # admin role — has budget.read/create/update/delete
        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin_user = _user('bgt_admin', org=cls.org)
        _assign(cls.admin_user, cls.role_admin, cls.n_co)

        cls.no_cap_user = _user('bgt_nocap', org=cls.org)

        cls.superuser = _user('bgt_super', is_superuser=True)

    def setUp(self):
        self.api = APIClient()

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── List / auth tests ────────────────────────────────────────────────────────

class TestBudgetAuth(BudgetTestBase):

    def test_budget_read_can_list(self):
        """1. budget.read can list."""
        self._login(self.admin_user)
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 200)

    def test_no_budget_read_gets_403(self):
        """2. no budget.read → 403."""
        self._login(self.no_cap_user)
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_gets_401(self):
        """3. unauthenticated → 401."""
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 401)


# ─── Create validation ────────────────────────────────────────────────────────

class TestBudgetCreate(BudgetTestBase):

    def test_create_billable_with_client(self):
        """4. billable with client → 201."""
        self._login(self.admin_user)
        resp = self.api.post(URL, _billable_payload(self.budget_client.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_nature'], 'billable')
        self.assertEqual(resp.data['client'], self.budget_client.pk)

    def test_billable_without_client_returns_400(self):
        """5. billable without client → 400."""
        self._login(self.admin_user)
        payload = _billable_payload(None, code='no-client-01')
        del payload['client']
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', str(resp.data))

    def test_billable_with_site_autofills_client(self):
        """6. billable with site but no client → client auto-filled from site."""
        self._login(self.admin_user)
        payload = {
            'name': 'Auto Client', 'code': 'auto-client-01',
            'budget_nature': 'billable', 'budget_type': 'manpower',
            'site': self.site.pk,
            'period_start': '2025-04-01', 'amount': '100000.00',
            'status': 'draft',
        }
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client'], self.budget_client.pk)

    def test_site_client_mismatch_returns_400(self):
        """7. site.client != provided client → 400."""
        # Create a second client in org, then try to use site with wrong client
        n_cl_b = _node(self.org, 'bgt-cl-b', 'client', self.n_co, 1, 'bgt-org/bgt-cl-b')
        client_b = _client(self.org, 'bgt-client-b', n_cl_b)
        self._login(self.admin_user)
        payload = _billable_payload(
            client_b.pk, code='mismatch-01', site=self.site.pk
        )
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', str(resp.data))

    def test_create_nonbillable_with_department(self):
        """8. non-billable with department → 201."""
        self._login(self.admin_user)
        resp = self.api.post(URL, _nonbillable_payload(self.dept.pk), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['budget_nature'], 'non_billable')
        self.assertEqual(resp.data['department'], self.dept.pk)

    def test_nonbillable_with_client_returns_400(self):
        """9. non-billable with client → 400."""
        self._login(self.admin_user)
        payload = _nonbillable_payload(self.dept.pk, code='nb-cl-01', client=self.budget_client.pk)
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', str(resp.data))

    def test_nonbillable_without_department_returns_400(self):
        """10. non-billable without department → 400."""
        self._login(self.admin_user)
        payload = {
            'name': 'No Dept', 'code': 'nb-nodept-01',
            'budget_nature': 'non_billable', 'budget_type': 'hiring',
            'period_start': '2025-04-01', 'amount': '100000.00',
            'status': 'draft',
        }
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', str(resp.data))

    def test_amount_zero_returns_400(self):
        """11. amount=0 → 400."""
        self._login(self.admin_user)
        resp = self.api.post(URL, _billable_payload(self.budget_client.pk, code='zero-amt-01', amount='0'), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('amount', str(resp.data))

    def test_amount_negative_returns_400(self):
        """11b. amount<0 → 400."""
        self._login(self.admin_user)
        resp = self.api.post(URL, _billable_payload(self.budget_client.pk, code='neg-amt-01', amount='-100'), format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('amount', str(resp.data))

    def test_period_end_before_start_returns_400(self):
        """12. period_end < period_start → 400."""
        self._login(self.admin_user)
        payload = _billable_payload(
            self.budget_client.pk, code='date-err-01',
            period_start='2025-04-01', period_end='2025-03-01',
        )
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('period_end', str(resp.data))

    def test_cross_org_client_rejected(self):
        """13. client from different org → 400."""
        self._login(self.admin_user)
        payload = _billable_payload(self.budget_client2.pk, code='xorg-cl-01')
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', str(resp.data))

    def test_cross_org_department_rejected(self):
        """15. department from different org → 400."""
        self._login(self.admin_user)
        payload = _nonbillable_payload(self.dept2.pk, code='xorg-dept-01')
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('department', str(resp.data))

    def test_cross_org_site_rejected_on_create(self):
        """14. site from different org → 400."""
        n_site2 = _node(self.org2, 'bgt-site2', 'site', self.n_cl2, 2, 'bgt-org2/bgt-cl2/bgt-site2')
        site2 = _site(self.org2, self.budget_client2, 'bgt-site2', n_site2)
        self._login(self.admin_user)
        payload = _billable_payload(self.budget_client.pk, code='xorg-site-01', site=site2.pk)
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', str(resp.data))

    def test_non_superuser_create_forces_actor_org(self):
        """25. non-superuser cannot set a different org."""
        self._login(self.admin_user)
        payload = _billable_payload(
            self.budget_client.pk, code='force-org-01', org=self.org2.pk
        )
        resp = self.api.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org.pk)

    def test_create_response_includes_denormalized_fields(self):
        """24. create response includes client_name, created_by_username etc."""
        self._login(self.admin_user)
        resp = self.api.post(URL, _billable_payload(self.budget_client.pk, code='dn-01'), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['client_name'], self.budget_client.name)
        self.assertEqual(resp.data['created_by_username'], self.admin_user.username)
        self.assertIn('created_at', resp.data)


# ─── Update / delete ──────────────────────────────────────────────────────────

class TestBudgetUpdateDelete(BudgetTestBase):

    def _make_budget(self, code='upd-01'):
        return BudgetPlan.objects.create(
            org=self.org, name='Update Me', code=code,
            budget_nature='billable', budget_type='manpower',
            client=self.budget_client, period_start=datetime.date(2025, 4, 1),
            amount='100000.00', status='draft',
        )

    def test_budget_update_patches_amount_and_status(self):
        """16. PATCH amount/status → 200."""
        bp = self._make_budget('upd-amt-01')
        self._login(self.admin_user)
        resp = self.api.patch(f'{URL}{bp.pk}/', {'amount': '200000.00', 'status': 'active'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'active')
        self.assertEqual(resp.data['amount'], '200000.00')

    def test_budget_delete_soft_deactivates(self):
        """17. DELETE → 204, is_active=False, status=inactive."""
        bp = self._make_budget('del-01')
        self._login(self.admin_user)
        resp = self.api.delete(f'{URL}{bp.pk}/')
        self.assertEqual(resp.status_code, 204)
        bp.refresh_from_db()
        self.assertFalse(bp.is_active)
        self.assertEqual(bp.status, 'inactive')

    def test_update_response_includes_read_fields(self):
        """PATCH returns read serializer with denormalized fields."""
        bp = self._make_budget('upd-read-01')
        self._login(self.admin_user)
        resp = self.api.patch(f'{URL}{bp.pk}/', {'notes': 'Updated'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('client_name', resp.data)
        self.assertIn('updated_at', resp.data)

    def test_non_superuser_update_cannot_change_org(self):
        """Non-superuser PATCH with org=other_org is silently ignored; budget stays on actor.org."""
        bp = self._make_budget('upd-org-01')
        self._login(self.admin_user)
        resp = self.api.patch(f'{URL}{bp.pk}/', {'org': self.org2.pk, 'notes': 'org-hijack'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        bp.refresh_from_db()
        self.assertEqual(bp.org_id, self.org.pk)

    def test_cross_org_site_rejected_on_update(self):
        """PATCH that sets site from a different org → 400."""
        bp = self._make_budget('upd-xsite-01')
        n_site2 = _node(self.org2, 'bgt-upd-site2', 'site', self.n_cl2, 2, 'bgt-org2/bgt-cl2/bgt-upd-site2')
        site2 = _site(self.org2, self.budget_client2, 'bgt-upd-site2', n_site2)
        self._login(self.admin_user)
        resp = self.api.patch(f'{URL}{bp.pk}/', {'site': site2.pk}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', str(resp.data))


# ─── Scoping / filter / search ────────────────────────────────────────────────

class TestBudgetScopingAndFilter(BudgetTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.bp_org1 = BudgetPlan.objects.create(
            org=cls.org, name='Org1 Budget', code='scope-org1',
            budget_nature='billable', budget_type='manpower',
            client=cls.budget_client, period_start=datetime.date(2025, 4, 1),
            amount='100000.00', status='active',
        )
        cls.bp_org2 = BudgetPlan.objects.create(
            org=cls.org2, name='Org2 Budget', code='scope-org2',
            budget_nature='non_billable', budget_type='hiring',
            department=cls.dept2, period_start=datetime.date(2025, 4, 1),
            amount='50000.00', status='draft',
        )

    def test_non_superuser_sees_only_own_org(self):
        """18. non-superuser sees only own org budgets."""
        self._login(self.admin_user)
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data['results']}
        self.assertIn(self.bp_org1.pk, ids)
        self.assertNotIn(self.bp_org2.pk, ids)

    def test_superuser_sees_all_orgs(self):
        """19. superuser sees budgets from all orgs."""
        self._login(self.superuser)
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data['results']}
        self.assertIn(self.bp_org1.pk, ids)
        self.assertIn(self.bp_org2.pk, ids)

    def test_filter_by_budget_nature(self):
        """20. filter by budget_nature."""
        self._login(self.admin_user)
        resp = self.api.get(f'{URL}?budget_nature=billable')
        self.assertEqual(resp.status_code, 200)
        for r in resp.data['results']:
            self.assertEqual(r['budget_nature'], 'billable')

    def test_filter_by_status(self):
        """21. filter by status."""
        self._login(self.admin_user)
        resp = self.api.get(f'{URL}?status=active')
        self.assertEqual(resp.status_code, 200)
        for r in resp.data['results']:
            self.assertEqual(r['status'], 'active')

    def test_search_by_name(self):
        """22. search by name."""
        self._login(self.admin_user)
        resp = self.api.get(f'{URL}?search=Org1')
        self.assertEqual(resp.status_code, 200)
        names = [r['name'] for r in resp.data['results']]
        self.assertIn('Org1 Budget', names)

    def test_read_response_denormalized_fields(self):
        """23. list response includes client_name, department_name."""
        self._login(self.admin_user)
        resp = self.api.get(f'{URL}{self.bp_org1.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['client_name'], self.budget_client.name)


# ─── Seed / capability tests ──────────────────────────────────────────────────

class TestBudgetClientCommercials(BudgetTestBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.client_role = _role(cls.org, 'client_admin')
        bootstrap_role_permissions(cls.client_role)
        cls.client_user = _user('bgt_client_admin', org=cls.org)
        _assign(cls.client_user, cls.client_role, cls.n_cl)

        cls.job_role = JobRole.objects.create(
            org=cls.org,
            name='Electrician',
            code='electrician',
            skill_category='skilled',
        )
        cls.lead = SalesLead.objects.create(
            org=cls.org,
            client_name=cls.budget_client.name,
            client_email='buyer@example.com',
        )
        cls.lead_site = SalesLeadSite.objects.create(
            lead=cls.lead,
            site_name=cls.site.name,
            is_active=True,
        )
        cls.role_requirement = SalesRoleRequirement.objects.create(
            lead=cls.lead,
            site=cls.lead_site,
            job_role=cls.job_role,
            manpower_count=2,
            approved_by_operations=True,
        )
        cls.proposal = ProposalVersion.objects.create(
            lead=cls.lead,
            version_number=1,
            status='client_approved',
            internal_approval_status='approved',
            client_approval_status='approved',
            is_final_approved_version=True,
            grand_total='100000.00',
            subtotal_amount='90000.00',
            management_fee_amount='5000.00',
            gst_amount='5000.00',
            manpower_total=2,
            management_fee_percent='5.00',
            gst_applicable=True,
            validity_days=30,
            sales_remarks='internal sales note',
        )
        ProposalBudgetLine.objects.create(
            proposal_version=cls.proposal,
            site=cls.lead_site,
            role_requirement=cls.role_requirement,
            job_role=cls.job_role,
            service_category='Technical',
            description='Electrician',
            manpower_count=2,
            unit_cost='45000.00',
            total_cost='90000.00',
            remarks='internal budget note',
            sort_order=1,
        )
        ProposalBreakupLine.objects.create(
            proposal_version=cls.proposal,
            site=cls.lead_site,
            role_requirement=cls.role_requirement,
            job_role=cls.job_role,
            component_name='Basic',
            component_type='earning',
            amount='19000.00',
            remarks='internal breakup note',
            sort_order=101,
        )
        cls.budget = BudgetPlan.objects.create(
            org=cls.org,
            name='Approved Client Budget',
            code='approved-client-commercial',
            budget_nature='billable',
            budget_type='manpower',
            client=cls.budget_client,
            site=cls.site,
            period_start=datetime.date(2026, 1, 1),
            amount='100000.00',
            currency='INR',
            status='active',
            is_active=True,
            source_type='sales_conversion',
            source_sales_lead=cls.lead,
            source_proposal_version=cls.proposal,
        )
        cls.reserved_mrf = ManpowerRequest.objects.create(
            org=cls.org,
            site=cls.site,
            requested_by=cls.client_user,
            requested_by_type='client',
            mrf_type='new_hiring',
            status='submitted',
            billing_type='billable',
            budget_plan=cls.budget,
        )
        cls.committed_mrf = ManpowerRequest.objects.create(
            org=cls.org,
            site=cls.site,
            requested_by=cls.client_user,
            requested_by_type='client',
            mrf_type='new_hiring',
            status='approved',
            billing_type='billable',
            budget_plan=cls.budget,
        )
        BudgetReservation.objects.create(
            org=cls.org,
            budget_plan=cls.budget,
            mrf=cls.reserved_mrf,
            amount='12000.00',
            status='reserved',
        )
        BudgetReservation.objects.create(
            org=cls.org,
            budget_plan=cls.budget,
            mrf=cls.committed_mrf,
            amount='8000.00',
            status='committed',
        )

    def test_client_user_can_read_approved_budget_commercials(self):
        self._login(self.client_user)

        resp = self.api.get(f'{URL}{self.budget.pk}/client-commercials/')

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['budget']['id'], self.budget.pk)
        self.assertEqual(resp.data['budget']['reserved_amount'], '12000.00')
        self.assertEqual(resp.data['budget']['committed_amount'], '8000.00')
        self.assertEqual(resp.data['budget']['available_amount'], '80000.00')
        self.assertEqual(resp.data['proposal']['id'], self.proposal.pk)
        self.assertEqual(resp.data['proposal']['grand_total'], '100000.00')
        self.assertEqual(len(resp.data['budget_lines']), 1)
        self.assertEqual(resp.data['budget_lines'][0]['job_role_name'], 'Electrician')
        self.assertEqual(resp.data['budget_lines'][0]['unit_cost'], '45000.00')
        self.assertNotIn('remarks', resp.data['budget_lines'][0])
        self.assertEqual(len(resp.data['breakup_lines']), 1)
        self.assertEqual(resp.data['breakup_lines'][0]['component_name'], 'Basic')
        self.assertNotIn('remarks', resp.data['breakup_lines'][0])
        self.assertNotIn('internal_approval_status', resp.data['proposal'])
        self.assertNotIn('sales_remarks', resp.data['proposal'])

    def test_non_final_proposal_lines_are_hidden_from_client_commercials(self):
        self.proposal.is_final_approved_version = False
        self.proposal.save(update_fields=['is_final_approved_version'])
        self._login(self.client_user)

        resp = self.api.get(f'{URL}{self.budget.pk}/client-commercials/')

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['proposal'])
        self.assertEqual(resp.data['budget_lines'], [])
        self.assertEqual(resp.data['breakup_lines'], [])


class TestBudgetSeedCapabilities(TestCase):

    def test_all_capabilities_includes_budget_update_delete(self):
        """26. ALL_CAPABILITIES includes budget.update and budget.delete."""
        from apps.access.capabilities import ALL_CAPABILITIES
        self.assertIn('budget.update', ALL_CAPABILITIES)
        self.assertIn('budget.delete', ALL_CAPABILITIES)

    def test_finance_role_capabilities_include_budget_crud(self):
        """27. finance ROLE_CAPABILITIES includes budget read/create/update/delete."""
        from apps.access.capabilities import ROLE_CAPABILITIES
        finance_caps = ROLE_CAPABILITIES.get('finance', [])
        self.assertIn('budget.read', finance_caps)
        self.assertIn('budget.create', finance_caps)
        self.assertIn('budget.update', finance_caps)
        self.assertIn('budget.delete', finance_caps)

    def test_finance_manager_role_capabilities_include_budget_crud(self):
        """finance_manager ROLE_CAPABILITIES includes full budget CRUD."""
        from apps.access.capabilities import ROLE_CAPABILITIES
        fm_caps = ROLE_CAPABILITIES.get('finance_manager', [])
        self.assertIn('budget.update', fm_caps)
        self.assertIn('budget.delete', fm_caps)

    def test_seed_creates_budget_permissions(self):
        """Seed _seed_permissions creates Permission rows for budget.update/delete."""
        from django.core.management import call_command
        from io import StringIO
        from apps.access.models import Permission

        # Run just the permission seeding portion
        from apps.core.management.commands.seed_foundation import Command
        cmd = Command()
        cmd.stdout = type('F', (), {'write': lambda self, x: None})()
        perms = cmd._seed_permissions()

        self.assertIn('budget.update', perms)
        self.assertIn('budget.delete', perms)
        self.assertTrue(Permission.objects.filter(code='budget.update').exists())
        self.assertTrue(Permission.objects.filter(code='budget.delete').exists())

    def test_seed_maps_finance_role_budget_permissions(self):
        """Seed _seed_role_permissions maps budget.update/delete to finance role."""
        from apps.access.models import AccessRole, AccessRolePermission
        from apps.core.models import Organization
        from apps.core.management.commands.seed_foundation import Command

        org = Organization.objects.create(name='Seed Test Org', code='seed-bgt-test')
        finance_role, _ = AccessRole.objects.get_or_create(
            org=org, code='finance', defaults={'name': 'finance'}
        )

        cmd = Command()
        cmd.stdout = type('F', (), {'write': lambda self, x: None})()
        perms = cmd._seed_permissions()
        cmd._seed_role_permissions({'finance': finance_role}, perms)

        db_caps = set(
            AccessRolePermission.objects
            .filter(role=finance_role)
            .values_list('permission__code', flat=True)
        )
        self.assertIn('budget.update', db_caps)
        self.assertIn('budget.delete', db_caps)

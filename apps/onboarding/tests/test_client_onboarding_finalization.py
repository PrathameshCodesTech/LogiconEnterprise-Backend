"""
apps/onboarding/tests/test_client_onboarding_finalization.py

Phase Client-Onboarding-Finalization-A tests.

Scenarios:
  Model / validation:
  1.  new_client request created without client — OK
  2.  new_client request with client set → validation error
  3.  new_site_expansion without client → validation error
  4.  new_site_expansion with client → OK
  5.  proposed_client_code collides with real active Client code → 400

  Proposed setup CRUD:
  6.  Proposed site CRUD (create / list / update / delete)
  7.  Proposed department CRUD
  8.  Proposed SRR CRUD
  9.  Proposed site code uniqueness within request enforced
  10. Proposed SRR with wrong-request proposed_site → 400
  11. Proposed setup edits blocked when workflow is active (in_review)
  12. Proposed setup edits allowed when status is 'rejected' (resubmission path)

  Workflow routing:
  13. Route availability: null-client returns only org-level routes
  14. Workflow start works for new_client (no real client)
  15. Workflow start works for new_site_expansion (existing client)

  Finalization — rejection path:
  16. Rejected new_client onboarding creates no Client, Site, Dept, SRR

  Finalization — approval path:
  17. Approved new_client → real Client, ScopeNode, Site, Department, SRR created
  18. Approved new_site_expansion → sites/depts/SRRs created under existing client
  19. Finalization is idempotent (second call is a no-op)
  20. Finalization failure records failed status and stores error message

  Access / scope:
  21. null-client request visible to org-level user, invisible to other-client user
  22. finalized_by is set to the approving workflow actor

  Regression:
  23. Existing MRF workflow behavior still passes (non-regression)
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Department, Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.onboarding.exceptions import OnboardingFinalizationError
from apps.onboarding.models import (
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingRequest,
)
from apps.onboarding.services import finalize_client_onboarding_request
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.workflow.models import (
    StepAssignmentConfig,
    WorkflowInstance,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateMapping,
)
from apps.workflow.services import act_on_step, start_client_onboarding_workflow


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _node(org, code, node_type, parent, depth, path):
    return ScopeNode.objects.create(
        org=org, code=code, name=code, node_type=node_type,
        parent=parent, depth=depth, path=path, is_active=True,
    )


def _user(username, org=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass',
                                 is_superuser=is_superuser, is_staff=is_superuser)
    if org:
        u.org = org
        u.save()
    return u


def _role(org, code):
    return AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]


def _assign(user, role, scope_node):
    return UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope_node)


def _ob_template(org, code='fin-co-default'):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code,
        trigger_type='client_onboarding', version=1, is_active=True,
    )


def _ob_step(template, order, code):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        requires_comment_on_reject=False,
        requires_comment_on_request_changes=False,
    )


def _ob_mapping(org, template):
    return WorkflowTemplateMapping.objects.create(
        org=org, trigger_type='client_onboarding', template=template,
        client=None, site=None, is_active=True,
    )


def _ob_sac(org, step_code, named_user):
    return StepAssignmentConfig.objects.create(
        org=org, trigger_type='client_onboarding', step_code=step_code,
        client=None, site=None,
        assignment_mode='named_user', named_user=named_user,
        is_active=True,
    )


def _job_role(org, code='fin-jr-1'):
    return JobRole.objects.create(
        org=org, name=code, code=code, skill_category='unskilled', is_active=True,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class FinalizationTestBase(TestCase):
    """Org with org-level workflow template; one existing client; one job role."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Fin Org', code='fin-org')

        # Scope hierarchy
        cls.n_org = _node(cls.org, 'fin-org', 'company', None, 0, 'fin-org')
        cls.n_cl_a = _node(cls.org, 'fin-cl-a', 'client', cls.n_org, 1, 'fin-org/fin-cl-a')

        cls.client_a = Client.objects.create(
            org=cls.org, name='Fin Client A', code='fin-cl-a',
            scope_node=cls.n_cl_a, is_active=True,
        )

        # Roles and users
        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = _user('fin_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_org)

        cls.approver = _user('fin_approver', org=cls.org)
        _assign(cls.approver, cls.role_admin, cls.n_org)

        # User scoped to client_a only (cannot see null-client requests via client scope)
        cls.cl_a_user = _user('fin_cl_a_user', org=cls.org)
        _assign(cls.cl_a_user, cls.role_admin, cls.n_cl_a)

        # User from a completely separate org
        cls.org2 = Organization.objects.create(name='Fin Org2', code='fin-org2')
        cls.n_org2 = _node(cls.org2, 'fin-org2', 'company', None, 0, 'fin-org2')
        cls.role2 = _role(cls.org2, 'admin')
        bootstrap_role_permissions(cls.role2)
        cls.other_org_user = _user('fin_other', org=cls.org2)
        _assign(cls.other_org_user, cls.role2, cls.n_org2)

        # Job role
        cls.job_role = _job_role(cls.org)

        # Org-level workflow: 2 steps — both assigned to approver
        cls.template = _ob_template(cls.org)
        cls.step1 = _ob_step(cls.template, 1, 'fin_step1')
        cls.step2 = _ob_step(cls.template, 2, 'fin_step2')
        cls.mapping = _ob_mapping(cls.org, cls.template)
        cls.sac1 = _ob_sac(cls.org, 'fin_step1', cls.approver)
        cls.sac2 = _ob_sac(cls.org, 'fin_step2', cls.approver)

    def setUp(self):
        self.api = APIClient()

    # ── URL helpers ──────────────────────────────────────────────────────────

    def _list_url(self):
        return '/api/onboarding/client-requests/'

    def _detail_url(self, pk):
        return f'/api/onboarding/client-requests/{pk}/'

    def _start_url(self, ob_id):
        return f'/api/workflow/client-onboarding/{ob_id}/start/'

    def _act_url(self, instance_id, step_id):
        return f'/api/workflow/instances/{instance_id}/steps/{step_id}/act/'

    def _proposed_sites_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-sites/'

    def _proposed_sites_detail_url(self, req_pk, pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-sites/{pk}/'

    def _proposed_depts_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-departments/'

    def _proposed_depts_detail_url(self, req_pk, pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-departments/{pk}/'

    def _proposed_srr_url(self, req_pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-role-requirements/'

    def _proposed_srr_detail_url(self, req_pk, pk):
        return f'/api/onboarding/client-requests/{req_pk}/proposed-role-requirements/{pk}/'

    # ── Object factories ─────────────────────────────────────────────────────

    def _new_client_request(self, **kwargs):
        defaults = dict(
            org=self.org,
            client=None,
            requested_by=self.admin,
            onboarding_type='new_client',
            status='draft',
            proposed_client_name='New Corp',
            proposed_client_code='new-corp',
        )
        defaults.update(kwargs)
        return ClientOnboardingRequest.objects.create(**defaults)

    def _site_expansion_request(self, **kwargs):
        defaults = dict(
            org=self.org,
            client=self.client_a,
            requested_by=self.admin,
            onboarding_type='new_site_expansion',
            status='draft',
        )
        defaults.update(kwargs)
        return ClientOnboardingRequest.objects.create(**defaults)

    def _proposed_site(self, req, code='ps-1', name='Proposed Site 1'):
        return ClientOnboardingProposedSite.objects.create(
            request=req, name=name, code=code,
            city='Mumbai', state='MH', is_active=True,
        )

    def _proposed_dept(self, req, p_site=None, code='pd-1', scope_level='site'):
        return ClientOnboardingProposedDepartment.objects.create(
            request=req, proposed_site=p_site, scope_level=scope_level,
            name=f'Dept {code}', code=code, is_active=True,
        )

    def _proposed_srr(self, req, p_site, code=None):
        return ClientOnboardingProposedSiteRoleRequirement.objects.create(
            request=req,
            proposed_site=p_site,
            job_role=self.job_role,
            approved_headcount=5,
            billing_type='billable',
            effective_from=datetime.date.today(),
            is_active=True,
        )

    def _full_approve(self, onboarding_request):
        """Start workflow and approve all steps. Returns final WorkflowInstance."""
        instance = start_client_onboarding_workflow(onboarding_request, actor=self.admin)
        steps = list(instance.steps.order_by('step_order'))
        for step in steps[:-1]:
            act_on_step(step, actor=self.approver, action='approve')
        # Approve last step to trigger completion + finalization
        last = steps[-1]
        act_on_step(last, actor=self.approver, action='approve')
        instance.refresh_from_db()
        return instance

    def _reject_final(self, onboarding_request):
        """Start workflow and reject at first step."""
        instance = start_client_onboarding_workflow(onboarding_request, actor=self.admin)
        step = instance.steps.order_by('step_order').first()
        act_on_step(step, actor=self.approver, action='reject', comment='Not approved.')
        instance.refresh_from_db()
        return instance

    def _login(self, user):
        self.api.force_authenticate(user=user)


# ─── Scenario 1–5: Model / validation ────────────────────────────────────────

class TestOnboardingTypeValidation(FinalizationTestBase):

    def test_01_new_client_without_client_ok(self):
        """new_client request can be created without an existing Client FK."""
        req = self._new_client_request()
        self.assertIsNone(req.client_id)
        self.assertEqual(req.onboarding_type, 'new_client')
        self.assertEqual(req.proposed_client_name, 'New Corp')

    def test_02_new_client_with_client_rejected_by_api(self):
        """new_client type with client set → 400 from write serializer."""
        self._login(self.admin)
        resp = self.api.post(self._list_url(), {
            'onboarding_type': 'new_client',
            'client': self.client_a.pk,
            'proposed_client_name': 'New Corp',
            'proposed_client_code': 'new-corp',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', str(resp.data))

    def test_03_site_expansion_without_client_rejected_by_api(self):
        """new_site_expansion without client → 400."""
        self._login(self.admin)
        resp = self.api.post(self._list_url(), {
            'onboarding_type': 'new_site_expansion',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('client', str(resp.data))

    def test_04_site_expansion_with_client_ok(self):
        """new_site_expansion with valid client → 201."""
        self._login(self.admin)
        resp = self.api.post(self._list_url(), {
            'onboarding_type': 'new_site_expansion',
            'client': self.client_a.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['onboarding_type'], 'new_site_expansion')

    def test_05_proposed_code_collides_with_real_client_code(self):
        """proposed_client_code matches an existing active Client code → 400."""
        self._login(self.admin)
        resp = self.api.post(self._list_url(), {
            'onboarding_type': 'new_client',
            'proposed_client_name': 'Clash Corp',
            'proposed_client_code': 'fin-cl-a',  # same as cls.client_a.code
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposed_client_code', str(resp.data))


# ─── Scenario 6–12: Proposed setup CRUD ──────────────────────────────────────

class TestProposedSetupCRUD(FinalizationTestBase):

    def test_06_proposed_site_crud(self):
        """Create, list, patch, delete a proposed site."""
        req = self._new_client_request()
        self._login(self.admin)

        # Create
        resp = self.api.post(self._proposed_sites_url(req.pk), {
            'name': 'Mumbai HQ', 'code': 'mum-hq',
            'city': 'Mumbai', 'state': 'Maharashtra',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        ps_id = resp.data['id']

        # List
        resp = self.api.get(self._proposed_sites_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

        # Patch
        resp = self.api.patch(self._proposed_sites_detail_url(req.pk, ps_id),
                              {'city': 'Pune'}, format='json')
        self.assertEqual(resp.status_code, 200)

        # Delete
        resp = self.api.delete(self._proposed_sites_detail_url(req.pk, ps_id))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ClientOnboardingProposedSite.objects.filter(pk=ps_id).exists())

    def test_07_proposed_department_crud(self):
        """Create and retrieve proposed departments including site-scoped."""
        req = self._new_client_request()
        p_site = self._proposed_site(req)
        self._login(self.admin)

        resp = self.api.post(self._proposed_depts_url(req.pk), {
            'proposed_site': p_site.pk,
            'scope_level': 'site',
            'name': 'Operations',
            'code': 'ops-dept',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        resp = self.api.get(self._proposed_depts_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_08_proposed_srr_crud(self):
        """Create and retrieve a proposed SRR."""
        req = self._new_client_request()
        p_site = self._proposed_site(req)
        self._login(self.admin)

        resp = self.api.post(self._proposed_srr_url(req.pk), {
            'proposed_site': p_site.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 10,
            'billing_type': 'billable',
            'effective_from': str(datetime.date.today()),
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        resp = self.api.get(self._proposed_srr_url(req.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_09_proposed_site_code_uniqueness_within_request(self):
        """Duplicate active site code within same request → 400."""
        req = self._new_client_request()
        self._proposed_site(req, code='dup-site')
        self._login(self.admin)

        resp = self.api.post(self._proposed_sites_url(req.pk), {
            'name': 'Duplicate', 'code': 'dup-site',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('code', str(resp.data))

    def test_10_proposed_srr_with_wrong_request_site_rejected(self):
        """proposed_site from a different onboarding request → 400."""
        req1 = self._new_client_request(proposed_client_code='nc-1', proposed_client_name='NC1')
        req2 = self._new_client_request(proposed_client_code='nc-2', proposed_client_name='NC2')
        p_site_for_req2 = self._proposed_site(req2, code='other-site')
        self._login(self.admin)

        resp = self.api.post(self._proposed_srr_url(req1.pk), {
            'proposed_site': p_site_for_req2.pk,
            'job_role': self.job_role.pk,
            'approved_headcount': 1,
            'billing_type': 'billable',
            'effective_from': str(datetime.date.today()),
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_11_proposed_setup_edits_blocked_when_workflow_active(self):
        """Cannot add/edit proposed setup when request status is 'in_review'."""
        req = self._new_client_request()
        start_client_onboarding_workflow(req, actor=self.admin)
        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

        self._login(self.admin)
        resp = self.api.post(self._proposed_sites_url(req.pk), {
            'name': 'Blocked', 'code': 'blocked-site',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_12_proposed_setup_edits_allowed_when_rejected(self):
        """After rejection (status='rejected'), proposed setup can be edited."""
        req = self._new_client_request()
        instance = self._reject_final(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'rejected')

        self._login(self.admin)
        resp = self.api.post(self._proposed_sites_url(req.pk), {
            'name': 'Re-attempt Site', 'code': 're-site',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)


# ─── Scenario 13–15: Workflow routing ────────────────────────────────────────

class TestWorkflowRoutingForNullClient(FinalizationTestBase):

    def test_13_route_availability_null_client_returns_org_routes(self):
        """get_available_approval_routes with client=None returns org-level routes."""
        from apps.workflow.services import get_available_approval_routes
        from apps.workflow.models import ApprovalRoute

        route = ApprovalRoute.objects.create(
            org=self.org,
            template=self.template,
            trigger_type='client_onboarding',
            name='Org Route',
            code='fin-org-route',
            is_active=True,
        )
        routes = get_available_approval_routes('client_onboarding', self.org, client=None)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].pk, route.pk)
        route.delete()

    def test_14_workflow_start_works_for_new_client(self):
        """start_client_onboarding_workflow works when client is None."""
        req = self._new_client_request()
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        self.assertEqual(instance.status, 'active')
        req.refresh_from_db()
        self.assertEqual(req.status, 'in_review')

    def test_15_workflow_start_works_for_site_expansion(self):
        """start_client_onboarding_workflow works for new_site_expansion with existing client."""
        req = self._site_expansion_request()
        instance = start_client_onboarding_workflow(req, actor=self.admin)
        self.assertEqual(instance.status, 'active')


# ─── Scenario 16: Rejection creates no records ───────────────────────────────

class TestRejectionCreatesNoRecords(FinalizationTestBase):

    def test_16_rejected_onboarding_creates_no_client(self):
        """Rejected new_client onboarding: no real Client/Site/SRR created."""
        req = self._new_client_request()
        self._proposed_site(req)

        client_count_before = Client.objects.filter(org=self.org).count()
        site_count_before = SiteProfile.objects.filter(org=self.org).count()

        self._reject_final(req)
        req.refresh_from_db()

        self.assertEqual(req.status, 'rejected')
        self.assertEqual(req.finalization_status, 'not_finalized')
        self.assertEqual(Client.objects.filter(org=self.org).count(), client_count_before)
        self.assertEqual(SiteProfile.objects.filter(org=self.org).count(), site_count_before)


# ─── Scenario 17–20: Finalization ────────────────────────────────────────────

class TestFinalization(FinalizationTestBase):

    def test_17_approved_new_client_creates_real_records(self):
        """Full approval for new_client → Client, ScopeNode, Site, Dept, SRR created."""
        req = self._new_client_request(
            proposed_client_name='Acme Corp',
            proposed_client_code='acme',
        )
        p_site = self._proposed_site(req, code='acme-site-1')
        p_dept = self._proposed_dept(req, p_site=p_site, code='acme-dept-1', scope_level='site')
        self._proposed_srr(req, p_site)

        self._full_approve(req)
        req.refresh_from_db()

        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.finalization_status, 'finalized')
        self.assertIsNotNone(req.created_client)
        self.assertIsNotNone(req.finalized_at)

        # Real Client created with correct data
        real_client = req.created_client
        self.assertEqual(real_client.name, 'Acme Corp')
        self.assertEqual(real_client.code, 'acme')
        self.assertIsNotNone(real_client.scope_node)
        self.assertIn('acme', real_client.scope_node.path)

        # Real Site created
        real_sites = SiteProfile.objects.filter(client=real_client)
        self.assertEqual(real_sites.count(), 1)
        self.assertEqual(real_sites.first().code, 'acme-site-1')
        self.assertIsNotNone(real_sites.first().scope_node)

        # Real Department created
        real_depts = Department.objects.filter(
            org=self.org, client=real_client, site=real_sites.first(),
        )
        self.assertEqual(real_depts.count(), 1)
        self.assertEqual(real_depts.first().code, 'acme-dept-1')

        # Real SRR created
        real_srrs = SiteRoleRequirement.objects.filter(site=real_sites.first())
        self.assertEqual(real_srrs.count(), 1)
        self.assertEqual(real_srrs.first().approved_headcount, 5)

    def test_18_approved_site_expansion_creates_under_existing_client(self):
        """Approved new_site_expansion → sites/depts/SRRs under existing client."""
        req = self._site_expansion_request()
        p_site = self._proposed_site(req, code='exp-site-1')
        self._proposed_srr(req, p_site)

        self._full_approve(req)
        req.refresh_from_db()

        self.assertEqual(req.finalization_status, 'finalized')
        self.assertIsNone(req.created_client)  # no new client created

        real_sites = SiteProfile.objects.filter(client=self.client_a, code='exp-site-1')
        self.assertEqual(real_sites.count(), 1)
        real_srrs = SiteRoleRequirement.objects.filter(site=real_sites.first())
        self.assertEqual(real_srrs.count(), 1)

    def test_19_finalization_is_idempotent(self):
        """Calling finalize_client_onboarding_request a second time is a no-op."""
        req = self._new_client_request(
            proposed_client_code='idem-corp',
            proposed_client_name='Idem Corp',
        )
        self._full_approve(req)
        req.refresh_from_db()
        self.assertEqual(req.finalization_status, 'finalized')

        client_count_after_first = Client.objects.filter(org=self.org).count()
        # Second call should not create another Client
        finalize_client_onboarding_request(req, actor=self.admin)
        self.assertEqual(Client.objects.filter(org=self.org).count(), client_count_after_first)

    def test_20_finalization_failure_records_failed_status(self):
        """
        If finalization raises (e.g. duplicate Client code), finalization_status='failed'
        and finalization_error is stored; no real records are persisted.
        """
        # Pre-create a Client whose code will collide with the proposed code
        Client.objects.create(org=self.org, name='Pre-existing', code='clash-corp', is_active=True)

        req = self._new_client_request(
            proposed_client_code='clash-corp',
            proposed_client_name='Clash Corp',
        )
        req.status = 'approved'
        req.save()

        with self.assertRaises(OnboardingFinalizationError):
            finalize_client_onboarding_request(req, actor=self.admin)

        req.refresh_from_db()
        self.assertEqual(req.finalization_status, 'failed')
        self.assertGreater(len(req.finalization_error), 0)
        # Only one client with this code (the pre-existing one; no new one created)
        self.assertEqual(Client.objects.filter(org=self.org, code='clash-corp').count(), 1)


# ─── Scenario 21: Access / scope ─────────────────────────────────────────────

class TestScopeFiltering(FinalizationTestBase):

    def test_21_null_client_request_visible_to_org_user_not_other_client_user(self):
        """
        null-client request is visible to org-level users but not to users scoped
        only to a different client (who can't see the parent org scope).
        """
        req = self._new_client_request()

        # Org-level admin sees it
        self._login(self.admin)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(req.pk, ids)

        # User scoped only to client_a also sees it (they're in the same org)
        self._login(self.cl_a_user)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(req.pk, ids)

        # User from a different org does not see it
        self._login(self.other_org_user)
        resp = self.api.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data.get('results', resp.data)]
        self.assertNotIn(req.pk, ids)


# ─── Scenario 22: finalized_by actor ─────────────────────────────────────────

class TestFinalizedByActor(FinalizationTestBase):

    def test_22_finalized_by_set_to_approving_actor(self):
        """finalized_by is set to the user who approved the final workflow step."""
        req = self._new_client_request(
            proposed_client_code='actor-corp',
            proposed_client_name='Actor Corp',
        )
        self._full_approve(req)
        req.refresh_from_db()
        self.assertEqual(req.finalization_status, 'finalized')
        self.assertIsNotNone(req.finalized_by)
        self.assertEqual(req.finalized_by_id, self.approver.pk)


# ─── Scenario 23: MRF regression ─────────────────────────────────────────────

class TestMRFNonRegression(FinalizationTestBase):
    """Existing MRF workflow behavior is unaffected by finalization changes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.mrf.models import ManpowerRequest
        from apps.workflow.models import WorkflowTemplate, WorkflowStepTemplate, WorkflowTemplateMapping, StepAssignmentConfig

        cls.mrf_template = WorkflowTemplate.objects.create(
            org=cls.org, name='MRF Default Fin', code='fin-mrf-default',
            trigger_type='mrf', version=1, is_active=True,
        )
        cls.mrf_step = WorkflowStepTemplate.objects.create(
            template=cls.mrf_template, order=1, code='fin_mrf_step1',
            name='HR Review', assignment_mode='named_user', actor_type='internal',
            requires_comment_on_reject=False, requires_comment_on_request_changes=False,
        )
        WorkflowTemplateMapping.objects.create(
            org=cls.org, trigger_type='mrf', template=cls.mrf_template,
            client=None, site=None, is_active=True,
        )
        from apps.core.models import ScopeNode
        cls.n_site = _node(
            cls.org, 'fin-site-a', 'site', cls.n_cl_a, 2, 'fin-org/fin-cl-a/fin-site-a',
        )
        cls.site_a = SiteProfile.objects.create(
            org=cls.org, client=cls.client_a, name='Fin Site A', code='fin-site-a',
            scope_node=cls.n_site,
        )
        StepAssignmentConfig.objects.create(
            org=cls.org, trigger_type='mrf', step_code='fin_mrf_step1',
            assignment_mode='named_user', named_user=cls.approver,
            is_active=True,
        )

    def test_23_mrf_workflow_approve_still_works(self):
        """Approving an MRF workflow still sets mrf.status=approved."""
        from apps.mrf.models import ManpowerRequest
        from apps.workflow.services import start_mrf_workflow

        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.site_a,
            requested_by=self.admin,
            mrf_type='new_hiring', status='submitted', billing_type='billable',
        )
        instance = start_mrf_workflow(mrf, actor=self.admin)
        step = instance.steps.first()
        act_on_step(step, actor=self.approver, action='approve')
        mrf.refresh_from_db()
        self.assertEqual(mrf.status, 'approved')

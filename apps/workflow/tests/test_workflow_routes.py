"""
apps/workflow/tests/test_workflow_routes.py

Phase Workflow-Route-A — Approval Route Backend tests.

Scenarios:
  Model validation (5):
  1.  ApprovalRoute unique_ar_org_code enforced
  2.  ApprovalRoute.clean: template belongs to different org
  3.  ApprovalRoute.clean: trigger_type mismatch with template
  4.  ApprovalRouteStepAssignment.clean: step_code not in template
  5.  ApprovalRouteStepAssignment.clean: inactive named_user rejected

  get_available_approval_routes (4):
  6.  Site-scoped routes returned for site query
  7.  Client-scoped routes returned for client query
  8.  Org-level routes returned as fallback
  9.  Priority order: site > client > org

  build_approval_route_preview (3):
  10. Preview ok=True when all step assignments valid
  11. Preview ok=False when a step has no assignment
  12. Preview ok=False when assigned user is inactive

  _select_route_or_legacy (4):
  13. No routes → use_legacy=True (legacy path)
  14. Exactly one route → auto-selected
  15. Multiple routes, one default → default auto-selected
  16. Multiple routes, no default → raises WorkflowConfigurationError

  Available routes API (4):
  17. Unauthenticated GET → 401
  18. Missing / bad trigger_type → 400
  19. Valid request returns route list with preview steps
  20. Empty result list when no routes configured

  start_mrf_workflow with route (4):
  21. Start with valid route → 201, approval_route FK and snapshots stored
  22. Start with route from wrong org → WorkflowConfigurationError
  23. POST with nonexistent route_id → 400
  24. Start with route missing a step assignment → WorkflowConfigurationError

  start_client_onboarding_workflow with route (3):
  25. Start with valid route → success, instance.approval_route set
  26. Multiple routes, no default, no route given → 400 (selection required)
  27. Approval route name/code snapshotted on WorkflowInstance

  Regression (1):
  28. Legacy path unchanged when no ApprovalRoute rows exist
"""

from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.mobilisation.models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)
from apps.sites.models import Client, SiteProfile, SiteRoleRequirement
from apps.workflow.exceptions import WorkflowConfigurationError
from apps.workflow.models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    ApprovalRoute, ApprovalRouteStepAssignment,
    WorkflowInstance,
)
from apps.workflow.services import (
    get_available_approval_routes,
    build_approval_route_preview,
    start_mrf_workflow,
    start_client_onboarding_workflow,
)
from apps.workflow.services import _select_route_or_legacy


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


def _template(org, code, trigger_type='mrf'):
    return WorkflowTemplate.objects.create(
        org=org, name=code, code=code, trigger_type=trigger_type, version=1, is_active=True,
    )


def _step(template, order, code, on_approve_next='END'):
    return WorkflowStepTemplate.objects.create(
        template=template, order=order, code=code, name=code,
        assignment_mode='named_user', actor_type='internal',
        on_approve_next=on_approve_next,
        requires_comment_on_reject=True,
        requires_comment_on_request_changes=True,
    )


def _route(org, name, code, template, trigger_type='mrf', client=None, site=None,
           is_default=False, is_active=True):
    return ApprovalRoute.objects.create(
        org=org, name=name, code=code, trigger_type=trigger_type,
        template=template, client=client, site=site,
        is_default=is_default, is_active=is_active,
    )


def _arsa(route, step_code, named_user, is_active=True):
    return ApprovalRouteStepAssignment.objects.create(
        route=route, step_code=step_code,
        assignment_mode='named_user', named_user=named_user,
        is_active=is_active,
    )


def _mrf(org, site, requested_by, status='submitted'):
    return ManpowerRequest.objects.create(
        org=org, site=site, requested_by=requested_by,
        mrf_type='new_hiring', status=status,
        billing_type='billable',
    )


def _onboarding(org, client, requested_by, status='draft'):
    return MobilisationSetupRequest.objects.create(
        org=org, client=client, requested_by=requested_by,
        mobilisation_type='new_client', status=status,
    )


# ─── Base class ───────────────────────────────────────────────────────────────

class RouteTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Route Test Org', code='rt-org')
        cls.org2 = Organization.objects.create(name='Route Other Org', code='rt-org2')

        cls.n_company = _node(cls.org, 'rt-org', 'company', None, 0, 'rt-org')
        cls.n_client = _node(
            cls.org, 'rt-client', 'client', cls.n_company, 1, 'rt-org/rt-client',
        )
        cls.n_site = _node(
            cls.org, 'rt-site', 'site', cls.n_client, 2, 'rt-org/rt-client/rt-site',
        )

        cls.client_obj = Client.objects.create(
            org=cls.org, name='RT Client', code='rt-client', scope_node=cls.n_client,
        )
        cls.site = SiteProfile.objects.create(
            org=cls.org, client=cls.client_obj,
            name='RT Site', code='rt-site', scope_node=cls.n_site,
        )

        cls.role_admin = _role(cls.org, 'admin')
        bootstrap_role_permissions(cls.role_admin)

        cls.admin = _user('rt_admin', org=cls.org)
        _assign(cls.admin, cls.role_admin, cls.n_company)

        cls.approver1 = _user('rt_approver1', org=cls.org)
        cls.approver2 = _user('rt_approver2', org=cls.org)

        cls.template = _template(cls.org, 'rt-mrf-template', trigger_type='mrf')
        cls.step1 = _step(cls.template, 1, 'hr_review', on_approve_next='finance_review')
        cls.step2 = _step(cls.template, 2, 'finance_review', on_approve_next='END')

        cls.ob_template = _template(cls.org, 'rt-ob-template', trigger_type='client_onboarding')
        cls.ob_step1 = _step(cls.ob_template, 1, 'sales_review', on_approve_next='END')

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='RT Guard', code='rt-guard', skill_category='unskilled',
        )
        cls.srr = SiteRoleRequirement.objects.create(
            site=cls.site, job_role=cls.job_role,
            approved_headcount=20, billing_type='billable',
            effective_from=date.today(), is_active=True,
        )

    def _api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c


# ─── 1–5: Model validation ───────────────────────────────────────────────────

class TestApprovalRouteModelValidation(RouteTestBase):

    def test_01_unique_code_per_org_enforced(self):
        """Duplicate code within same org raises IntegrityError."""
        from django.db import IntegrityError
        _route(self.org, 'Standard', 'rt-dup-code', self.template)
        with self.assertRaises(IntegrityError):
            ApprovalRoute.objects.create(
                org=self.org, name='Other', code='rt-dup-code',
                trigger_type='mrf', template=self.template,
            )

    def test_02_template_org_mismatch_raises(self):
        """Route.clean(): template from a different org is rejected."""
        other_template = WorkflowTemplate.objects.create(
            org=self.org2, name='org2-tpl', code='org2-tpl',
            trigger_type='mrf', version=1, is_active=True,
        )
        route = ApprovalRoute(
            org=self.org, name='Bad Route', code='rt-bad-org',
            trigger_type='mrf', template=other_template,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            route.clean()
        self.assertIn('template', ctx.exception.message_dict)

    def test_03_trigger_type_mismatch_with_template_raises(self):
        """Route.clean(): trigger_type must match template.trigger_type."""
        route = ApprovalRoute(
            org=self.org, name='Mis Route', code='rt-mismatch',
            trigger_type='client_onboarding',
            template=self.template,  # template is trigger_type='mrf'
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            route.clean()
        self.assertIn('trigger_type', ctx.exception.message_dict)

    def test_04_step_assignment_invalid_step_code_raises(self):
        """ARSA.clean(): step_code not in route.template raises ValidationError."""
        route = _route(self.org, 'Valid Route', 'rt-valid', self.template)
        arsa = ApprovalRouteStepAssignment(
            route=route,
            step_code='nonexistent_step',
            assignment_mode='named_user',
            named_user=self.approver1,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            arsa.clean()
        self.assertIn('step_code', ctx.exception.message_dict)

    def test_05_step_assignment_inactive_user_raises(self):
        """ARSA.clean(): inactive named_user is rejected for active assignment."""
        inactive_user = _user('rt_inactive', org=self.org)
        inactive_user.is_active = False
        inactive_user.save()

        route = _route(self.org, 'Route05', 'rt-route05', self.template)
        arsa = ApprovalRouteStepAssignment(
            route=route,
            step_code='hr_review',
            assignment_mode='named_user',
            named_user=inactive_user,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            arsa.clean()
        self.assertIn('named_user', ctx.exception.message_dict)


# ─── 6–9: get_available_approval_routes ──────────────────────────────────────

class TestGetAvailableApprovalRoutes(RouteTestBase):

    def test_06_site_scoped_route_returned_for_site_query(self):
        """Site route is included when querying with that site."""
        site_route = _route(
            self.org, 'Site Route', 'rt-site-route', self.template,
            site=self.site,
        )
        routes = get_available_approval_routes('mrf', self.org, site=self.site)
        self.assertIn(site_route, routes)

    def test_07_client_scoped_route_returned_for_client_query(self):
        """Client route is included when querying with that client (no site)."""
        client_route = _route(
            self.org, 'Client Route', 'rt-client-route', self.template,
            client=self.client_obj,
        )
        routes = get_available_approval_routes(
            'mrf', self.org, client=self.client_obj,
        )
        self.assertIn(client_route, routes)

    def test_08_org_level_route_returned_as_fallback(self):
        """Org-level route appears when no site/client filtering."""
        org_route = _route(self.org, 'Org Route', 'rt-org-route', self.template)
        routes = get_available_approval_routes('mrf', self.org)
        self.assertIn(org_route, routes)

    def test_09_priority_site_before_client_before_org(self):
        """
        Routes are returned in priority order: site, then client, then org.
        The first item in the list is the site-scoped route.
        """
        org_route = _route(self.org, 'Org P9', 'rt-p9-org', self.template)
        client_route = _route(
            self.org, 'Client P9', 'rt-p9-client', self.template,
            client=self.client_obj,
        )
        site_route = _route(
            self.org, 'Site P9', 'rt-p9-site', self.template,
            site=self.site,
        )
        routes = get_available_approval_routes(
            'mrf', self.org, client=self.client_obj, site=self.site,
        )
        route_ids = [r.pk for r in routes]
        self.assertLess(
            route_ids.index(site_route.pk),
            route_ids.index(client_route.pk),
        )
        self.assertLess(
            route_ids.index(client_route.pk),
            route_ids.index(org_route.pk),
        )


# ─── 10–12: build_approval_route_preview ─────────────────────────────────────

class TestBuildApprovalRoutePreview(RouteTestBase):

    def test_10_preview_ok_when_all_assignments_valid(self):
        """ok=True when every template step has an active valid named_user assignment."""
        route = _route(self.org, 'Preview OK', 'rt-prev-ok', self.template)
        _arsa(route, 'hr_review', self.approver1)
        _arsa(route, 'finance_review', self.approver2)

        preview = build_approval_route_preview(route)

        self.assertTrue(preview['ok'])
        self.assertEqual(preview['errors'], [])
        self.assertEqual(len(preview['steps']), 2)
        step_codes = [s['step_code'] for s in preview['steps']]
        self.assertIn('hr_review', step_codes)
        self.assertIn('finance_review', step_codes)
        for step in preview['steps']:
            self.assertTrue(step['assignment_ok'])

    def test_11_preview_not_ok_when_step_missing_assignment(self):
        """ok=False when a template step has no active assignment."""
        route = _route(self.org, 'Preview Miss', 'rt-prev-miss', self.template)
        _arsa(route, 'hr_review', self.approver1)
        # finance_review assignment missing

        preview = build_approval_route_preview(route)

        self.assertFalse(preview['ok'])
        self.assertTrue(len(preview['errors']) > 0)
        finance_step = next(
            (s for s in preview['steps'] if s['step_code'] == 'finance_review'), None,
        )
        self.assertIsNotNone(finance_step)
        self.assertFalse(finance_step['assignment_ok'])

    def test_12_preview_not_ok_when_user_inactive(self):
        """ok=False when the assigned user is inactive."""
        inactive = _user('rt_prev_inactive', org=self.org)
        inactive.is_active = False
        inactive.save()

        route = _route(self.org, 'Preview Inactive', 'rt-prev-inactive', self.template)
        # Bypass model.clean() to force the scenario
        ApprovalRouteStepAssignment.objects.create(
            route=route, step_code='hr_review',
            assignment_mode='named_user', named_user=inactive, is_active=True,
        )
        _arsa(route, 'finance_review', self.approver2)

        preview = build_approval_route_preview(route)

        self.assertFalse(preview['ok'])
        hr_step = next(s for s in preview['steps'] if s['step_code'] == 'hr_review')
        self.assertFalse(hr_step['assignment_ok'])


# ─── 13–16: _select_route_or_legacy ──────────────────────────────────────────

class TestSelectRouteOrLegacy(RouteTestBase):

    def test_13_no_routes_returns_legacy(self):
        """When no ApprovalRoute rows exist, use_legacy is True."""
        # Use a fresh org so no routes are present
        fresh_org = Organization.objects.create(name='Empty Org', code='rt-empty')
        route, use_legacy = _select_route_or_legacy('mrf', fresh_org)
        self.assertIsNone(route)
        self.assertTrue(use_legacy)

    def test_14_exactly_one_route_auto_selected(self):
        """Single available route is auto-selected without error."""
        fresh_org = Organization.objects.create(name='Single Org', code='rt-single')
        tpl = _template(fresh_org, 'rt-single-tpl')
        single = _route(fresh_org, 'Only Route', 'rt-only', tpl)

        route, use_legacy = _select_route_or_legacy('mrf', fresh_org)
        self.assertFalse(use_legacy)
        self.assertEqual(route.pk, single.pk)

    def test_15_multiple_routes_one_default_uses_default(self):
        """When multiple routes exist and exactly one is default, it is selected."""
        fresh_org = Organization.objects.create(name='Multi Org', code='rt-multi')
        tpl = _template(fresh_org, 'rt-multi-tpl')
        _route(fresh_org, 'Route A', 'rt-multi-a', tpl, is_default=False)
        default_route = _route(fresh_org, 'Route B', 'rt-multi-b', tpl, is_default=True)

        route, use_legacy = _select_route_or_legacy('mrf', fresh_org)
        self.assertFalse(use_legacy)
        self.assertEqual(route.pk, default_route.pk)

    def test_16_multiple_routes_no_default_raises(self):
        """Multiple routes with no default raises WorkflowConfigurationError."""
        fresh_org = Organization.objects.create(name='Ambig Org', code='rt-ambig')
        tpl = _template(fresh_org, 'rt-ambig-tpl')
        _route(fresh_org, 'Route X', 'rt-ambig-x', tpl, is_default=False)
        _route(fresh_org, 'Route Y', 'rt-ambig-y', tpl, is_default=False)

        with self.assertRaises(WorkflowConfigurationError) as ctx:
            _select_route_or_legacy('mrf', fresh_org)
        self.assertIn('Select an approval route', str(ctx.exception))


# ─── 17–20: Available routes API ─────────────────────────────────────────────

class TestAvailableRoutesAPI(RouteTestBase):

    def _url(self, **params):
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'/api/workflow/routes/available/?{qs}' if qs else '/api/workflow/routes/available/'

    def test_17_unauthenticated_returns_401(self):
        resp = self._api().get(self._url(trigger_type='mrf'))
        self.assertEqual(resp.status_code, 401)

    def test_18_bad_trigger_type_returns_400(self):
        resp = self._api(self.admin).get(self._url(trigger_type='invalid'))
        self.assertEqual(resp.status_code, 400)

    def test_19_valid_request_returns_routes_with_step_preview(self):
        """GET with valid params returns list of routes with step details."""
        route = _route(self.org, 'API Route', 'rt-api-route', self.template)
        _arsa(route, 'hr_review', self.approver1)
        _arsa(route, 'finance_review', self.approver2)

        resp = self._api(self.admin).get(self._url(trigger_type='mrf'))
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(route.pk, ids)

        match = next(r for r in resp.data['results'] if r['id'] == route.pk)
        self.assertEqual(len(match['steps']), 2)
        self.assertIn('assigned_user', match['steps'][0])

    def test_20_empty_result_when_no_routes_configured(self):
        """Returns empty results list when no ApprovalRoutes exist for the org."""
        empty_org = Organization.objects.create(name='No Routes Org', code='rt-nones')
        n_company = _node(empty_org, 'rt-nones', 'company', None, 0, 'rt-nones')
        role = _role(empty_org, 'admin')
        bootstrap_role_permissions(role)
        user = _user('rt_noroutes_user', org=empty_org)
        _assign(user, role, n_company)

        resp = self._api(user).get(self._url(trigger_type='mrf'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])


# ─── 21–24: start_mrf_workflow with route ────────────────────────────────────

class TestStartMRFWorkflowWithRoute(RouteTestBase):

    def test_21_start_with_valid_route_returns_201_and_stores_fk(self):
        """POST with a valid approval_route → 201, WorkflowInstance.approval_route set."""
        route = _route(self.org, 'MRF Route 21', 'rt-mrf21', self.template, is_default=True)
        _arsa(route, 'hr_review', self.approver1)
        _arsa(route, 'finance_review', self.approver2)

        mrf = _mrf(self.org, self.site, self.admin)
        MRFLineItem.objects.create(
            mrf=mrf, job_role=self.job_role,
            site_role_requirement=self.srr, headcount=2,
        )
        resp = self._api(self.admin).post(
            f'/api/workflow/mrf/{mrf.pk}/start/',
            {'approval_route': route.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        instance = WorkflowInstance.objects.get(mrf=mrf)
        self.assertEqual(instance.approval_route_id, route.pk)
        self.assertEqual(instance.approval_route_name_snapshot, route.name)
        self.assertEqual(instance.approval_route_code_snapshot, route.code)

    def test_22_start_with_wrong_org_route_raises_error(self):
        """Route from a different org raises WorkflowConfigurationError."""
        org2_template = _template(self.org2, 'rt-org2-mrf-tpl', trigger_type='mrf')
        wrong_org_route = _route(self.org2, 'Org2 Route', 'rt-org2-route', org2_template)

        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(WorkflowConfigurationError):
            start_mrf_workflow(mrf, actor=self.admin, approval_route=wrong_org_route)

    def test_23_post_with_nonexistent_route_id_returns_400(self):
        """POST with a route ID that does not exist returns 400."""
        mrf = _mrf(self.org, self.site, self.admin)
        resp = self._api(self.admin).post(
            f'/api/workflow/mrf/{mrf.pk}/start/',
            {'approval_route': 999999},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_24_route_with_missing_step_assignment_raises(self):
        """Route with a template step that has no assignment raises WorkflowConfigurationError."""
        route = _route(self.org, 'MRF Route 24', 'rt-mrf24', self.template)
        _arsa(route, 'hr_review', self.approver1)
        # finance_review assignment missing

        mrf = _mrf(self.org, self.site, self.admin)
        with self.assertRaises(WorkflowConfigurationError) as ctx:
            start_mrf_workflow(mrf, actor=self.admin, approval_route=route)
        self.assertIn('finance_review', str(ctx.exception))


# ─── 25–27: start_client_onboarding_workflow with route ──────────────────────

class TestStartOnboardingWorkflowWithRoute(RouteTestBase):

    def test_25_start_onboarding_with_valid_route_succeeds(self):
        """start_client_onboarding_workflow with a valid route creates instance."""
        route = _route(
            self.org, 'OB Route 25', 'rt-ob25', self.ob_template,
            trigger_type='client_onboarding',
        )
        _arsa(route, 'sales_review', self.approver1)

        req = _onboarding(self.org, self.client_obj, self.admin)
        instance = start_client_onboarding_workflow(req, actor=self.admin, approval_route=route)

        self.assertIsNotNone(instance)
        self.assertEqual(instance.approval_route_id, route.pk)

    def test_26_multiple_routes_no_default_no_route_given_returns_400(self):
        """Multiple routes, no default, and no route provided → API returns 400."""
        tpl = _template(self.org, 'rt-ob26-tpl', trigger_type='client_onboarding')
        _step(tpl, 1, 'ob_step_a', on_approve_next='END')
        # Two routes, neither is default
        _route(self.org, 'OB Route A', 'rt-ob26a', tpl, trigger_type='client_onboarding')
        _route(self.org, 'OB Route B', 'rt-ob26b', tpl, trigger_type='client_onboarding')

        req = MobilisationSetupRequest.objects.create(
            org=self.org, client=self.client_obj, requested_by=self.admin,
            mobilisation_type='new_client', status='setup_completed',
        )
        MobilisationProposedDepartment.objects.create(
            request=req, name='Dept 26', code='rt-pd26',
            scope_level='client', is_active=True,
        )
        for site in self.client_obj.sites.filter(is_active=True):
            site_dept = MobilisationProposedDepartment.objects.create(
                request=req, name=f'{site.name} Ops', code=f'rt-pd26-{site.pk}',
                scope_level='site', real_site=site, is_active=True,
            )
            for idx, srr in enumerate(site.role_requirements.filter(is_active=True), start=1):
                MobilisationProposedDepartmentRole.objects.create(
                    request=req,
                    proposed_department=site_dept,
                    site_role_requirement=srr,
                    sort_order=idx,
                )
        MobilisationProposedUser.objects.create(
            request=req, full_name='User 26', email='user26@rt.local',
            access_role=self.role_admin, scope_level='client', is_active=True,
        )

        resp = self._api(self.admin).post(
            f'/api/workflow/client-onboarding/{req.pk}/start/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('approval route', resp.data['detail'].lower())

    def test_27_approval_route_name_and_code_snapshotted(self):
        """WorkflowInstance records route name and code as snapshot strings."""
        route = _route(
            self.org, 'Snapshot Route', 'rt-snapshot', self.ob_template,
            trigger_type='client_onboarding',
        )
        _arsa(route, 'sales_review', self.approver1)

        req = _onboarding(self.org, self.client_obj, self.admin)
        instance = start_client_onboarding_workflow(req, actor=self.admin, approval_route=route)

        self.assertEqual(instance.approval_route_name_snapshot, 'Snapshot Route')
        self.assertEqual(instance.approval_route_code_snapshot, 'rt-snapshot')


# ─── 28: Regression — legacy path unchanged ───────────────────────────────────

class TestLegacyRegressionWithRoutes(RouteTestBase):

    def test_28_no_routes_uses_legacy_sac_path(self):
        """
        When no ApprovalRoute rows exist, start_mrf_workflow falls back to the legacy
        template mapping + SAC path, producing a valid workflow with approval_route=None.
        """
        # Use a fresh org with no ApprovalRoutes at all
        fresh_org = Organization.objects.create(name='Legacy Org', code='rt-legacy')
        n_co = _node(fresh_org, 'rt-legacy', 'company', None, 0, 'rt-legacy')
        n_cl = _node(fresh_org, 'rt-leg-client', 'client', n_co, 1, 'rt-legacy/rt-leg-client')
        n_si = _node(fresh_org, 'rt-leg-site', 'site', n_cl, 2, 'rt-legacy/rt-leg-client/rt-leg-site')

        client = Client.objects.create(
            org=fresh_org, name='Legacy Client', code='rt-leg-client', scope_node=n_cl,
        )
        site = SiteProfile.objects.create(
            org=fresh_org, client=client,
            name='Legacy Site', code='rt-leg-site', scope_node=n_si,
        )

        tpl = _template(fresh_org, 'rt-leg-tpl')
        _step(tpl, 1, 'hr_review', on_approve_next='END')
        WorkflowTemplateMapping.objects.create(
            org=fresh_org, trigger_type='mrf', template=tpl, is_active=True,
        )

        approver = _user('rt_leg_approver', org=fresh_org)
        StepAssignmentConfig.objects.create(
            org=fresh_org, trigger_type='mrf', step_code='hr_review',
            assignment_mode='named_user', named_user=approver, is_active=True,
        )

        actor = _user('rt_leg_actor', org=fresh_org)
        mrf = _mrf(fresh_org, site, actor)

        instance = start_mrf_workflow(mrf, actor=actor)

        self.assertIsNotNone(instance)
        self.assertIsNone(instance.approval_route_id)
        self.assertEqual(instance.approval_route_name_snapshot, '')
        self.assertEqual(instance.approval_route_code_snapshot, '')
        self.assertEqual(instance.steps.count(), 1)
        first_step = instance.steps.first()
        self.assertEqual(first_step.assigned_user_id, approver.pk)

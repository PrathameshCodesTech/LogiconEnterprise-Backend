"""
Phase Mobilisation-Operations-Setup-Handoff-O focused tests.

The key rule: sales may explicitly choose an operations owner during conversion,
but the system must not auto-assign one.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    MOBILISATION_READ,
    MOBILISATION_UPDATE,
    MOBILISATION_FINALIZE,
    WORKFLOW_START,
)
from apps.mobilisation.models import (
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)
from apps.sales.services import convert_won_sales_lead_to_onboarding_setup
from apps.workflow.services import WorkflowConfigurationError, start_client_onboarding_workflow

from .test_mobilisation_sales_context_n import (
    _all_caps,
    _org,
    _role,
    _scope_node,
    _user,
    _won_lead_with_proposal,
)


def _ops_caps():
    return _all_caps() + [
        MOBILISATION_READ,
        MOBILISATION_UPDATE,
        MOBILISATION_FINALIZE,
        WORKFLOW_START,
    ]


def _same_org_user(username, org, scope_node, role):
    return _user(username, org, role, scope_node)


class MobilisationOperationsHandoffTestCase(TestCase):
    def setUp(self):
        self.org = _org('MOBOPS')
        self.scope_node = _scope_node(self.org)
        self.role = _role(self.org, 'ops-admin', _ops_caps())
        self.client_role = _role(self.org, 'client_admin', [])
        self.sales_user = _same_org_user('rohan.sales', self.org, self.scope_node, self.role)
        self.ops_user = _same_org_user('alice.ops', self.org, self.scope_node, self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.ops_user)

    def _converted_request(self, operations_owner=None):
        lead, proposal = _won_lead_with_proposal(self.org, self.sales_user)
        req = convert_won_sales_lead_to_onboarding_setup(
            lead,
            self.sales_user,
            proposal=proposal,
            operations_owner=operations_owner,
        )
        return lead, proposal, req

    def _add_ready_setup(self, req):
        MobilisationProposedUser.objects.create(
            request=req,
            full_name='Client Admin',
            email=f'client-admin-{req.pk}@example.com',
            user_type='client',
            access_role=self.role,
            scope_level='client',
            is_primary_contact=True,
            send_invite_on_finalization=False,
        )

    def test_convert_without_operations_owner_does_not_auto_assign(self):
        lead, proposal, req = self._converted_request()

        self.assertEqual(req.status, 'draft')
        self.assertIsNone(req.assigned_operations_owner_id)
        self.assertIsNone(req.submitted_to_operations_at)

    def test_convert_with_explicit_operations_owner_hands_off_to_operations(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        self.assertEqual(req.status, 'operations_setup')
        self.assertEqual(req.assigned_operations_owner_id, self.ops_user.id)
        self.assertIsNotNone(req.submitted_to_operations_at)

    def test_convert_rejects_operations_owner_from_another_org(self):
        other_org = _org('OTHEROPS')
        other_scope = _scope_node(other_org)
        other_role = _role(other_org, 'other', _ops_caps())
        other_user = _same_org_user('other.ops', other_org, other_scope, other_role)

        lead, proposal = _won_lead_with_proposal(self.org, self.sales_user)

        with self.assertRaisesMessage(ValueError, 'same organization'):
            convert_won_sales_lead_to_onboarding_setup(
                lead,
                self.sales_user,
                proposal=proposal,
                operations_owner=other_user,
            )

    def test_assign_operations_owner_endpoint_sets_operations_setup_status(self):
        lead, proposal, req = self._converted_request()

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/assign-operations-owner/',
            {'operations_owner': self.ops_user.pk},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'operations_setup')
        self.assertEqual(req.assigned_operations_owner_id, self.ops_user.id)
        self.assertIsNotNone(req.submitted_to_operations_at)

    def test_mark_setup_completed_requires_readiness(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('Add at least one active proposed user', resp.data['detail'])

    def test_mark_setup_completed_sets_completion_fields(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        self._add_ready_setup(req)

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'setup_completed')
        self.assertIsNotNone(req.setup_completed_at)
        self.assertEqual(req.setup_completed_by_id, self.ops_user.id)

    def test_sales_led_direct_finalize_blocked_before_setup_completed(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        req.mobilisation_requires_approval = False
        req.save(update_fields=['mobilisation_requires_approval', 'updated_at'])
        self._add_ready_setup(req)

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/finalize-directly/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('completed by operations', resp.data['detail'])

    def test_sales_led_direct_finalize_allowed_after_setup_completed(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        req.mobilisation_requires_approval = False
        req.save(update_fields=['mobilisation_requires_approval', 'updated_at'])
        self._add_ready_setup(req)

        self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )
        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/finalize-directly/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.finalization_status, 'finalized')

    def test_direct_finalize_can_explicitly_override_required_approval_after_setup_completed(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        self._add_ready_setup(req)

        self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )
        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/finalize-directly/',
            {'override_approval_required': True},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.finalization_status, 'finalized')

    def test_workflow_start_blocked_before_setup_completed(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        self._add_ready_setup(req)

        with self.assertRaisesMessage(WorkflowConfigurationError, 'completed by operations'):
            start_client_onboarding_workflow(req, actor=self.ops_user)

    def test_list_filter_by_assigned_operations_owner(self):
        lead, proposal, assigned_req = self._converted_request(operations_owner=self.ops_user)
        lead2, proposal2, unassigned_req = self._converted_request()

        resp = self.client.get(
            '/api/mobilisation/setup-requests/',
            {'assigned_operations_owner': self.ops_user.pk},
        )

        self.assertEqual(resp.status_code, 200)
        ids = [row['id'] for row in resp.json()['results']]
        self.assertIn(assigned_req.pk, ids)
        self.assertNotIn(unassigned_req.pk, ids)

    def test_setup_suggestions_endpoint_returns_departments_and_client_user(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        resp = self.client.get(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-suggestions/',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        dept_keys = {row['key'] for row in body['departments']}
        self.assertIn('client-administration', dept_keys)
        self.assertTrue(any(row['scope_level'] == 'site' for row in body['departments']))
        self.assertTrue(any(row['real_site_name'] == 'Acme HQ' for row in body['departments']))
        self.assertEqual(body['users'][0]['email'], 'john@acme.com')
        self.assertEqual(body['users'][0]['access_role_code'], 'client_admin')
        self.assertTrue(body['users'][0]['can_apply'])

    def test_apply_setup_suggestions_creates_rows_idempotently(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        resp1 = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/apply-setup-suggestions/',
            {},
            format='json',
        )
        resp2 = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/apply-setup-suggestions/',
            {},
            format='json',
        )

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertGreaterEqual(len(resp1.json()['created']['departments']), 2)
        self.assertEqual(len(resp1.json()['created']['users']), 1)
        self.assertEqual(len(resp2.json()['created']['departments']), 0)
        self.assertEqual(len(resp2.json()['created']['users']), 0)
        self.assertGreaterEqual(req.proposed_departments.filter(is_active=True).count(), 2)
        self.assertEqual(req.proposed_users.filter(is_active=True).count(), 1)

    def test_apply_setup_suggestions_allows_setup_completion(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/apply-setup-suggestions/',
            {},
            format='json',
        )
        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'setup_completed')

    def test_setup_builder_get_returns_available_roles(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        resp = self.client.get(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-builder/',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['request'], req.pk)
        self.assertGreaterEqual(len(body['available_roles']), 1)
        self.assertEqual(body['available_roles'][0]['assigned_department'], None)
        self.assertGreaterEqual(len(body['unassigned_roles']), 1)

    def test_setup_builder_apply_simple_template_locks_client_admin_and_maps_roles(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-builder/apply-template/',
            {'setup_strategy': 'simple'},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.setup_strategy, 'simple')
        self.assertTrue(req.proposed_departments.filter(
            source_key='client-administration',
            is_locked=True,
            is_active=True,
        ).exists())
        self.assertEqual(req.proposed_department_roles.filter(is_active=True).count(), 1)
        self.assertEqual(resp.json()['unassigned_roles'], [])

    def test_setup_builder_put_custom_updates_department_and_role_assignment(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-builder/apply-template/',
            {'setup_strategy': 'simple'},
            format='json',
        )
        body = self.client.get(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-builder/',
        ).json()
        site_dept = next(row for row in body['departments'] if row['scope_level'] == 'site')
        site_dept['name'] = 'Technical Services'
        site_dept['code'] = 'tech-services'

        resp = self.client.put(
            f'/api/mobilisation/setup-requests/{req.pk}/setup-builder/',
            {
                'setup_strategy': 'custom',
                'departments': body['departments'],
                'users': body['users'],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.setup_strategy, 'custom')
        self.assertTrue(req.proposed_departments.filter(
            name='Technical Services',
            code='tech-services',
            is_active=True,
        ).exists())
        self.assertEqual(req.proposed_department_roles.filter(is_active=True).count(), 1)

    def test_mark_setup_completed_does_not_require_srr_department_assignments(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        site = req.client.sites.filter(is_active=True).first()
        MobilisationProposedDepartment.objects.create(
            request=req,
            real_site=site,
            scope_level='site',
            name='Operations',
            code='ops',
        )
        MobilisationProposedUser.objects.create(
            request=req,
            full_name='Client Admin',
            email=f'client-admin-unassigned-{req.pk}@example.com',
            user_type='client',
            access_role=self.role,
            scope_level='client',
            is_primary_contact=True,
            send_invite_on_finalization=False,
        )

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'setup_completed')

    def test_finalization_creates_client_users_without_srr_departments(self):
        lead, proposal, req = self._converted_request(operations_owner=self.ops_user)
        req.mobilisation_requires_approval = False
        req.save(update_fields=['mobilisation_requires_approval', 'updated_at'])
        self._add_ready_setup(req)
        self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/mark-setup-completed/',
            {},
            format='json',
        )

        resp = self.client.post(
            f'/api/mobilisation/setup-requests/{req.pk}/finalize-directly/',
            {},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        srr = req.client.sites.first().role_requirements.first()
        srr.refresh_from_db()
        self.assertIsNone(srr.department_id)
        self.assertEqual(req.proposed_users.filter(created_user__isnull=False).count(), 1)

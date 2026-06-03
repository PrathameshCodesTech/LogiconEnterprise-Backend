"""
apps/deployment/tests/test_deployment_lifecycle_g.py

Phase Deployment-Lifecycle-Backend-G — service + API + DB-level coverage.

Groups:
  Group 1 — Deployment lifecycle (activate, cancel, complete)
  Group 2 — Transfer (planned / activate_new / blocked statuses)
  Group 3 — Employee suspend / reactivate / exit
  Group 4 — DeploymentHistory rows + read-only API
  Group 5 — DB unique-active constraint
  Group 6 — Cross-org scope + permission checks
"""

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.access.capabilities import (
    DEPLOYMENT_MANAGE,
    DEPLOYMENT_READ,
    EMPLOYEE_READ,
    EMPLOYEE_UPDATE,
    SITE_DEPLOYMENT_READ,
    SITE_DEPLOYMENT_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.deployment.lifecycle_services import (
    DeploymentLifecycleError,
    activate_deployment,
    cancel_deployment,
    complete_deployment,
    exit_employee,
    reactivate_employee,
    suspend_employee,
    transfer_deployment,
)
from apps.deployment.models import DeploymentHistory, Employee, SiteDeployment
from apps.jobs.models import JobRole
from apps.sites.models import Client, SiteProfile


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org, suffix='a'):
    n_co = ScopeNode.objects.create(
        org=org, code=f'{org.code}', name=f'{org.code}',
        node_type='company', parent=None, depth=0,
        path=f'{org.code}', is_active=True,
    )
    client = Client.objects.create(
        org=org, name=f'Client {org.code}-{suffix}',
        code=f'cl-{org.code}-{suffix}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}-{suffix}', name=f'cl-{org.code}-{suffix}',
        node_type='client', parent=n_co, depth=1,
        path=f'{org.code}/cl-{org.code}-{suffix}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name=f'Site {org.code}-{suffix}', code=f'site-{org.code}-{suffix}',
        is_active=True,
    )
    return n_co, n_cl, client, site


def _second_site(org, n_co, suffix='b'):
    """Create a second site under the same org/company scope."""
    client = Client.objects.create(
        org=org, name=f'Client {org.code}-{suffix}',
        code=f'cl-{org.code}-{suffix}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}-{suffix}', name=f'cl-{org.code}-{suffix}',
        node_type='client', parent=n_co, depth=1,
        path=f'{org.code}/cl-{org.code}-{suffix}', is_active=True,
    )
    return SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name=f'Site {org.code}-{suffix}', code=f'site-{org.code}-{suffix}',
        is_active=True,
    )


def _role(org, code, caps=None):
    r, _ = AccessRole.objects.get_or_create(
        org=org, code=code, defaults={'name': code},
    )
    bootstrap_role_permissions(r, caps=caps)
    return r


def _user(username, org, role_obj=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.is_superuser = is_superuser
    u.is_staff = is_superuser
    u.save()
    if role_obj and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _employee(org, code='EMP-001', first='Test', last='Emp', status='active'):
    return Employee.objects.create(
        org=org,
        employee_code=code,
        first_name=first,
        last_name=last,
        status=status,
        joined_on=date.today(),
    )


def _deployment(org, employee, site, job_role, status='planned', start_date=None):
    return SiteDeployment.objects.create(
        org=org,
        employee=employee,
        site=site,
        job_role=job_role,
        status=status,
        start_date=start_date or date.today(),
        billing_type='billable',
    )


# ─── Shared base ──────────────────────────────────────────────────────────────

class LifecycleBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('lcg')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org, 'a')
        cls.site_b = _second_site(cls.org, cls.n_co, 'b')

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard LCG', code='guard-lcg',
        )
        cls.job_role_2 = JobRole.objects.create(
            org=cls.org, name='Supervisor LCG', code='sup-lcg',
        )

        # Roles
        full_caps = [
            EMPLOYEE_READ, EMPLOYEE_UPDATE,
            SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_UPDATE,
            DEPLOYMENT_READ, DEPLOYMENT_MANAGE,
        ]
        cls.role_admin = _role(cls.org, 'lcg_admin', caps=full_caps)
        cls.role_read_only = _role(
            cls.org, 'lcg_read',
            caps=[EMPLOYEE_READ, SITE_DEPLOYMENT_READ, DEPLOYMENT_READ],
        )

        cls.user_admin = _user('lcg_admin', cls.org, cls.role_admin, cls.n_co)
        cls.user_read = _user('lcg_reader', cls.org, cls.role_read_only, cls.n_co)
        cls.superuser = _user('lcg_super', cls.org, is_superuser=True)

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Activate / Cancel / Complete
# ═══════════════════════════════════════════════════════════════════════════════

class TestActivateCancelComplete(LifecycleBase):

    def test_activate_planned_deployment(self):
        emp = _employee(self.org, code='EMP-ACT-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        result = activate_deployment(dep, self.user_admin, note='go live')
        self.assertEqual(result.status, 'active')
        self.assertTrue(
            DeploymentHistory.objects.filter(
                deployment=dep, action_type='deployment_activated',
                from_status='planned', to_status='active',
            ).exists()
        )

    def test_activate_blocked_when_other_active_exists(self):
        emp = _employee(self.org, code='EMP-ACT-2')
        _deployment(self.org, emp, self.site, self.job_role, status='active')
        dep2 = _deployment(self.org, emp, self.site_b, self.job_role, status='planned')
        with self.assertRaises(DeploymentLifecycleError):
            activate_deployment(dep2, self.user_admin)

    def test_activate_blocked_for_exited_employee(self):
        emp = _employee(self.org, code='EMP-ACT-3', status='exited')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        with self.assertRaises(DeploymentLifecycleError):
            activate_deployment(dep, self.user_admin)

    def test_activate_blocked_for_non_planned(self):
        emp = _employee(self.org, code='EMP-ACT-4')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        with self.assertRaises(DeploymentLifecycleError):
            activate_deployment(dep, self.user_admin)

    def test_cancel_planned_deployment_keeps_end_date_null(self):
        emp = _employee(self.org, code='EMP-CXL-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        dep = cancel_deployment(dep, self.user_admin, note='no longer needed')
        self.assertEqual(dep.status, 'cancelled')
        self.assertIsNone(dep.end_date)
        self.assertTrue(
            DeploymentHistory.objects.filter(
                deployment=dep, action_type='deployment_cancelled',
            ).exists()
        )

    def test_cancel_blocked_for_active(self):
        emp = _employee(self.org, code='EMP-CXL-2')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        with self.assertRaises(DeploymentLifecycleError):
            cancel_deployment(dep, self.user_admin)

    def test_complete_active_deployment_sets_end_date(self):
        emp = _employee(self.org, code='EMP-CMP-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        dep = complete_deployment(dep, self.user_admin)
        self.assertEqual(dep.status, 'completed')
        self.assertEqual(dep.end_date, date.today())

    def test_complete_blocked_for_cancelled(self):
        emp = _employee(self.org, code='EMP-CMP-2')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='cancelled')
        with self.assertRaises(DeploymentLifecycleError):
            complete_deployment(dep, self.user_admin)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — Transfer
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransfer(LifecycleBase):

    def test_transfer_creates_new_planned_and_closes_old(self):
        emp = _employee(self.org, code='EMP-TR-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        result = transfer_deployment(
            dep, self.user_admin, new_site=self.site_b,
        )
        old, new = result['old'], result['new']
        self.assertEqual(old.status, 'transferred')
        self.assertEqual(new.status, 'planned')
        self.assertEqual(new.site_id, self.site_b.pk)
        self.assertEqual(new.employee_id, emp.pk)
        # Job role defaults to current
        self.assertEqual(new.job_role_id, dep.job_role_id)

        out_row = DeploymentHistory.objects.get(
            deployment=old, action_type='deployment_transferred_out',
        )
        in_row = DeploymentHistory.objects.get(
            deployment=new, action_type='deployment_transferred_in',
        )
        self.assertEqual(out_row.metadata['new_deployment_id'], new.pk)
        self.assertEqual(in_row.metadata['previous_deployment_id'], old.pk)

    def test_transfer_with_activate_new_creates_active(self):
        emp = _employee(self.org, code='EMP-TR-2')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        result = transfer_deployment(
            dep, self.user_admin, new_site=self.site_b,
            new_job_role=self.job_role_2, activate_new=True,
        )
        self.assertEqual(result['old'].status, 'transferred')
        self.assertEqual(result['new'].status, 'active')
        self.assertEqual(result['new'].job_role_id, self.job_role_2.pk)
        # exactly one active deployment exists for this employee
        self.assertEqual(
            SiteDeployment.objects.filter(employee=emp, status='active').count(),
            1,
        )

    def test_transfer_blocked_for_completed_or_cancelled(self):
        emp = _employee(self.org, code='EMP-TR-3')
        dep_done = _deployment(
            self.org, emp, self.site, self.job_role, status='completed',
        )
        with self.assertRaises(DeploymentLifecycleError):
            transfer_deployment(dep_done, self.user_admin, new_site=self.site_b)

        emp2 = _employee(self.org, code='EMP-TR-4')
        dep_cxl = _deployment(
            self.org, emp2, self.site, self.job_role, status='cancelled',
        )
        with self.assertRaises(DeploymentLifecycleError):
            transfer_deployment(dep_cxl, self.user_admin, new_site=self.site_b)

    def test_transfer_blocked_for_exited_employee(self):
        emp = _employee(self.org, code='EMP-TR-5', status='exited')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        with self.assertRaises(DeploymentLifecycleError):
            transfer_deployment(dep, self.user_admin, new_site=self.site_b)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — Employee suspend / reactivate / exit
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmployeeLifecycle(LifecycleBase):

    def test_suspend_employee_auto_closes_open_deployments(self):
        emp = _employee(self.org, code='EMP-SUS-1')
        dep_planned = _deployment(
            self.org, emp, self.site, self.job_role, status='planned',
        )
        dep_active = _deployment(
            self.org, emp, self.site_b, self.job_role, status='active',
        )

        emp = suspend_employee(emp, self.user_admin, note='caught misconduct')

        dep_planned.refresh_from_db()
        dep_active.refresh_from_db()

        self.assertEqual(emp.status, 'suspended')
        self.assertEqual(dep_planned.status, 'cancelled')
        self.assertIsNone(dep_planned.end_date)
        self.assertEqual(dep_active.status, 'completed')
        self.assertEqual(dep_active.end_date, date.today())

        # 3 history rows: 2 deployments + 1 employee event
        emp_rows = DeploymentHistory.objects.filter(employee=emp)
        self.assertEqual(emp_rows.count(), 3)
        self.assertTrue(emp_rows.filter(action_type='employee_suspended').exists())

    def test_reactivate_employee_does_not_restore_deployments(self):
        emp = _employee(self.org, code='EMP-RE-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        emp = suspend_employee(emp, self.user_admin)
        dep.refresh_from_db()
        self.assertEqual(dep.status, 'completed')

        emp = reactivate_employee(emp, self.user_admin)
        self.assertEqual(emp.status, 'active')
        dep.refresh_from_db()
        self.assertEqual(dep.status, 'completed')  # not restored

        self.assertTrue(
            DeploymentHistory.objects.filter(
                employee=emp, action_type='employee_reactivated',
            ).exists()
        )

    def test_exit_employee_closes_deployments_and_sets_exited_on(self):
        emp = _employee(self.org, code='EMP-EX-1')
        _deployment(self.org, emp, self.site, self.job_role, status='active')
        _deployment(self.org, emp, self.site_b, self.job_role, status='planned')

        emp = exit_employee(emp, self.user_admin, note='resigned')
        self.assertEqual(emp.status, 'exited')
        self.assertEqual(emp.exited_on, date.today())

        # All deployments closed
        self.assertEqual(
            SiteDeployment.objects.filter(
                employee=emp, status__in=('planned', 'active'),
            ).count(),
            0,
        )

        self.assertTrue(
            DeploymentHistory.objects.filter(
                employee=emp, action_type='employee_exited',
            ).exists()
        )

    def test_cannot_activate_deployment_for_exited_employee(self):
        emp = _employee(self.org, code='EMP-EX-2')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        emp = exit_employee(emp, self.user_admin)
        dep.refresh_from_db()
        # After exit, the planned deployment has been auto-cancelled.
        self.assertEqual(dep.status, 'cancelled')

        # Even if a stray planned deployment somehow existed, activate would refuse.
        stray = _deployment(self.org, emp, self.site_b, self.job_role, status='planned')
        with self.assertRaises(DeploymentLifecycleError):
            activate_deployment(stray, self.user_admin)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — History rows and read-only API
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoryRows(LifecycleBase):

    def test_history_row_has_correct_fields(self):
        emp = _employee(self.org, code='EMP-HIS-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        complete_deployment(dep, self.user_admin, note='done')
        row = DeploymentHistory.objects.get(
            deployment=dep, action_type='deployment_completed',
        )
        self.assertEqual(row.org_id, self.org.pk)
        self.assertEqual(row.employee_id, emp.pk)
        self.assertEqual(row.from_status, 'active')
        self.assertEqual(row.to_status, 'completed')
        self.assertEqual(row.from_site_id, self.site.pk)
        self.assertEqual(row.to_site_id, self.site.pk)
        self.assertEqual(row.from_job_role_id, self.job_role.pk)
        self.assertEqual(row.actor_id, self.user_admin.pk)
        self.assertEqual(row.note, 'done')
        self.assertIn('end_date', row.metadata)

    def test_history_api_lists_rows(self):
        emp = _employee(self.org, code='EMP-API-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        activate_deployment(dep, self.user_admin)

        self._auth(self.user_admin)
        r = self.api.get('/api/deployment/history/')
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        ids = [row['id'] for row in r.data.get('results', r.data)]
        history_id = DeploymentHistory.objects.filter(deployment=dep).first().pk
        self.assertIn(history_id, ids)

    def test_history_api_requires_deployment_read(self):
        # A user with no caps gets 403.
        no_cap_role = _role(self.org, 'lcg_nocap', caps=[])
        u = _user('lcg_nocap_user', self.org, no_cap_role, self.n_co)
        self._auth(u)
        r = self.api.get('/api/deployment/history/')
        self.assertEqual(r.status_code, http_status.HTTP_403_FORBIDDEN)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 5 — DB-level unique active constraint
# ═══════════════════════════════════════════════════════════════════════════════

class TestDbActiveConstraint(LifecycleBase):

    def test_db_rejects_two_active_deployments_for_same_employee(self):
        emp = _employee(self.org, code='EMP-DB-1')
        _deployment(self.org, emp, self.site, self.job_role, status='active')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SiteDeployment.objects.create(
                    org=self.org, employee=emp, site=self.site_b,
                    job_role=self.job_role, status='active',
                    start_date=date.today(), billing_type='billable',
                )

    def test_db_allows_multiple_planned_deployments(self):
        emp = _employee(self.org, code='EMP-DB-2')
        _deployment(self.org, emp, self.site, self.job_role, status='planned')
        # Second planned deployment is allowed (constraint is partial on status='active').
        _deployment(self.org, emp, self.site_b, self.job_role, status='planned')
        self.assertEqual(
            SiteDeployment.objects.filter(employee=emp, status='planned').count(),
            2,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group 6 — API: scope + permission checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiScopeAndPermissions(LifecycleBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_org = _org('lcgX')
        cls.x_n_co, _, _, cls.x_site = _scope_tree(cls.other_org, 'x')
        cls.x_job_role = JobRole.objects.create(
            org=cls.other_org, name='X Guard', code='guard-lcgx',
        )

    def test_activate_via_api_succeeds(self):
        emp = _employee(self.org, code='EMP-APIA-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        self._auth(self.user_admin)
        r = self.api.post(
            f'/api/deployment/site-deployments/{dep.pk}/activate/',
            data={'note': 'ok'}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        self.assertEqual(r.data['status'], 'active')

    def test_transfer_via_api_returns_old_and_new(self):
        emp = _employee(self.org, code='EMP-APIT-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='active')
        self._auth(self.user_admin)
        r = self.api.post(
            f'/api/deployment/site-deployments/{dep.pk}/transfer/',
            data={'site': self.site_b.pk, 'activate_new': False},
            format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(r.data['old']['status'], 'transferred')
        self.assertEqual(r.data['new']['status'], 'planned')
        self.assertEqual(r.data['new']['site'], self.site_b.pk)

    def test_employee_exit_via_api(self):
        emp = _employee(self.org, code='EMP-APIE-1')
        _deployment(self.org, emp, self.site, self.job_role, status='active')
        self._auth(self.user_admin)
        r = self.api.post(
            f'/api/deployment/employees/{emp.pk}/exit/',
            data={'note': 'resigned'}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        self.assertEqual(r.data['status'], 'exited')

    def test_cross_org_access_blocked(self):
        # Set up a deployment in the OTHER org and try to activate it from
        # this org's user (admin in `self.org`, no access to `self.other_org`).
        emp_x = Employee.objects.create(
            org=self.other_org, employee_code='EMP-X-1',
            first_name='X', last_name='Worker', status='active',
        )
        dep_x = SiteDeployment.objects.create(
            org=self.other_org, employee=emp_x, site=self.x_site,
            job_role=self.x_job_role, status='planned',
            start_date=date.today(), billing_type='billable',
        )
        self._auth(self.user_admin)
        r = self.api.post(
            f'/api/deployment/site-deployments/{dep_x.pk}/activate/',
            data={}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_permission_check_denies_read_only_user(self):
        emp = _employee(self.org, code='EMP-PERM-1')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        self._auth(self.user_read)
        r = self.api.post(
            f'/api/deployment/site-deployments/{dep.pk}/activate/',
            data={}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_permission_check_allows_deployment_manage(self):
        # Build a user with ONLY deployment.manage (no site_deployment.update).
        role = _role(self.org, 'lcg_dm', caps=[
            SITE_DEPLOYMENT_READ, DEPLOYMENT_MANAGE, DEPLOYMENT_READ,
        ])
        u = _user('lcg_dm_user', self.org, role, self.n_co)
        emp = _employee(self.org, code='EMP-PERM-2')
        dep = _deployment(self.org, emp, self.site, self.job_role, status='planned')
        self._auth(u)
        r = self.api.post(
            f'/api/deployment/site-deployments/{dep.pk}/activate/',
            data={}, format='json',
        )
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)

"""
apps/deployment/lifecycle_services.py

Operational lifecycle actions for Employees and SiteDeployments after the
hiring → deployment conversion.

All actions are atomic. Every action writes one or more `DeploymentHistory`
rows. Service-level invariants are enforced in addition to the DB-level
partial UniqueConstraint (`unique_active_deployment_per_employee`).

Public functions
----------------
- activate_deployment
- cancel_deployment
- complete_deployment
- transfer_deployment
- suspend_employee
- reactivate_employee
- exit_employee

Each raises `DeploymentLifecycleError` (a subclass of DRF ValidationError) on
invariant violations.
"""

from datetime import date

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import DeploymentHistory, Employee, SiteDeployment


# ─── Errors ───────────────────────────────────────────────────────────────────

class DeploymentLifecycleError(ValidationError):
    """Raised when a deployment / employee lifecycle invariant is violated."""

    def __init__(self, message, code='invalid'):
        super().__init__({'non_field_errors': [message]})
        self.code = code


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _record_history(
    *,
    employee,
    action_type,
    actor,
    deployment=None,
    from_status='',
    to_status='',
    from_site=None,
    to_site=None,
    from_job_role=None,
    to_job_role=None,
    note='',
    metadata=None,
):
    return DeploymentHistory.objects.create(
        org=employee.org,
        employee=employee,
        deployment=deployment,
        action_type=action_type,
        from_status=from_status or '',
        to_status=to_status or '',
        from_site=from_site,
        to_site=to_site,
        from_job_role=from_job_role,
        to_job_role=to_job_role,
        actor=actor,
        note=note or '',
        metadata=metadata or {},
    )


def _has_other_active_deployment(employee, exclude_id=None):
    qs = SiteDeployment.objects.filter(employee=employee, status='active')
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def _assert_employee_deployable(employee):
    if employee.status == 'exited':
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} has exited and cannot be deployed.",
            code='employee_exited',
        )
    if employee.status == 'suspended':
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} is suspended. Reactivate before deploying.",
            code='employee_suspended',
        )


# ─── Deployment actions ───────────────────────────────────────────────────────

def activate_deployment(deployment, actor, *, note=''):
    """planned → active. Refuses if another active deployment exists or
    if the employee is suspended/exited."""
    if deployment.status != 'planned':
        raise DeploymentLifecycleError(
            f"Cannot activate deployment in status '{deployment.status}'. "
            f"Only 'planned' deployments can be activated.",
            code='invalid_status',
        )

    employee = deployment.employee
    _assert_employee_deployable(employee)

    if _has_other_active_deployment(employee, exclude_id=deployment.pk):
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} already has an active deployment. "
            f"Only one active deployment per employee is allowed.",
            code='active_deployment_exists',
        )

    with transaction.atomic():
        prev_status = deployment.status
        deployment.status = 'active'
        deployment.save(update_fields=['status', 'updated_at'])

        _record_history(
            employee=employee,
            deployment=deployment,
            action_type='deployment_activated',
            actor=actor,
            from_status=prev_status,
            to_status='active',
            from_site=deployment.site,
            to_site=deployment.site,
            from_job_role=deployment.job_role,
            to_job_role=deployment.job_role,
            note=note,
        )

    return deployment


def cancel_deployment(deployment, actor, *, note=''):
    """planned → cancelled. `end_date` is intentionally left untouched
    (only `complete_deployment` sets an end date)."""
    if deployment.status != 'planned':
        raise DeploymentLifecycleError(
            f"Cannot cancel deployment in status '{deployment.status}'. "
            f"Only 'planned' deployments can be cancelled.",
            code='invalid_status',
        )

    with transaction.atomic():
        prev_status = deployment.status
        deployment.status = 'cancelled'
        deployment.save(update_fields=['status', 'updated_at'])

        _record_history(
            employee=deployment.employee,
            deployment=deployment,
            action_type='deployment_cancelled',
            actor=actor,
            from_status=prev_status,
            to_status='cancelled',
            from_site=deployment.site,
            to_site=deployment.site,
            from_job_role=deployment.job_role,
            to_job_role=deployment.job_role,
            note=note,
        )

    return deployment


def complete_deployment(deployment, actor, *, note='', end_date=None):
    """planned/active → completed. Sets `end_date` (defaults to today)."""
    if deployment.status not in ('planned', 'active'):
        raise DeploymentLifecycleError(
            f"Cannot complete deployment in status '{deployment.status}'. "
            f"Only 'planned' or 'active' deployments can be completed.",
            code='invalid_status',
        )

    with transaction.atomic():
        prev_status = deployment.status
        deployment.status = 'completed'
        deployment.end_date = end_date or date.today()
        deployment.save(update_fields=['status', 'end_date', 'updated_at'])

        _record_history(
            employee=deployment.employee,
            deployment=deployment,
            action_type='deployment_completed',
            actor=actor,
            from_status=prev_status,
            to_status='completed',
            from_site=deployment.site,
            to_site=deployment.site,
            from_job_role=deployment.job_role,
            to_job_role=deployment.job_role,
            note=note,
            metadata={'end_date': deployment.end_date.isoformat()},
        )

    return deployment


def transfer_deployment(
    deployment,
    actor,
    *,
    new_site,
    new_job_role=None,
    start_date=None,
    note='',
    activate_new=False,
):
    """
    Close the current deployment (planned/active → transferred) and create a
    new deployment for the same employee at `new_site`.

    The new deployment is created as 'planned' by default; pass
    `activate_new=True` to atomically activate it (subject to the
    one-active-per-employee constraint).
    """
    if deployment.status not in ('planned', 'active'):
        raise DeploymentLifecycleError(
            f"Cannot transfer deployment in status '{deployment.status}'. "
            f"Only 'planned' or 'active' deployments can be transferred.",
            code='invalid_status',
        )

    employee = deployment.employee
    _assert_employee_deployable(employee)

    target_job_role = new_job_role or deployment.job_role
    target_start = start_date or date.today()

    if new_site.org_id != employee.org_id:
        raise DeploymentLifecycleError(
            "Cannot transfer to a site in a different organization.",
            code='cross_org_site',
        )
    if target_job_role and target_job_role.org_id != employee.org_id:
        raise DeploymentLifecycleError(
            "Cannot transfer to a job role in a different organization.",
            code='cross_org_job_role',
        )

    with transaction.atomic():
        new_deployment = SiteDeployment.objects.create(
            org=employee.org,
            employee=employee,
            site=new_site,
            job_role=target_job_role,
            status='planned',
            start_date=target_start,
            shift_hours=deployment.shift_hours,
            billing_type=deployment.billing_type,
            created_by=actor,
        )

        prev_status = deployment.status
        deployment.status = 'transferred'
        deployment.save(update_fields=['status', 'updated_at'])

        _record_history(
            employee=employee,
            deployment=deployment,
            action_type='deployment_transferred_out',
            actor=actor,
            from_status=prev_status,
            to_status='transferred',
            from_site=deployment.site,
            to_site=new_site,
            from_job_role=deployment.job_role,
            to_job_role=target_job_role,
            note=note,
            metadata={'new_deployment_id': new_deployment.pk},
        )

        _record_history(
            employee=employee,
            deployment=new_deployment,
            action_type='deployment_transferred_in',
            actor=actor,
            from_status=prev_status,
            to_status='planned',
            from_site=deployment.site,
            to_site=new_site,
            from_job_role=deployment.job_role,
            to_job_role=target_job_role,
            note=note,
            metadata={'previous_deployment_id': deployment.pk},
        )

        if activate_new:
            new_deployment = activate_deployment(new_deployment, actor, note=note)

    return {'old': deployment, 'new': new_deployment}


# ─── Employee actions ─────────────────────────────────────────────────────────

def _close_open_deployments_for(employee, actor, *, suspend_reason):
    """
    Close every open deployment of the employee:
      - 'planned' → cancelled (no end_date)
      - 'active'  → completed (end_date=today)
    Returns the list of closed (employee_id, deployment_id, prev_status, new_status) tuples.
    """
    open_qs = SiteDeployment.objects.filter(
        employee=employee, status__in=('planned', 'active'),
    ).select_related('site', 'job_role')

    closed = []
    today = date.today()

    for dep in open_qs:
        prev_status = dep.status
        if prev_status == 'planned':
            dep.status = 'cancelled'
            dep.save(update_fields=['status', 'updated_at'])
            _record_history(
                employee=employee,
                deployment=dep,
                action_type='deployment_cancelled',
                actor=actor,
                from_status=prev_status,
                to_status='cancelled',
                from_site=dep.site,
                to_site=dep.site,
                from_job_role=dep.job_role,
                to_job_role=dep.job_role,
                note=f'Auto-cancelled: {suspend_reason}',
                metadata={'reason': suspend_reason},
            )
        else:  # active
            dep.status = 'completed'
            dep.end_date = today
            dep.save(update_fields=['status', 'end_date', 'updated_at'])
            _record_history(
                employee=employee,
                deployment=dep,
                action_type='deployment_completed',
                actor=actor,
                from_status=prev_status,
                to_status='completed',
                from_site=dep.site,
                to_site=dep.site,
                from_job_role=dep.job_role,
                to_job_role=dep.job_role,
                note=f'Auto-completed: {suspend_reason}',
                metadata={'reason': suspend_reason, 'end_date': today.isoformat()},
            )
        closed.append((dep.pk, prev_status, dep.status))

    return closed


def suspend_employee(employee, actor, *, note=''):
    """active → suspended. Auto-cancels planned and auto-completes active deployments."""
    if employee.status == 'exited':
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} has exited and cannot be suspended.",
            code='employee_exited',
        )
    if employee.status == 'suspended':
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} is already suspended.",
            code='already_suspended',
        )

    with transaction.atomic():
        closed = _close_open_deployments_for(
            employee, actor, suspend_reason='employee_suspended',
        )

        prev_status = employee.status
        employee.status = 'suspended'
        employee.save(update_fields=['status', 'updated_at'])

        _record_history(
            employee=employee,
            action_type='employee_suspended',
            actor=actor,
            from_status=prev_status,
            to_status='suspended',
            note=note,
            metadata={'closed_deployments': closed},
        )

    return employee


def reactivate_employee(employee, actor, *, note=''):
    """suspended → active. Does NOT auto-restore deployments closed by suspend."""
    if employee.status != 'suspended':
        raise DeploymentLifecycleError(
            f"Cannot reactivate employee in status '{employee.status}'. "
            f"Only 'suspended' employees can be reactivated.",
            code='invalid_status',
        )

    with transaction.atomic():
        prev_status = employee.status
        employee.status = 'active'
        employee.save(update_fields=['status', 'updated_at'])

        _record_history(
            employee=employee,
            action_type='employee_reactivated',
            actor=actor,
            from_status=prev_status,
            to_status='active',
            note=note,
        )

    return employee


def exit_employee(employee, actor, *, exited_on=None, note=''):
    """active/suspended → exited. Closes all open deployments."""
    if employee.status == 'exited':
        raise DeploymentLifecycleError(
            f"Employee #{employee.pk} is already exited.",
            code='already_exited',
        )

    exit_date = exited_on or date.today()

    with transaction.atomic():
        closed = _close_open_deployments_for(
            employee, actor, suspend_reason='employee_exited',
        )

        prev_status = employee.status
        employee.status = 'exited'
        employee.exited_on = exit_date
        employee.save(update_fields=['status', 'exited_on', 'updated_at'])

        _record_history(
            employee=employee,
            action_type='employee_exited',
            actor=actor,
            from_status=prev_status,
            to_status='exited',
            note=note,
            metadata={
                'exited_on': exit_date.isoformat(),
                'closed_deployments': closed,
            },
        )

    return employee

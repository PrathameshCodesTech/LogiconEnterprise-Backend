"""
apps/workflow/services.py

MRF workflow engine services — Phase 1.
"""

from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from .exceptions import WorkflowConfigurationError


# ─── Public: resolution helpers ───────────────────────────────────────────────

def resolve_workflow_template(trigger_type, org, client=None, site=None):
    """
    Return the WorkflowTemplate to use.
    Fallback order: site → client → org default.
    Raises WorkflowConfigurationError if no active mapping exists.
    """
    from .models import WorkflowTemplateMapping

    base_qs = WorkflowTemplateMapping.objects.filter(
        org=org, trigger_type=trigger_type, is_active=True,
    ).select_related('template')

    if site is not None:
        mapping = base_qs.filter(site=site).first()
        if mapping:
            return _assert_template_active(mapping.template)

    if client is not None:
        mapping = base_qs.filter(client=client, site__isnull=True).first()
        if mapping:
            return _assert_template_active(mapping.template)

    mapping = base_qs.filter(client__isnull=True, site__isnull=True).first()
    if mapping:
        return _assert_template_active(mapping.template)

    raise WorkflowConfigurationError(
        f'No active workflow template mapping found for trigger_type="{trigger_type}" '
        f'in your organization. Please configure a WorkflowTemplateMapping.'
    )


def resolve_step_assignment(trigger_type, org, step_code, client=None, site=None, on_date=None):
    """
    Return the StepAssignmentConfig to use for the given step.
    Fallback order: site → client → org default.
    Phase 1: only named_user mode is supported.
    Raises WorkflowConfigurationError if no valid config exists.
    """
    from .models import StepAssignmentConfig

    if on_date is None:
        on_date = timezone.now().date()

    base_qs = StepAssignmentConfig.objects.filter(
        org=org, trigger_type=trigger_type, step_code=step_code, is_active=True,
    ).filter(
        models.Q(effective_from__isnull=True) | models.Q(effective_from__lte=on_date)
    ).filter(
        models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=on_date)
    ).select_related('named_user', 'department', 'site__client', 'client')

    config = None
    if site is not None:
        config = base_qs.filter(site=site).first()
    if config is None and client is not None:
        config = base_qs.filter(client=client, site__isnull=True).first()
    if config is None:
        config = base_qs.filter(client__isnull=True, site__isnull=True).first()

    if config is None:
        raise WorkflowConfigurationError(
            f'No active assignment config found for step_code="{step_code}" '
            f'(trigger_type="{trigger_type}") in your organization.'
        )

    if config.assignment_mode != 'named_user':
        raise WorkflowConfigurationError(
            f'Step "{step_code}" uses assignment_mode="{config.assignment_mode}" '
            f'which is not supported in Phase 1. Only "named_user" is supported.'
        )

    if config.named_user is None:
        raise WorkflowConfigurationError(
            f'Step "{step_code}" is configured as named_user but no user is set.'
        )

    if not config.named_user.is_active:
        raise WorkflowConfigurationError(
            f'Step "{step_code}": assigned user "{config.named_user.username}" is inactive.'
        )

    if config.named_user.org_id != org.pk:
        raise WorkflowConfigurationError(
            f'Step "{step_code}": assigned user "{config.named_user.username}" '
            f'does not belong to this organization.'
        )

    if config.department_id and config.named_user.department_id != config.department_id:
        dept_name = config.department.name if config.department else f'dept_id={config.department_id}'
        raise WorkflowConfigurationError(
            f'Step "{step_code}": assigned user "{config.named_user.username}" '
            f'does not belong to department "{dept_name}".'
        )

    if config.department_id and config.department:
        dept = config.department
        if config.site_id:
            # Site-level SAC: dept must be org-level, same-client, or same-site
            site_client_id = config.site.client_id if config.site else None
            if dept.site_id is not None and dept.site_id != config.site_id:
                raise WorkflowConfigurationError(
                    f'Step "{step_code}": department "{dept.name}" is scoped to a different site '
                    f'than this assignment config.'
                )
            elif dept.site_id is None and dept.client_id is not None and dept.client_id != site_client_id:
                raise WorkflowConfigurationError(
                    f'Step "{step_code}": department "{dept.name}" belongs to a different client '
                    f'than the site for this assignment config.'
                )
        elif config.client_id:
            # Client-level SAC: dept must be org-level or same-client
            if dept.site_id is not None or (dept.client_id is not None and dept.client_id != config.client_id):
                raise WorkflowConfigurationError(
                    f'Step "{step_code}": department "{dept.name}" scope is incompatible '
                    f'with a client-level assignment config.'
                )
        else:
            # Org-level SAC: dept must be org-level
            if dept.client_id is not None or dept.site_id is not None:
                raise WorkflowConfigurationError(
                    f'Step "{step_code}": department "{dept.name}" is not org-level. '
                    f'Org-level assignment configs require an org-level department.'
                )

    return config


# ─── Public: workflow lifecycle ───────────────────────────────────────────────

@transaction.atomic
def start_client_onboarding_workflow(onboarding_request, actor):
    """
    Start a workflow for the given ClientOnboardingRequest.
    Mirrors start_mrf_workflow but uses trigger_type='client_onboarding'
    and is scoped to org/client (no site).
    Returns the created WorkflowInstance.
    """
    from .models import WorkflowInstance, WorkflowStepInstance, WorkflowAction

    if WorkflowInstance.objects.filter(
        client_onboarding_request=onboarding_request, status='active',
    ).exists():
        raise WorkflowConfigurationError(
            'This onboarding request already has an active workflow.'
        )

    org = onboarding_request.org
    client = onboarding_request.client

    template = resolve_workflow_template('client_onboarding', org, client=client)

    steps = list(template.steps.order_by('order'))
    if not steps:
        raise WorkflowConfigurationError(
            f'Workflow template "{template.code}" has no steps configured.'
        )

    today = timezone.now().date()
    assignments = {}
    for step in steps:
        config = resolve_step_assignment(
            trigger_type='client_onboarding',
            org=org,
            step_code=step.code,
            client=client,
            on_date=today,
        )
        assignments[step.code] = config

    instance = WorkflowInstance.objects.create(
        org=org,
        client_onboarding_request=onboarding_request,
        template=template,
        template_version=template.version,
        status='active',
        initiated_by=actor,
    )

    now = timezone.now()
    step_instances = []
    for step in steps:
        config = assignments[step.code]
        dept = config.department
        step_instances.append(WorkflowStepInstance(
            workflow=instance,
            step_template=step,
            step_order=step.order,
            step_code=step.code,
            step_name=step.name,
            assignment_mode=step.assignment_mode,
            actor_type=step.actor_type,
            on_approve_next=step.on_approve_next,
            on_reject_target=step.on_reject_target,
            on_request_changes_target=step.on_request_changes_target,
            requires_comment_on_reject=step.requires_comment_on_reject,
            requires_comment_on_request_changes=step.requires_comment_on_request_changes,
            sla_hours=step.sla_hours,
            assigned_user=config.named_user,
            assigned_department=dept,
            assigned_department_name_snapshot=dept.name if dept else '',
            assigned_department_code_snapshot=dept.code if dept else '',
            assigned_at=now,
            status='pending',
        ))
    WorkflowStepInstance.objects.bulk_create(step_instances)

    first_step = instance.steps.order_by('step_order').first()
    _activate_step(first_step)

    WorkflowAction.objects.create(
        workflow=instance,
        step_instance=first_step,
        actor=actor,
        action='start',
    )

    onboarding_request.status = 'in_review'
    onboarding_request.submitted_at = now
    onboarding_request.save(update_fields=['status', 'submitted_at', 'updated_at'])

    return instance


@transaction.atomic
def start_mrf_workflow(mrf, actor):
    """
    Start a workflow for the given MRF.
    - Resolves template and all step assignments.
    - Creates WorkflowInstance and all WorkflowStepInstance records.
    - Activates the first step.
    - Writes a 'start' WorkflowAction.
    - Sets mrf.status = 'hr_review'.
    Returns the created WorkflowInstance.
    """
    from .models import WorkflowInstance, WorkflowStepInstance, WorkflowAction

    if WorkflowInstance.objects.filter(mrf=mrf, status='active').exists():
        raise WorkflowConfigurationError('This MRF already has an active workflow.')

    site = mrf.site
    client = getattr(site, 'client', None)
    org = mrf.org

    template = resolve_workflow_template('mrf', org, client=client, site=site)

    steps = list(template.steps.order_by('order'))
    if not steps:
        raise WorkflowConfigurationError(
            f'Workflow template "{template.code}" has no steps configured.'
        )

    today = timezone.now().date()
    assignments = {}
    for step in steps:
        config = resolve_step_assignment(
            trigger_type='mrf',
            org=org,
            step_code=step.code,
            client=client,
            site=site,
            on_date=today,
        )
        assignments[step.code] = config

    instance = WorkflowInstance.objects.create(
        org=org,
        mrf=mrf,
        template=template,
        template_version=template.version,
        status='active',
        initiated_by=actor,
    )

    now = timezone.now()
    step_instances = []
    for step in steps:
        config = assignments[step.code]
        dept = config.department
        step_instances.append(WorkflowStepInstance(
            workflow=instance,
            step_template=step,
            step_order=step.order,
            step_code=step.code,
            step_name=step.name,
            assignment_mode=step.assignment_mode,
            actor_type=step.actor_type,
            on_approve_next=step.on_approve_next,
            on_reject_target=step.on_reject_target,
            on_request_changes_target=step.on_request_changes_target,
            requires_comment_on_reject=step.requires_comment_on_reject,
            requires_comment_on_request_changes=step.requires_comment_on_request_changes,
            sla_hours=step.sla_hours,
            assigned_user=config.named_user,
            assigned_department=dept,
            assigned_department_name_snapshot=dept.name if dept else '',
            assigned_department_code_snapshot=dept.code if dept else '',
            assigned_at=now,
            status='pending',
        ))
    WorkflowStepInstance.objects.bulk_create(step_instances)

    first_step = instance.steps.order_by('step_order').first()
    _activate_step(first_step)

    WorkflowAction.objects.create(
        workflow=instance,
        step_instance=first_step,
        actor=actor,
        action='start',
    )

    mrf.status = 'hr_review'
    mrf.save(update_fields=['status', 'updated_at'])

    return instance


def act_on_step(step_instance, actor, action, comment=''):
    """
    Record an action (approve / reject / request_changes) on an active step.
    Updates step state and drives the workflow transition.
    Raises WorkflowConfigurationError for invalid state or missing comment.
    """
    from .models import WorkflowAction

    VALID_ACTIONS = ('approve', 'reject', 'request_changes')
    if action not in VALID_ACTIONS:
        raise WorkflowConfigurationError(
            f'Invalid action "{action}". Must be one of {VALID_ACTIONS}.'
        )

    with transaction.atomic():
        step_instance = (
            step_instance.__class__.objects
            .select_for_update()
            .get(pk=step_instance.pk)
        )

        if step_instance.status != 'active':
            raise WorkflowConfigurationError(
                f'Step is not active (current status: "{step_instance.status}").'
            )

        if action == 'reject' and step_instance.requires_comment_on_reject and not comment.strip():
            raise WorkflowConfigurationError(
                'A comment is required when rejecting this step.'
            )
        if action == 'request_changes' and step_instance.requires_comment_on_request_changes and not comment.strip():
            raise WorkflowConfigurationError(
                'A comment is required when requesting changes on this step.'
            )

        action_to_status = {
            'approve': 'approved',
            'reject': 'rejected',
            'request_changes': 'request_changes',
        }
        now = timezone.now()
        step_instance.status = action_to_status[action]
        step_instance.acted_by = actor
        step_instance.acted_at = now
        step_instance.action_taken = action
        step_instance.comment = comment
        step_instance.save(update_fields=[
            'status', 'acted_by', 'acted_at', 'action_taken', 'comment',
        ])

        WorkflowAction.objects.create(
            workflow=step_instance.workflow,
            step_instance=step_instance,
            actor=actor,
            action=action,
            comment=comment,
        )

        _apply_transition(step_instance, action)


def reassign_step(step_instance, actor, new_user, comment=''):
    """
    Reassign a pending or active step to a different user.
    Raises WorkflowConfigurationError if the step or user is invalid.
    """
    from .models import WorkflowAction

    if step_instance.status not in ('pending', 'active'):
        raise WorkflowConfigurationError(
            f'Cannot reassign a step with status "{step_instance.status}".'
        )

    if not new_user.is_active:
        raise WorkflowConfigurationError('Cannot reassign to an inactive user.')

    if new_user.org_id != step_instance.workflow.org_id:
        raise WorkflowConfigurationError(
            'Assignee must belong to the same organization as the workflow.'
        )

    if step_instance.assigned_department_id is not None:
        if new_user.department_id != step_instance.assigned_department_id:
            raise WorkflowConfigurationError(
                f'New assignee must belong to department "{step_instance.assigned_department_name_snapshot}".'
            )

    with transaction.atomic():
        step_instance = (
            step_instance.__class__.objects
            .select_for_update()
            .get(pk=step_instance.pk)
        )
        old_user = step_instance.assigned_user
        step_instance.assigned_user = new_user
        step_instance.assigned_at = timezone.now()
        step_instance.save(update_fields=['assigned_user', 'assigned_at'])

        WorkflowAction.objects.create(
            workflow=step_instance.workflow,
            step_instance=step_instance,
            actor=actor,
            action='reassign',
            comment=comment,
            reassign_from=old_user,
            reassign_to=new_user,
        )


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _assert_template_active(template):
    if not template.is_active:
        raise WorkflowConfigurationError(
            f'Workflow template "{template.code}" is inactive.'
        )
    return template


def _activate_step(step_instance):
    now = timezone.now()
    step_instance.status = 'active'
    step_instance.activated_at = now
    if step_instance.sla_hours:
        step_instance.due_at = now + timedelta(hours=step_instance.sla_hours)
    step_instance.save(update_fields=['status', 'activated_at', 'due_at'])


def _reactivate_step(step_instance):
    """Reset a previously-acted step back to active (e.g. on reject-back or request_changes-back)."""
    now = timezone.now()
    step_instance.status = 'active'
    step_instance.activated_at = now
    step_instance.acted_by = None
    step_instance.acted_at = None
    step_instance.action_taken = ''
    step_instance.comment = ''
    if step_instance.sla_hours:
        step_instance.due_at = now + timedelta(hours=step_instance.sla_hours)
    else:
        step_instance.due_at = None
    step_instance.save(update_fields=[
        'status', 'activated_at', 'due_at',
        'acted_by', 'acted_at', 'action_taken', 'comment',
    ])


def _get_next_step(workflow, current_step_instance):
    """Return the next sequential pending step by order, or None if none remain."""
    return (
        workflow.steps
        .filter(step_order__gt=current_step_instance.step_order, status='pending')
        .order_by('step_order')
        .first()
    )


def _apply_transition(step_instance, action):
    """Drive the workflow forward after a step action."""
    workflow = step_instance.workflow

    if action == 'approve':
        target_code = step_instance.on_approve_next
        if target_code == 'END':
            _complete_workflow(workflow, 'approved')
        elif target_code:
            next_step = workflow.steps.filter(step_code=target_code).first()
            if next_step:
                _activate_step(next_step)
            else:
                _complete_workflow(workflow, 'approved')
        else:
            next_step = _get_next_step(workflow, step_instance)
            if next_step:
                _activate_step(next_step)
            else:
                _complete_workflow(workflow, 'approved')

    elif action in ('reject', 'request_changes'):
        target_code = (
            step_instance.on_reject_target
            if action == 'reject'
            else step_instance.on_request_changes_target
        )
        if target_code:
            target_step = workflow.steps.filter(step_code=target_code).first()
            if target_step:
                _reactivate_step(target_step)
                return
        _complete_workflow(workflow, 'rejected')


def _complete_workflow(workflow_instance, outcome):
    """Mark the workflow and its linked target object as complete."""
    now = timezone.now()
    workflow_instance.status = outcome
    workflow_instance.completed_at = now
    workflow_instance.save(update_fields=['status', 'completed_at'])

    if workflow_instance.mrf_id is not None:
        mrf = workflow_instance.mrf
        if outcome == 'approved':
            mrf.status = 'approved'
            mrf.approved_at = now
            mrf.save(update_fields=['status', 'approved_at', 'updated_at'])
        elif outcome == 'rejected':
            mrf.status = 'rejected'
            mrf.rejected_at = now
            mrf.save(update_fields=['status', 'rejected_at', 'updated_at'])

    elif workflow_instance.client_onboarding_request_id is not None:
        req = workflow_instance.client_onboarding_request
        if outcome == 'approved':
            req.status = 'approved'
            req.approved_at = now
            req.save(update_fields=['status', 'approved_at', 'updated_at'])
        elif outcome == 'rejected':
            req.status = 'rejected'
            req.rejected_at = now
            req.save(update_fields=['status', 'rejected_at', 'updated_at'])



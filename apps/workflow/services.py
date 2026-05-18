"""
apps/workflow/services.py

MRF workflow engine services — Phase 1.
"""

from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from .exceptions import OnboardingPreflightError, WorkflowConfigurationError


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


# ─── Public: approval route helpers ──────────────────────────────────────────

def get_available_approval_routes(trigger_type, org, client=None, site=None):
    """
    Return active ApprovalRoutes for the given org/trigger/scope, in priority order:
      1. Site-specific routes (if site provided)
      2. Client-specific routes (if client provided)
      3. Org-level routes

    Within each group: defaults first, then by name/code for determinism.
    """
    from .models import ApprovalRoute

    base_qs = ApprovalRoute.objects.filter(
        org=org, trigger_type=trigger_type, is_active=True,
    ).select_related('template', 'client', 'site')

    results = []

    if site is not None:
        results.extend(
            base_qs.filter(site=site).order_by('-is_default', 'name', 'code')
        )

    if client is not None:
        results.extend(
            base_qs.filter(client=client, site__isnull=True).order_by('-is_default', 'name', 'code')
        )

    results.extend(
        base_qs.filter(client__isnull=True, site__isnull=True).order_by('-is_default', 'name', 'code')
    )

    return results


def build_approval_route_preview(route):
    """
    Build a preview dict for one ApprovalRoute, including per-step assignment status.
    Returns dict with 'ok' (bool), 'errors' (list), and 'steps' (list).
    """
    from .models import ApprovalRouteStepAssignment

    template = route.template
    template_steps = list(template.steps.order_by('order'))

    if route.site_id:
        scope_level = 'site'
    elif route.client_id:
        scope_level = 'client'
    else:
        scope_level = 'org'

    assignments = {
        a.step_code: a
        for a in ApprovalRouteStepAssignment.objects.filter(
            route=route, is_active=True,
        ).select_related('named_user', 'department')
    }

    errors = []
    steps = []

    if not template.is_active:
        errors.append(f'Template "{template.code}" is inactive.')

    for step in template_steps:
        assignment = assignments.get(step.code)
        step_ok = True
        step_errors = []

        if assignment is None:
            step_ok = False
            step_errors.append(f'No active assignment for step "{step.code}".')
        elif assignment.assignment_mode == 'named_user':
            if assignment.named_user is None:
                step_ok = False
                step_errors.append(
                    f'Step "{step.code}": named_user assignment but no user set.'
                )
            elif not assignment.named_user.is_active:
                step_ok = False
                step_errors.append(
                    f'Step "{step.code}": assigned user '
                    f'"{assignment.named_user.username}" is inactive.'
                )
            elif assignment.named_user.org_id != route.org_id:
                step_ok = False
                step_errors.append(
                    f'Step "{step.code}": assigned user does not belong to this organization.'
                )

        errors.extend(step_errors)

        dept = assignment.department if assignment and assignment.department_id else None
        user = assignment.named_user if assignment and assignment.named_user_id else None

        full_name = None
        if user:
            full_name = user.get_full_name() or user.username

        steps.append({
            'order': step.order,
            'step_code': step.code,
            'step_name': step.name,
            'assignment_ok': step_ok,
            'assigned_user': user.pk if user else None,
            'assigned_user_username': user.username if user else None,
            'assigned_user_name': full_name,
            'department': dept.pk if dept else None,
            'department_name': dept.name if dept else None,
        })

    return {
        'id': route.pk,
        'name': route.name,
        'code': route.code,
        'trigger_type': route.trigger_type,
        'template': route.template_id,
        'template_name': template.name,
        'template_code': template.code,
        'scope_level': scope_level,
        'is_default': route.is_default,
        'description': route.description,
        'ok': len(errors) == 0,
        'errors': errors,
        'steps': steps,
    }


def _validate_route_for_start(route, org, trigger_type, client=None, site=None):
    """
    Validate an ApprovalRoute can be used to start a workflow for the given target.
    Raises WorkflowConfigurationError if any check fails.
    """
    if not route.is_active:
        raise WorkflowConfigurationError(
            f'Approval route "{route.code}" is not active.'
        )
    if route.org_id != org.pk:
        raise WorkflowConfigurationError(
            f'Approval route "{route.code}" does not belong to this organization.'
        )
    if route.trigger_type != trigger_type:
        raise WorkflowConfigurationError(
            f'Approval route "{route.code}" is for trigger_type="{route.trigger_type}", '
            f'not "{trigger_type}".'
        )
    # Scope check: site-scoped route requires matching site
    if route.site_id is not None:
        if site is None or route.site_id != site.pk:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}" is scoped to a site '
                f'that does not match this request.'
            )
    # Client-scoped route requires matching client (and no site restriction)
    elif route.client_id is not None:
        if client is None or route.client_id != client.pk:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}" is scoped to a client '
                f'that does not match this request.'
            )
    # Org-level route: no additional scope check needed


def _resolve_route_step_assignments(route, template):
    """
    Build step-code → ApprovalRouteStepAssignment map for all template steps.
    Raises WorkflowConfigurationError if any step lacks a valid active assignment.
    """
    from .models import ApprovalRouteStepAssignment

    steps = list(template.steps.order_by('order'))
    if not steps:
        raise WorkflowConfigurationError(
            f'Workflow template "{template.code}" has no steps configured.'
        )

    assignment_map = {
        a.step_code: a
        for a in ApprovalRouteStepAssignment.objects.filter(
            route=route, is_active=True,
        ).select_related('named_user', 'department')
    }

    result = {}
    for step in steps:
        assignment = assignment_map.get(step.code)
        if assignment is None:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}": no active assignment for step "{step.code}".'
            )
        if assignment.assignment_mode != 'named_user':
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}", step "{step.code}": '
                f'only named_user assignment is supported.'
            )
        if assignment.named_user is None:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}", step "{step.code}": named_user not set.'
            )
        if not assignment.named_user.is_active:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}", step "{step.code}": '
                f'assigned user "{assignment.named_user.username}" is inactive.'
            )
        if assignment.named_user.org_id != route.org_id:
            raise WorkflowConfigurationError(
                f'Approval route "{route.code}", step "{step.code}": '
                f'assigned user does not belong to this organization.'
            )
        result[step.code] = assignment

    return result


def _select_route_or_legacy(trigger_type, org, client=None, site=None):
    """
    Auto-select an ApprovalRoute when none is explicitly provided.

    Returns (route, use_legacy) where:
      - route is an ApprovalRoute or None
      - use_legacy is True when falling back to the legacy template mapping path

    Rules:
      - No active routes at all → (None, True) — legacy fallback
      - Exactly 1 route total → use it
      - Multiple routes, exactly 1 default → use that default
      - Multiple routes, 0 or >1 defaults → raise WorkflowConfigurationError
    """
    available = get_available_approval_routes(trigger_type, org, client=client, site=site)

    if not available:
        return None, True  # legacy fallback

    if len(available) == 1:
        return available[0], False

    defaults = [r for r in available if r.is_default]
    if len(defaults) == 1:
        return defaults[0], False

    raise WorkflowConfigurationError(
        'Multiple approval routes are available. '
        'Select an approval route before sending for approval.'
    )


# ─── Public: workflow lifecycle ───────────────────────────────────────────────

@transaction.atomic
def start_client_onboarding_workflow(onboarding_request, actor, approval_route=None):
    """
    Start a workflow for the given ClientOnboardingRequest.

    If approval_route is provided, uses that route's template and step assignments.
    Otherwise auto-selects via _select_route_or_legacy():
      - No routes → legacy template-mapping + SAC path (backward-compatible).
      - One route or one default → use it automatically.
      - Multiple routes with no clear default → raises WorkflowConfigurationError.

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

    use_legacy = False
    route_assignments = None

    if approval_route is not None:
        _validate_route_for_start(approval_route, org, 'client_onboarding', client=client)
        template = approval_route.template
        _assert_template_active(template)
        route_assignments = _resolve_route_step_assignments(approval_route, template)
    else:
        approval_route, use_legacy = _select_route_or_legacy(
            'client_onboarding', org, client=client,
        )
        if not use_legacy:
            _validate_route_for_start(approval_route, org, 'client_onboarding', client=client)
            template = approval_route.template
            _assert_template_active(template)
            route_assignments = _resolve_route_step_assignments(approval_route, template)

    if use_legacy:
        template = resolve_workflow_template('client_onboarding', org, client=client)
        steps = list(template.steps.order_by('order'))
        if not steps:
            raise WorkflowConfigurationError(
                f'Workflow template "{template.code}" has no steps configured.'
            )
        today = timezone.now().date()
        sac_assignments = {}
        for step in steps:
            config = resolve_step_assignment(
                trigger_type='client_onboarding',
                org=org,
                step_code=step.code,
                client=client,
                on_date=today,
            )
            sac_assignments[step.code] = config
    else:
        steps = list(template.steps.order_by('order'))

    instance = WorkflowInstance.objects.create(
        org=org,
        client_onboarding_request=onboarding_request,
        template=template,
        template_version=template.version,
        status='active',
        initiated_by=actor,
        approval_route=approval_route if not use_legacy else None,
        approval_route_name_snapshot=approval_route.name if (approval_route and not use_legacy) else '',
        approval_route_code_snapshot=approval_route.code if (approval_route and not use_legacy) else '',
    )

    now = timezone.now()
    step_instances = []
    for step in steps:
        if use_legacy:
            config = sac_assignments[step.code]
            dept = config.department
            assigned_user = config.named_user
        else:
            assignment = route_assignments[step.code]
            dept = assignment.department
            assigned_user = assignment.named_user

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
            assigned_user=assigned_user,
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
def start_mrf_workflow(mrf, actor, approval_route=None):
    """
    Start a workflow for the given MRF.

    If approval_route is provided, uses that route's template and step assignments.
    Otherwise auto-selects via _select_route_or_legacy():
      - No routes → legacy template-mapping + SAC path (backward-compatible).
      - One route or one default → use it automatically.
      - Multiple routes with no clear default → raises WorkflowConfigurationError.

    Sets mrf.status = 'hr_review'.
    Returns the created WorkflowInstance.
    """
    from .models import WorkflowInstance, WorkflowStepInstance, WorkflowAction

    if WorkflowInstance.objects.filter(mrf=mrf, status='active').exists():
        raise WorkflowConfigurationError('This MRF already has an active workflow.')

    site = mrf.site
    client = getattr(site, 'client', None)
    org = mrf.org

    use_legacy = False
    route_assignments = None

    if approval_route is not None:
        _validate_route_for_start(approval_route, org, 'mrf', client=client, site=site)
        template = approval_route.template
        _assert_template_active(template)
        route_assignments = _resolve_route_step_assignments(approval_route, template)
    else:
        approval_route, use_legacy = _select_route_or_legacy(
            'mrf', org, client=client, site=site,
        )
        if not use_legacy:
            _validate_route_for_start(approval_route, org, 'mrf', client=client, site=site)
            template = approval_route.template
            _assert_template_active(template)
            route_assignments = _resolve_route_step_assignments(approval_route, template)

    if use_legacy:
        template = resolve_workflow_template('mrf', org, client=client, site=site)
        steps = list(template.steps.order_by('order'))
        if not steps:
            raise WorkflowConfigurationError(
                f'Workflow template "{template.code}" has no steps configured.'
            )
        today = timezone.now().date()
        sac_assignments = {}
        for step in steps:
            config = resolve_step_assignment(
                trigger_type='mrf',
                org=org,
                step_code=step.code,
                client=client,
                site=site,
                on_date=today,
            )
            sac_assignments[step.code] = config
    else:
        steps = list(template.steps.order_by('order'))

    instance = WorkflowInstance.objects.create(
        org=org,
        mrf=mrf,
        template=template,
        template_version=template.version,
        status='active',
        initiated_by=actor,
        approval_route=approval_route if not use_legacy else None,
        approval_route_name_snapshot=approval_route.name if (approval_route and not use_legacy) else '',
        approval_route_code_snapshot=approval_route.code if (approval_route and not use_legacy) else '',
    )

    now = timezone.now()
    step_instances = []
    for step in steps:
        if use_legacy:
            config = sac_assignments[step.code]
            dept = config.department
            assigned_user = config.named_user
        else:
            assignment = route_assignments[step.code]
            dept = assignment.department
            assigned_user = assignment.named_user

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
            assigned_user=assigned_user,
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

    # Reserve budget (no-op when MRF has no budget_plan)
    from apps.budgets.services import reserve_budget_for_mrf
    from apps.budgets.exceptions import BudgetReservationError
    try:
        reserve_budget_for_mrf(mrf, actor)
    except BudgetReservationError as exc:
        raise WorkflowConfigurationError(str(exc)) from exc

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

        # Preflight: block final approval when onboarding data has known conflicts
        if action == 'approve' and _is_final_onboarding_approve(step_instance):
            from apps.onboarding.services import validate_onboarding_finalization_preflight
            req = step_instance.workflow.client_onboarding_request
            preflight_errors = validate_onboarding_finalization_preflight(req)
            if preflight_errors:
                raise OnboardingPreflightError(
                    'Onboarding cannot be finalized.',
                    preflight_errors,
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

        _apply_transition(step_instance, action, actor=actor)


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

def _is_final_onboarding_approve(step_instance):
    """Return True if approving this step would complete a client_onboarding workflow."""
    if step_instance.workflow.client_onboarding_request_id is None:
        return False
    target_code = step_instance.on_approve_next
    if target_code == 'END':
        return True
    if target_code:
        return not step_instance.workflow.steps.filter(step_code=target_code).exists()
    # Sequential path: final if no pending step comes after this one
    return not step_instance.workflow.steps.filter(
        step_order__gt=step_instance.step_order, status='pending',
    ).exists()


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


def _reset_steps_after(step_instance):
    """
    Reset downstream steps after sending a workflow back.

    Without this, a later step can remain rejected/request_changes and the
    next approval from the reactivated step will skip it because forward
    progression only activates pending steps.
    """
    step_instance.workflow.steps.filter(
        step_order__gt=step_instance.step_order,
    ).update(
        status='pending',
        activated_at=None,
        due_at=None,
        acted_by=None,
        acted_at=None,
        action_taken='',
        comment='',
    )


def _get_next_step(workflow, current_step_instance):
    """Return the next sequential pending step by order, or None if none remain."""
    return (
        workflow.steps
        .filter(step_order__gt=current_step_instance.step_order, status='pending')
        .order_by('step_order')
        .first()
    )


def _apply_transition(step_instance, action, actor=None):
    """Drive the workflow forward after a step action."""
    workflow = step_instance.workflow

    if action == 'approve':
        target_code = step_instance.on_approve_next
        if target_code == 'END':
            _complete_workflow(workflow, 'approved', actor=actor)
        elif target_code:
            next_step = workflow.steps.filter(step_code=target_code).first()
            if next_step:
                _activate_step(next_step)
            else:
                _complete_workflow(workflow, 'approved', actor=actor)
        else:
            next_step = _get_next_step(workflow, step_instance)
            if next_step:
                _activate_step(next_step)
            else:
                _complete_workflow(workflow, 'approved', actor=actor)

    elif action in ('reject', 'request_changes'):
        target_code = (
            step_instance.on_reject_target
            if action == 'reject'
            else step_instance.on_request_changes_target
        )
        if target_code:
            target_step = workflow.steps.filter(step_code=target_code).first()
            if target_step:
                _reset_steps_after(target_step)
                _reactivate_step(target_step)
                return
        _complete_workflow(workflow, 'rejected', actor=actor)


def _complete_workflow(workflow_instance, outcome, actor=None):
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
            from apps.budgets.services import commit_mrf_budget_reservations
            commit_mrf_budget_reservations(mrf)
        elif outcome == 'rejected':
            mrf.status = 'rejected'
            mrf.rejected_at = now
            mrf.save(update_fields=['status', 'rejected_at', 'updated_at'])
            from apps.budgets.services import release_mrf_budget_reservations
            release_mrf_budget_reservations(mrf, note='Workflow rejected')

    elif workflow_instance.client_onboarding_request_id is not None:
        req = workflow_instance.client_onboarding_request
        if outcome == 'approved':
            req.status = 'approved'
            req.approved_at = now
            req.save(update_fields=['status', 'approved_at', 'updated_at'])
            from apps.onboarding.services import finalize_client_onboarding_request
            from apps.onboarding.exceptions import OnboardingFinalizationError
            try:
                finalize_client_onboarding_request(req, actor=actor)
            except OnboardingFinalizationError as exc:
                # Finalization failure is recorded on req (finalization_status='failed',
                # finalization_error set). Workflow approval stands; operator can retry
                # via admin action or retry_finalize_client_onboarding_request().
                import logging
                logging.getLogger(__name__).error(
                    'Onboarding finalization failed for request pk=%s '
                    '(workflow approved, but real records were not created): %s',
                    req.pk,
                    exc,
                )
        elif outcome == 'rejected':
            req.status = 'rejected'
            req.rejected_at = now
            req.save(update_fields=['status', 'rejected_at', 'updated_at'])


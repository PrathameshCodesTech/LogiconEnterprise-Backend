"""
Reusable idempotent seeder for the sales-proposal internal approval workflow.

This module exists so management commands AND the end-to-end demo bootstrap
(`seed_sales_demo`) can share the same idempotent setup without one command
importing another command's internals.
"""

from typing import Optional


def seed_sales_proposal_workflow_route(
    org,
    *,
    sales_head_user=None,
    finance_user=None,
    admin_user=None,
    writer=None,
):
    """
    Create (or refresh) the standard sales-proposal internal approval workflow
    for the given organization. Idempotent.

    Args:
        org: an apps.core.models.Organization instance.
        sales_head_user: optional apps.accounts.models.User for Sales Head Review.
        finance_user: optional User for Finance Review.
        admin_user: optional User for Admin Approval.
        writer: optional callable(str) for progress messages (eg `self.stdout.write`).

    Returns:
        dict with `template`, `mapping`, `route`, and a `created`/`updated` summary.
    """
    from apps.workflow.models import (
        WorkflowTemplate, WorkflowStepTemplate,
        WorkflowTemplateMapping, StepAssignmentConfig,
        ApprovalRoute, ApprovalRouteStepAssignment,
    )

    def _write(msg: str) -> None:
        if writer is not None:
            writer(msg)

    summary = {'created': [], 'existing': [], 'updated': []}

    template, created = WorkflowTemplate.objects.get_or_create(
        org=org,
        code='sales_proposal_default',
        defaults={
            'name': 'Sales Proposal Standard Route',
            'trigger_type': 'sales_proposal',
            'version': 1,
            'description': 'Internal proposal approval: Sales Head -> Finance -> Admin.',
            'is_active': True,
        },
    )
    if not created and template.trigger_type != 'sales_proposal':
        template.trigger_type = 'sales_proposal'
        template.save(update_fields=['trigger_type'])
        summary['updated'].append(f'WorkflowTemplate({template.code})')
    (summary['created'] if created else summary['existing']).append(
        f'WorkflowTemplate({template.code})'
    )
    _write(
        f'  [WorkflowTemplate] {template.name} -> '
        f'{"CREATED" if created else "EXISTS"}'
    )

    steps_def = [
        (1, 'sales_head_review', 'Sales Head Review'),
        (2, 'finance_review', 'Finance Review'),
        (3, 'admin_approval', 'Admin Approval'),
    ]
    for order, code, name in steps_def:
        _, sc = WorkflowStepTemplate.objects.get_or_create(
            template=template,
            code=code,
            defaults={
                'order': order,
                'name': name,
                'assignment_mode': 'named_user',
                'actor_type': 'internal',
                'on_approve_next': '',
                'requires_comment_on_reject': True,
                'requires_comment_on_request_changes': True,
            },
        )
        (summary['created'] if sc else summary['existing']).append(f'Step({code})')
        _write(f'  [Step] {order}. {name} -> {"CREATED" if sc else "EXISTS"}')

    mapping, mc = WorkflowTemplateMapping.objects.get_or_create(
        org=org,
        trigger_type='sales_proposal',
        client=None,
        site=None,
        is_active=True,
        defaults={'template': template},
    )
    if not mc and mapping.template_id != template.pk:
        mapping.template = template
        mapping.save(update_fields=['template'])
        summary['updated'].append('WorkflowTemplateMapping')
    (summary['created'] if mc else summary['existing']).append('WorkflowTemplateMapping')
    _write(
        f'  [WorkflowTemplateMapping] org default -> '
        f'{"CREATED" if mc else "EXISTS"}'
    )

    route, rc = ApprovalRoute.objects.get_or_create(
        org=org,
        code='sales_proposal_standard',
        defaults={
            'name': 'Sales Proposal Standard Route',
            'trigger_type': 'sales_proposal',
            'template': template,
            'is_default': True,
            'is_active': True,
        },
    )
    if not rc:
        changed = []
        if route.template_id != template.pk:
            route.template = template
            changed.append('template')
        if route.trigger_type != 'sales_proposal':
            route.trigger_type = 'sales_proposal'
            changed.append('trigger_type')
        if changed:
            route.save(update_fields=changed)
            summary['updated'].append(f'ApprovalRoute({route.code})')
    (summary['created'] if rc else summary['existing']).append(
        f'ApprovalRoute({route.code})'
    )
    _write(
        f'  [ApprovalRoute] {route.name} -> {"CREATED" if rc else "EXISTS"}'
    )

    user_args = {
        'sales_head_review': sales_head_user,
        'finance_review': finance_user,
        'admin_approval': admin_user,
    }
    for step_code, user in user_args.items():
        if user is None:
            _write(
                f'  [Assignment] {step_code} -> skipped (no user)'
            )
            continue

        sac, sc = StepAssignmentConfig.objects.get_or_create(
            org=org,
            trigger_type='sales_proposal',
            step_code=step_code,
            client=None,
            site=None,
            is_active=True,
            defaults={'assignment_mode': 'named_user', 'named_user': user},
        )
        if not sc and sac.named_user_id != user.pk:
            sac.named_user = user
            sac.save(update_fields=['named_user'])
            summary['updated'].append(f'StepAssignmentConfig({step_code})')

        arsa, ac = ApprovalRouteStepAssignment.objects.get_or_create(
            route=route,
            step_code=step_code,
            defaults={
                'assignment_mode': 'named_user',
                'named_user': user,
                'is_active': True,
            },
        )
        if not ac and arsa.named_user_id != user.pk:
            arsa.named_user = user
            arsa.save(update_fields=['named_user'])
            summary['updated'].append(f'ApprovalRouteStepAssignment({step_code})')

        _write(f'  [Assignments] {step_code} -> {user.username}')

    return {
        'template': template,
        'mapping': mapping,
        'route': route,
        'summary': summary,
    }

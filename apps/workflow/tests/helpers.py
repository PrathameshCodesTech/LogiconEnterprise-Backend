"""Shared helpers for workflow tests."""

from apps.workflow.models import (
    WorkflowTemplate,
    WorkflowStepTemplate,
    WorkflowTemplateMapping,
    StepAssignmentConfig,
)


def bootstrap_legacy_workflow(org, trigger_type, steps, assignee):
    """
    Create a minimal legacy workflow (template + org mapping + SAC per step).

    steps: list of (order, code, name)
    Returns the WorkflowTemplate.
    """
    code = f'{trigger_type}_test_{assignee.pk}'
    template, _ = WorkflowTemplate.objects.get_or_create(
        org=org,
        code=code,
        defaults={
            'name': f'{trigger_type} test',
            'trigger_type': trigger_type,
            'version': 1,
            'is_active': True,
        },
    )
    if template.trigger_type != trigger_type:
        template.trigger_type = trigger_type
        template.save(update_fields=['trigger_type'])

    for order, step_code, name in steps:
        step, created = WorkflowStepTemplate.objects.get_or_create(
            template=template,
            code=step_code,
            defaults={
                'order': order,
                'name': name,
                'assignment_mode': 'named_user',
                'actor_type': 'internal',
                'on_approve_next': 'END' if order == len(steps) else '',
            },
        )
        if not created and step.order != order:
            step.order = order
            step.save(update_fields=['order'])

    WorkflowTemplateMapping.objects.get_or_create(
        org=org,
        trigger_type=trigger_type,
        client=None,
        site=None,
        is_active=True,
        defaults={'template': template},
    )

    for _order, step_code, _name in steps:
        StepAssignmentConfig.objects.get_or_create(
            org=org,
            trigger_type=trigger_type,
            step_code=step_code,
            client=None,
            site=None,
            is_active=True,
            defaults={
                'assignment_mode': 'named_user',
                'named_user': assignee,
            },
        )

    return template

"""Workflow seeds for Logicon demo."""


def _write(writer, message):
    if writer:
        writer(message)


def seed_logicon_demo_workflows(context, *, writer=None):
    """Seed sales proposal and mobilisation approval workflow routes."""
    org = context['org']
    users = context['users']
    sales_result = _seed_sales_proposal_workflow(org, users, writer)
    mobilisation_result = _seed_mobilisation_workflow(org, users, writer)
    return {
        'sales_proposal': sales_result,
        'mobilisation': mobilisation_result,
    }


def _seed_sales_proposal_workflow(org, users, writer):
    from apps.workflow.seeders import seed_sales_proposal_workflow_route

    result = seed_sales_proposal_workflow_route(
        org,
        sales_head_user=users['sales'],
        finance_user=users['finance'],
        admin_user=users['admin'],
        writer=writer,
    )
    _write(writer, '[LogiconSeed] Sales proposal workflow ready')
    return result


def _seed_mobilisation_workflow(org, users, writer):
    from apps.workflow.models import (
        ApprovalRoute,
        ApprovalRouteStepAssignment,
        StepAssignmentConfig,
        WorkflowStepTemplate,
        WorkflowTemplate,
        WorkflowTemplateMapping,
    )

    template, _ = WorkflowTemplate.objects.get_or_create(
        org=org,
        code='logicon_mobilisation_default',
        defaults={
            'name': 'Logicon Mobilisation Route',
            'trigger_type': 'client_onboarding',
            'version': 1,
            'description': 'Logicon mobilisation approval route.',
            'is_active': True,
        },
    )
    steps = [
        (1, 'sales_submit_review', 'Sales Submit Review', users['sales']),
        (2, 'operations_review', 'Operations Review', users['operations']),
        (3, 'hr_review', 'HR Review', users['hr']),
        (4, 'finance_review', 'Finance Review', users['finance']),
        (5, 'final_admin_review', 'Final Admin Review', users['admin']),
    ]
    for order, code, name, _user in steps:
        WorkflowStepTemplate.objects.get_or_create(
            template=template,
            code=code,
            defaults={
                'order': order,
                'name': name,
                'assignment_mode': 'named_user',
                'actor_type': 'internal',
                'requires_comment_on_reject': True,
                'requires_comment_on_request_changes': True,
            },
        )

    mapping, _ = WorkflowTemplateMapping.objects.get_or_create(
        org=org,
        trigger_type='client_onboarding',
        client=None,
        site=None,
        is_active=True,
        defaults={'template': template},
    )
    if mapping.template_id != template.pk:
        mapping.template = template
        mapping.save(update_fields=['template', 'updated_at'])

    route, _ = ApprovalRoute.objects.get_or_create(
        org=org,
        code='logicon_mobilisation_standard',
        defaults={
            'name': 'Logicon Mobilisation Standard Route',
            'trigger_type': 'client_onboarding',
            'template': template,
            'is_default': True,
            'is_active': True,
        },
    )
    changed = []
    if route.template_id != template.pk:
        route.template = template
        changed.append('template')
    if not route.is_default:
        route.is_default = True
        changed.append('is_default')
    if changed:
        route.save(update_fields=changed + ['updated_at'])

    for _order, code, _name, user in steps:
        StepAssignmentConfig.objects.update_or_create(
            org=org,
            trigger_type='client_onboarding',
            step_code=code,
            client=None,
            site=None,
            is_active=True,
            defaults={
                'assignment_mode': 'named_user',
                'named_user': user,
                'department': user.department,
            },
        )
        ApprovalRouteStepAssignment.objects.update_or_create(
            route=route,
            step_code=code,
            defaults={
                'assignment_mode': 'named_user',
                'named_user': user,
                'department': user.department,
                'is_active': True,
            },
        )
    _write(writer, '[LogiconSeed] Mobilisation workflow ready')
    return {'template': template, 'mapping': mapping, 'route': route}







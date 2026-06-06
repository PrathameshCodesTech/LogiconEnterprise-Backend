"""
ServerUAT workflow route seed.

Creates org-level approval routes for sales proposals, client-raised MRFs,
and mobilisation/client onboarding. Assignments are named-user based and do
not depend on department scopes.
"""

from django.core.management.base import BaseCommand, CommandError


WORKFLOWS = [
    {
        'trigger_type': 'sales_proposal',
        'template_code': 'server_uat_sales_proposal_approval',
        'template_name': 'Server UAT Sales Proposal Approval',
        'route_code': 'server_uat_sales_proposal_standard',
        'route_name': 'Sales Proposal: Finance to Admin',
        'description': 'Sales proposal approval: Finance Manager then Admin.',
        'steps': [
            {
                'order': 1,
                'code': 'finance_review',
                'name': 'Finance Review',
                'user': 'finance.manager',
                'on_approve_next': '',
                'on_reject_target': '',
                'on_request_changes_target': '',
            },
            {
                'order': 2,
                'code': 'admin_review',
                'name': 'Admin Review',
                'user': 'admin.logicon',
                'on_approve_next': 'END',
                'on_reject_target': 'finance_review',
                'on_request_changes_target': 'finance_review',
            },
        ],
    },
    {
        'trigger_type': 'mrf',
        'template_code': 'server_uat_mrf_approval',
        'template_name': 'Server UAT MRF Approval',
        'route_code': 'server_uat_mrf_standard',
        'route_name': 'MRF: HR to Finance to Admin',
        'description': 'Client-raised MRF approval: HR, Finance, then Admin.',
        'steps': [
            {
                'order': 1,
                'code': 'hr_review',
                'name': 'HR Review',
                'user': 'hr.admin',
                'on_approve_next': '',
                'on_reject_target': '',
                'on_request_changes_target': '',
            },
            {
                'order': 2,
                'code': 'finance_review',
                'name': 'Finance Review',
                'user': 'finance.manager',
                'on_approve_next': '',
                'on_reject_target': 'hr_review',
                'on_request_changes_target': 'hr_review',
            },
            {
                'order': 3,
                'code': 'admin_review',
                'name': 'Admin Review',
                'user': 'admin.logicon',
                'on_approve_next': 'END',
                'on_reject_target': 'finance_review',
                'on_request_changes_target': 'finance_review',
            },
        ],
    },
    {
        'trigger_type': 'client_onboarding',
        'template_code': 'server_uat_mobilisation_approval',
        'template_name': 'Server UAT Mobilisation Approval',
        'route_code': 'server_uat_mobilisation_standard',
        'route_name': 'Mobilisation: Admin Approval',
        'description': 'Mobilisation/client onboarding approval: Admin only.',
        'steps': [
            {
                'order': 1,
                'code': 'admin_review',
                'name': 'Admin Review',
                'user': 'admin.logicon',
                'on_approve_next': 'END',
                'on_reject_target': '',
                'on_request_changes_target': '',
            },
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed ServerUAT workflow templates, mappings, routes, and named-user assignments.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Workflow Seed ===\n'))

        org = self._get_org()
        users = self._get_users()

        totals = {'templates': 0, 'routes': 0, 'assignments': 0}
        for definition in WORKFLOWS:
            result = self._seed_workflow(org, users, definition)
            for key, value in result.items():
                totals[key] += value

        self.stdout.write(
            self.style.SUCCESS(
                '\n[OK] ServerUAT workflow seed complete. '
                f'Templates touched: {totals["templates"]}, '
                f'routes touched: {totals["routes"]}, '
                f'assignments touched: {totals["assignments"]}.\n'
            )
        )

    def _get_org(self):
        from apps.core.models import Organization

        try:
            return Organization.objects.get(code='logicon')
        except Organization.DoesNotExist as exc:
            raise CommandError(
                'Organization "logicon" does not exist. Run seed_server_uat foundation first.'
            ) from exc

    def _get_users(self):
        from apps.accounts.models import User

        usernames = {
            step['user']
            for workflow in WORKFLOWS
            for step in workflow['steps']
        }
        users = {
            user.username: user
            for user in User.objects.filter(username__in=usernames, is_active=True)
        }
        missing = sorted(usernames.difference(users.keys()))
        if missing:
            raise CommandError(
                'Missing active users required for workflow seed: '
                f'{", ".join(missing)}. Run seed_server_uat foundation first.'
            )
        return users

    def _seed_workflow(self, org, users, definition):
        from apps.workflow.models import (
            ApprovalRoute,
            ApprovalRouteStepAssignment,
            StepAssignmentConfig,
            WorkflowStepTemplate,
            WorkflowTemplate,
            WorkflowTemplateMapping,
        )

        template = self._upsert_template(WorkflowTemplate, org, definition)
        steps_touched = self._upsert_steps(WorkflowStepTemplate, template, definition)
        mapping_touched = self._upsert_mapping(WorkflowTemplateMapping, org, template, definition)
        route = self._upsert_route(ApprovalRoute, org, template, definition)
        assignments_touched = self._upsert_assignments(
            ApprovalRouteStepAssignment,
            StepAssignmentConfig,
            org,
            route,
            users,
            definition,
        )

        self.stdout.write(
            f'  [Workflow] {definition["trigger_type"]}: '
            f'template={template.code}, route={route.code}, '
            f'steps={steps_touched}, mapping={mapping_touched}, assignments={assignments_touched}'
        )
        return {
            'templates': 1,
            'routes': 1,
            'assignments': assignments_touched,
        }

    def _upsert_template(self, model, org, definition):
        template, created = model.objects.get_or_create(
            org=org,
            code=definition['template_code'],
            defaults={
                'name': definition['template_name'],
                'trigger_type': definition['trigger_type'],
                'version': 1,
                'description': definition['description'],
                'is_active': True,
            },
        )
        changed_fields = []
        for field, value in {
            'name': definition['template_name'],
            'trigger_type': definition['trigger_type'],
            'version': 1,
            'description': definition['description'],
            'is_active': True,
        }.items():
            if getattr(template, field) != value:
                setattr(template, field, value)
                changed_fields.append(field)
        if changed_fields:
            template.save(update_fields=changed_fields)
        self.stdout.write(
            f'  [WorkflowTemplate] {template.code} - {"CREATED" if created else "EXISTS"}'
        )
        return template

    def _upsert_steps(self, model, template, definition):
        touched = 0
        wanted_codes = {step['code'] for step in definition['steps']}

        for step in definition['steps']:
            step_obj, created = model.objects.get_or_create(
                template=template,
                code=step['code'],
                defaults={
                    'order': step['order'],
                    'name': step['name'],
                    'assignment_mode': 'named_user',
                    'actor_type': 'internal',
                    'on_approve_next': step['on_approve_next'],
                    'on_reject_target': step['on_reject_target'],
                    'on_request_changes_target': step['on_request_changes_target'],
                    'requires_comment_on_reject': True,
                    'requires_comment_on_request_changes': True,
                    'sla_hours': None,
                },
            )
            changed_fields = []
            for field, value in {
                'order': step['order'],
                'name': step['name'],
                'assignment_mode': 'named_user',
                'actor_type': 'internal',
                'on_approve_next': step['on_approve_next'],
                'on_reject_target': step['on_reject_target'],
                'on_request_changes_target': step['on_request_changes_target'],
                'requires_comment_on_reject': True,
                'requires_comment_on_request_changes': True,
                'sla_hours': None,
            }.items():
                if getattr(step_obj, field) != value:
                    setattr(step_obj, field, value)
                    changed_fields.append(field)
            if changed_fields:
                step_obj.save(update_fields=changed_fields)
            if created or changed_fields:
                touched += 1

        stale_steps = template.steps.exclude(code__in=wanted_codes)
        if stale_steps.exists():
            stale_steps.delete()
            touched += 1

        return touched

    def _upsert_mapping(self, model, org, template, definition):
        mapping, created = model.objects.get_or_create(
            org=org,
            trigger_type=definition['trigger_type'],
            client=None,
            site=None,
            is_active=True,
            defaults={'template': template},
        )
        changed_fields = []
        if mapping.template_id != template.pk:
            mapping.template = template
            changed_fields.append('template')
        if not mapping.is_active:
            mapping.is_active = True
            changed_fields.append('is_active')
        if changed_fields:
            mapping.save(update_fields=changed_fields)
        return 1 if created or changed_fields else 0

    def _upsert_route(self, model, org, template, definition):
        route, created = model.objects.get_or_create(
            org=org,
            code=definition['route_code'],
            defaults={
                'name': definition['route_name'],
                'trigger_type': definition['trigger_type'],
                'template': template,
                'client': None,
                'site': None,
                'description': definition['description'],
                'is_default': True,
                'is_active': True,
            },
        )
        changed_fields = []
        for field, value in {
            'name': definition['route_name'],
            'trigger_type': definition['trigger_type'],
            'template': template,
            'client': None,
            'site': None,
            'description': definition['description'],
            'is_default': True,
            'is_active': True,
        }.items():
            current_value = getattr(route, field)
            if current_value != value:
                setattr(route, field, value)
                changed_fields.append(field)
        if changed_fields:
            route.save(update_fields=changed_fields)
        self.stdout.write(
            f'  [ApprovalRoute] {route.code} - {"CREATED" if created else "EXISTS"}'
        )
        return route

    def _upsert_assignments(self, route_assignment_model, config_model, org, route, users, definition):
        touched = 0
        wanted_codes = {step['code'] for step in definition['steps']}

        for step in definition['steps']:
            user = users[step['user']]
            _, route_created = route_assignment_model.objects.update_or_create(
                route=route,
                step_code=step['code'],
                defaults={
                    'department': None,
                    'assignment_mode': 'named_user',
                    'named_user': user,
                    'note': f'ServerUAT assignment for {step["name"]}',
                    'is_active': True,
                },
            )
            _, config_created = config_model.objects.update_or_create(
                org=org,
                trigger_type=definition['trigger_type'],
                step_code=step['code'],
                client=None,
                site=None,
                is_active=True,
                defaults={
                    'assignment_mode': 'named_user',
                    'named_user': user,
                    'department': None,
                },
            )
            if route_created or config_created:
                touched += 1

        route.step_assignments.exclude(step_code__in=wanted_codes).update(is_active=False)
        config_model.objects.filter(
            org=org,
            trigger_type=definition['trigger_type'],
            client=None,
            site=None,
            is_active=True,
        ).exclude(step_code__in=wanted_codes).update(is_active=False)

        return touched or len(wanted_codes)

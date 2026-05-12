"""
apps/workflow/management/commands/seed_client_onboarding_workflow_demo.py

Creates a demo client_onboarding workflow template for local/dev testing.

Usage:
    python manage.py seed_client_onboarding_workflow_demo
    python manage.py seed_client_onboarding_workflow_demo --org logicon
    python manage.py seed_client_onboarding_workflow_demo --org logicon \
        --operations-user suresh.ops --hr-user jyoti.hr --finance-user bhakti.finance
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'Seed a demo client_onboarding workflow template with 5 steps '
        '(sales_submit_review, operations_review, hr_review, finance_review, final_admin_review).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--org', default='logicon', help='Organization code (default: logicon)')
        parser.add_argument('--operations-user', default=None, help='Username for operations_review step')
        parser.add_argument('--hr-user', default=None, help='Username for hr_review step')
        parser.add_argument('--finance-user', default=None, help='Username for finance_review step')
        parser.add_argument('--admin-user', default=None, help='Username for final_admin_review step')
        parser.add_argument('--sales-user', default=None, help='Username for sales_submit_review step')

    def handle(self, *args, **options):
        from apps.core.models import Organization
        from apps.workflow.models import (
            WorkflowTemplate, WorkflowStepTemplate,
            WorkflowTemplateMapping, StepAssignmentConfig,
        )
        from apps.accounts.models import User

        self.stdout.write('=== Client Onboarding Workflow Demo Seed ===')

        org_code = options['org']
        try:
            org = Organization.objects.get(code=org_code)
        except Organization.DoesNotExist:
            raise CommandError(f'Organization with code "{org_code}" not found.')

        # ── Template ──────────────────────────────────────────────────────────
        template, created = WorkflowTemplate.objects.get_or_create(
            org=org,
            code='client_onboarding_default',
            defaults={
                'name': 'Client Onboarding Default Workflow',
                'trigger_type': 'client_onboarding',
                'version': 1,
                'description': 'Standard onboarding: Sales -> Ops -> HR -> Finance -> Admin.',
                'is_active': True,
            },
        )
        label = 'CREATED' if created else 'EXISTS'
        self.stdout.write(f'  [WorkflowTemplate] {template.name} -> {label}')

        # ── Steps ─────────────────────────────────────────────────────────────
        steps_def = [
            (1, 'sales_submit_review', 'Sales Submit Review'),
            (2, 'operations_review', 'Operations Review'),
            (3, 'hr_review', 'HR Review'),
            (4, 'finance_review', 'Finance Review'),
            (5, 'final_admin_review', 'Final Admin Review'),
        ]
        for order, code, name in steps_def:
            step, created = WorkflowStepTemplate.objects.get_or_create(
                template=template,
                code=code,
                defaults={
                    'order': order,
                    'name': name,
                    'assignment_mode': 'named_user',
                    'actor_type': 'internal',
                    'on_approve_next': '',
                    'on_reject_target': '',
                    'requires_comment_on_reject': True,
                    'requires_comment_on_request_changes': True,
                },
            )
            label = 'CREATED' if created else 'EXISTS'
            self.stdout.write(f'  [Step] {order}. {name} -> {label}')

        # ── Org-level template mapping ─────────────────────────────────────────
        mapping, created = WorkflowTemplateMapping.objects.get_or_create(
            org=org,
            trigger_type='client_onboarding',
            client=None,
            site=None,
            is_active=True,
            defaults={'template': template},
        )
        if not created and mapping.template != template:
            mapping.template = template
            mapping.save(update_fields=['template'])
        label = 'CREATED' if created else 'EXISTS'
        self.stdout.write(f'  [WorkflowTemplateMapping] org default -> {template.code} -> {label}')

        # ── Assignment configs ─────────────────────────────────────────────────
        user_args = {
            'sales_submit_review': options['sales_user'],
            'operations_review': options['operations_user'],
            'hr_review': options['hr_user'],
            'finance_review': options['finance_user'],
            'final_admin_review': options['admin_user'],
        }

        any_provided = any(v is not None for v in user_args.values())
        if not any_provided:
            self.stdout.write(
                self.style.WARNING(
                    '  Assignment configs not created (no users provided).'
                )
            )
            self.stdout.write(
                '  Pass --operations-user, --hr-user, --finance-user, etc. to create configs.'
            )
        else:
            for step_code, username in user_args.items():
                if username is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  [StepAssignmentConfig] {step_code} -> skipped (no user provided)'
                        )
                    )
                    continue
                try:
                    user = User.objects.get(username=username, org=org)
                except User.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(
                            f'  [StepAssignmentConfig] {step_code} -> user "{username}" not found in org "{org_code}", skipping.'
                        )
                    )
                    continue

                config, created = StepAssignmentConfig.objects.get_or_create(
                    org=org,
                    trigger_type='client_onboarding',
                    step_code=step_code,
                    client=None,
                    site=None,
                    is_active=True,
                    defaults={
                        'assignment_mode': 'named_user',
                        'named_user': user,
                    },
                )
                if not created and config.named_user != user:
                    config.named_user = user
                    config.save(update_fields=['named_user'])
                label = 'CREATED' if created else 'EXISTS'
                self.stdout.write(f'  [StepAssignmentConfig] {step_code} -> {username} -> {label}')

        self.stdout.write(self.style.SUCCESS('\n[OK] Client onboarding workflow demo seed complete.'))

"""
apps/workflow/management/commands/seed_client_onboarding_workflow_demo.py

Creates a demo mobilisation workflow template (trigger_type client_onboarding) for local/dev testing.
Fully idempotent — safe to run multiple times.

Usage:
    python manage.py seed_client_onboarding_workflow_demo
    python manage.py seed_client_onboarding_workflow_demo --org logicon
    python manage.py seed_client_onboarding_workflow_demo --org logicon \
        --sales-user rohan.sales \
        --operations-user suresh.ops \
        --hr-user jyoti.hr \
        --finance-user bhakti.finance \
        --admin-user admin
"""

from django.core.management.base import BaseCommand, CommandError

# Dept codes expected for each step (org-level departments).
# None = step has no department restriction.
STEP_DEPT_CODES = {
    'sales_submit_review': 'sales',
    'operations_review': 'operations',
    'hr_review': 'hr',
    'finance_review': 'finance',
    'final_admin_review': None,
}

DEMO_SUMMARY = 'Demo mobilisation setup request for newly closed client'


class Command(BaseCommand):
    help = (
        'Seed a demo Mobilisation Approval workflow template (trigger client_onboarding) with 5 steps '
        '(sales_submit_review, operations_review, hr_review, finance_review, final_admin_review). '
        'Fully idempotent — safe to run multiple times.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--org', default='logicon', help='Organization code (default: logicon)')
        parser.add_argument('--sales-user', default=None, help='Username for sales_submit_review step')
        parser.add_argument('--operations-user', default=None, help='Username for operations_review step')
        parser.add_argument('--hr-user', default=None, help='Username for hr_review step')
        parser.add_argument('--finance-user', default=None, help='Username for finance_review step')
        parser.add_argument('--admin-user', default=None, help='Username for final_admin_review step')

    def handle(self, *args, **options):
        from apps.core.models import Organization, Department
        from apps.workflow.models import (
            WorkflowTemplate, WorkflowStepTemplate,
            WorkflowTemplateMapping, StepAssignmentConfig,
        )
        from apps.accounts.models import User

        self.stdout.write('=== Mobilisation Workflow Demo Seed ===')

        org_code = options['org']
        try:
            org = Organization.objects.get(code=org_code)
        except Organization.DoesNotExist:
            raise CommandError(f'Organization with code "{org_code}" not found.')

        self.stdout.write(f'  Org: {org.name} ({org.code})')

        # ── Template ──────────────────────────────────────────────────────────
        self.stdout.write('\n--- Workflow Template ---')
        template, created = WorkflowTemplate.objects.get_or_create(
            org=org,
            code='client_onboarding_default',
            defaults={
                'name': 'Mobilisation Standard Route',
                'trigger_type': 'client_onboarding',
                'version': 1,
                'description': 'Mobilisation Approval: Sales -> Ops -> HR -> Finance -> Admin.',
                'is_active': True,
            },
        )
        label = 'CREATED' if created else 'EXISTS'
        self.stdout.write(f'  [WorkflowTemplate] {template.name} (code={template.code}) -> {label}')

        # ── Steps ─────────────────────────────────────────────────────────────
        self.stdout.write('\n--- Workflow Steps ---')
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
            self.stdout.write(f'  [Step] {order}. {name} (code={code}) -> {label}')

        # ── Org-level template mapping ─────────────────────────────────────────
        self.stdout.write('\n--- Template Mapping ---')
        mapping, created = WorkflowTemplateMapping.objects.get_or_create(
            org=org,
            trigger_type='client_onboarding',
            client=None,
            site=None,
            is_active=True,
            defaults={'template': template},
        )
        if not created and mapping.template_id != template.pk:
            mapping.template = template
            mapping.save(update_fields=['template'])
            label = 'UPDATED'
        else:
            label = 'CREATED' if created else 'EXISTS'
        self.stdout.write(
            f'  [WorkflowTemplateMapping] org default -> {template.code} '
            f'(mapping_level=org) -> {label}'
        )

        # ── Assignment configs ─────────────────────────────────────────────────
        self.stdout.write('\n--- Step Assignment Configs ---')
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
                    '  Assignment configs not created (no users provided).\n'
                    '  Pass --sales-user, --operations-user, --hr-user, --finance-user, --admin-user.'
                )
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
                            f'  [StepAssignmentConfig] {step_code} -> '
                            f'user "{username}" not found in org "{org_code}", skipping.'
                        )
                    )
                    continue

                # Resolve expected department for this step
                dept = self._resolve_dept(org, step_code, user, Department)

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
                        'department': dept,
                    },
                )

                # Update named_user or department if they changed on an existing record
                if not created:
                    changed_fields = []
                    if config.named_user_id != user.pk:
                        config.named_user = user
                        changed_fields.append('named_user')
                    expected_dept_id = dept.pk if dept else None
                    if config.department_id != expected_dept_id:
                        config.department = dept
                        changed_fields.append('department')
                    if changed_fields:
                        config.save(update_fields=changed_fields)
                        label = 'UPDATED'
                    else:
                        label = 'EXISTS'
                else:
                    label = 'CREATED'

                dept_label = dept.name if dept else 'none'
                self.stdout.write(
                    f'  [StepAssignmentConfig] {step_code} -> {username} '
                    f'(dept: {dept_label}) -> {label}'
                )

        # ── Demo mobilisation setup request ─────────────────────────────────────
        self.stdout.write('\n--- Demo Mobilisation Setup Request ---')
        self._seed_demo_request(org, options, User)

        self.stdout.write(self.style.SUCCESS('\n[OK] Mobilisation workflow demo seed complete.'))

    # ── Private helpers ────────────────────────────────────────────────────────

    def _resolve_dept(self, org, step_code, user, Department):
        """
        Return the expected Department for this step, or None.

        Rules:
        - Look up the expected dept code from STEP_DEPT_CODES.
        - If no dept code configured for this step, return None.
        - If dept not found in DB, warn and return None.
        - If user's department doesn't match the expected dept, warn and return None
          (rather than creating a misconfigured SAC that would fail at workflow start).
        """
        dept_code = STEP_DEPT_CODES.get(step_code)
        if not dept_code:
            return None

        dept = Department.objects.filter(
            org=org, code=dept_code, client__isnull=True, site__isnull=True,
        ).first()

        if dept is None:
            self.stdout.write(
                self.style.WARNING(
                    f'    [dept] code="{dept_code}" not found in org — '
                    f'step {step_code} will have no department restriction.'
                )
            )
            return None

        if user.department_id != dept.pk:
            user_dept_name = user.department.name if user.department else 'none'
            self.stdout.write(
                self.style.WARNING(
                    f'    [dept] User "{user.username}" is in dept "{user_dept_name}", '
                    f'expected "{dept.name}" for step {step_code} — '
                    f'skipping department assignment to avoid misconfiguration.'
                )
            )
            return None

        return dept

    def _seed_demo_request(self, org, options, User):
        """Create or confirm the demo MobilisationSetupRequest."""
        from apps.mobilisation.models import MobilisationSetupRequest
        from apps.sites.models import Client

        # Prefer ABC Infrastructure; fall back to first available client
        client = (
            Client.objects.filter(org=org, code='ABC-INFRA').first()
            or Client.objects.filter(org=org).first()
        )
        if client is None:
            self.stdout.write(
                self.style.WARNING('  [DemoRequest] No clients found in this org — skipping.')
            )
            return

        # Resolve requested_by: prefer --sales-user, then 'rohan.sales', then any active user
        sales_username = options.get('sales_user') or 'rohan.sales'
        try:
            requested_by = User.objects.get(username=sales_username, org=org)
        except User.DoesNotExist:
            requested_by = User.objects.filter(org=org, is_active=True).first()
            if requested_by is None:
                self.stdout.write(
                    self.style.WARNING('  [DemoRequest] No active users found — skipping.')
                )
                return
            self.stdout.write(
                self.style.WARNING(
                    f'  [DemoRequest] User "{sales_username}" not found; '
                    f'using "{requested_by.username}" as requested_by.'
                )
            )

        req, created = MobilisationSetupRequest.objects.get_or_create(
            org=org,
            client=client,
            mobilisation_type='new_client',
            summary=DEMO_SUMMARY,
            defaults={
                'requested_by': requested_by,
                'status': 'draft',
            },
        )
        label = 'CREATED' if created else 'EXISTS'
        self.stdout.write(
            f'  [DemoRequest] id={req.pk} | client={client.name} | '
            f'type={req.mobilisation_type} | status={req.status} -> {label}'
        )
        self.stdout.write(f'    summary="{req.summary}"')
        self.stdout.write(f'    requested_by={req.requested_by.username}')

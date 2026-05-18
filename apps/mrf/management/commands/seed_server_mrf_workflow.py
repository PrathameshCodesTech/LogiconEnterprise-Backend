"""
Seed server MRF workflow configuration only.

Idempotent by business codes. Safe to run repeatedly after migrations.
This command intentionally does not create demo clients, sites, budgets, SRRs,
or MRFs. It only prepares the MRF approval workflow so MRFs created from real
onboarded clients can be sent for approval.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Seed Logicon server MRF workflow config only.'

    def add_arguments(self, parser):
        parser.add_argument('--org', default='logicon')
        parser.add_argument('--password', default='Logicon@2025')
        parser.add_argument(
            '--skip-password-reset',
            action='store_true',
            help='Do not reset passwords for seeded internal users.',
        )

    def handle(self, *args, **options):
        self.org_code = options['org']
        self.password = options['password']
        self.reset_passwords = not options['skip_password_reset']

        with transaction.atomic():
            self.org = self._seed_org()
            self.scope_nodes = self._seed_scope_nodes()
            self.departments = self._seed_departments()
            self.roles = self._seed_roles_and_permissions()
            self.users = self._seed_internal_users()
            self.template, self.route = self._seed_mrf_workflow()

        self.stdout.write(self.style.SUCCESS('MRF workflow seed complete.'))
        self.stdout.write(f'Template: {self.template.name} ({self.template.code})')
        self.stdout.write(f'Route: {self.route.name} ({self.route.code})')
        self.stdout.write('Assignments:')
        self.stdout.write(f'  HR Review: {self.users["hr"].email}')
        self.stdout.write(f'  Finance Review: {self.users["finance"].email}')
        self.stdout.write(f'  Admin Review: {self.users["admin"].email}')

    def _seed_org(self):
        from apps.core.models import Organization

        org, _ = Organization.objects.update_or_create(
            code=self.org_code,
            defaults={'name': 'Logicon Facility Management', 'is_active': True},
        )
        return org

    def _seed_scope_nodes(self):
        from apps.core.models import ScopeNode

        company, _ = ScopeNode.objects.update_or_create(
            org=self.org,
            code=self.org.code,
            parent=None,
            defaults={
                'name': 'Logicon',
                'node_type': 'company',
                'path': self.org.code,
                'depth': 0,
                'is_active': True,
            },
        )
        return {'company': company}

    def _seed_departments(self):
        from apps.core.models import Department

        defs = [
            ('sales', 'Sales'),
            ('operations', 'Operations'),
            ('hr', 'Human Resources'),
            ('finance', 'Finance'),
            ('admin', 'Admin'),
        ]
        rows = {}
        for code, name in defs:
            dept, _ = Department.objects.update_or_create(
                org=self.org,
                code=code,
                client=None,
                site=None,
                defaults={
                    'name': name,
                    'description': f'{name} department',
                    'is_active': True,
                },
            )
            rows[code] = dept
        return rows

    def _seed_roles_and_permissions(self):
        from apps.access.capabilities import ALL_CAPABILITIES, ROLE_CAPABILITIES
        from apps.access.models import AccessRole, AccessRolePermission, Permission

        permissions = {}
        for cap in ALL_CAPABILITIES:
            parts = cap.split('.')
            if len(parts) == 2:
                resource, action = parts
            else:
                resource = '_'.join(parts[:-1])
                action = parts[-1]
            perm, _ = Permission.objects.get_or_create(
                code=cap,
                defaults={
                    'resource': resource,
                    'action': action,
                    'description': f'Can {action} {cap}',
                },
            )
            permissions[cap] = perm

        role_defs = [
            ('admin', 'System Administrator', ''),
            ('hr_manager', 'HR Manager', ''),
            ('finance_manager', 'Finance Manager', ''),
        ]
        roles = {}
        for code, name, scope in role_defs:
            role, _ = AccessRole.objects.update_or_create(
                org=self.org,
                code=code,
                defaults={'name': name, 'node_type_scope': scope, 'is_active': True},
            )
            roles[code] = role
            for cap in ROLE_CAPABILITIES.get(code, []):
                perm = permissions.get(cap)
                if perm:
                    AccessRolePermission.objects.get_or_create(role=role, permission=perm)
        return roles

    def _seed_internal_users(self):
        from apps.access.models import UserRoleAssignment

        User = get_user_model()
        company = self.scope_nodes['company']
        defs = {
            'admin': {
                'username': 'admin',
                'email': 'admin@logicon.in',
                'first_name': 'Admin',
                'last_name': 'Logicon',
                'department': None,
                'role': 'admin',
                'is_superuser': True,
                'is_staff': True,
            },
            'hr': {
                'username': 'jyoti.hr',
                'email': 'jyoti.hr@logicon.in',
                'first_name': 'Jyoti',
                'last_name': 'HR',
                'department': self.departments['hr'],
                'role': 'hr_manager',
                'is_superuser': False,
                'is_staff': False,
            },
            'finance': {
                'username': 'bhakti.finance',
                'email': 'bhakti.finance@logicon.in',
                'first_name': 'Bhakti',
                'last_name': 'Finance',
                'department': self.departments['finance'],
                'role': 'finance_manager',
                'is_superuser': False,
                'is_staff': False,
            },
        }
        users = {}
        for key, data in defs.items():
            role_code = data.pop('role')
            user, created = User.objects.update_or_create(
                username=data['username'],
                defaults={
                    **data,
                    'org': self.org,
                    'user_type': 'internal',
                    'is_active': True,
                },
            )
            if created or self.reset_passwords:
                user.set_password(self.password)
                user.save(update_fields=['password'])
            UserRoleAssignment.objects.get_or_create(
                user=user,
                role=self.roles[role_code],
                scope_node=company,
            )
            users[key] = user
        return users

    def _seed_mrf_workflow(self):
        from apps.workflow.models import (
            ApprovalRoute,
            ApprovalRouteStepAssignment,
            StepAssignmentConfig,
            WorkflowStepTemplate,
            WorkflowTemplate,
            WorkflowTemplateMapping,
        )

        template, _ = WorkflowTemplate.objects.update_or_create(
            org=self.org,
            code='mrf_hr_finance_admin_v1',
            defaults={
                'name': 'MRF HR Finance Admin Approval',
                'trigger_type': 'mrf',
                'version': 1,
                'description': 'HR, Finance, Admin approval for MRF.',
                'is_active': True,
            },
        )
        steps = [
            {
                'order': 1,
                'code': 'hr_review',
                'name': 'HR Review',
                'approve_next': '',
                'reject_target': '',
                'changes_target': 'hr_review',
            },
            {
                'order': 2,
                'code': 'finance_review',
                'name': 'Finance Review',
                'approve_next': '',
                'reject_target': 'hr_review',
                'changes_target': 'hr_review',
            },
            {
                'order': 3,
                'code': 'admin_review',
                'name': 'Admin Review',
                'approve_next': 'END',
                'reject_target': 'finance_review',
                'changes_target': 'hr_review',
            },
        ]
        for step in steps:
            WorkflowStepTemplate.objects.update_or_create(
                template=template,
                code=step['code'],
                defaults={
                    'order': step['order'],
                    'name': step['name'],
                    'assignment_mode': 'named_user',
                    'actor_type': 'internal',
                    'on_approve_next': step['approve_next'],
                    'on_reject_target': step['reject_target'],
                    'on_request_changes_target': step['changes_target'],
                    'requires_comment_on_reject': True,
                    'requires_comment_on_request_changes': True,
                },
            )

        WorkflowTemplateMapping.objects.update_or_create(
            org=self.org,
            trigger_type='mrf',
            client=None,
            site=None,
            defaults={'template': template, 'is_active': True},
        )

        ApprovalRoute.objects.filter(
            org=self.org,
            trigger_type='mrf',
            client__isnull=True,
            site__isnull=True,
            is_default=True,
        ).exclude(code='mrf_standard_route').update(is_default=False)
        route, _ = ApprovalRoute.objects.update_or_create(
            org=self.org,
            code='mrf_standard_route',
            defaults={
                'name': 'Standard MRF Approval Route',
                'trigger_type': 'mrf',
                'template': template,
                'client': None,
                'site': None,
                'description': 'Default MRF route: HR, Finance, Admin.',
                'is_default': True,
                'is_active': True,
            },
        )

        assignments = {
            'hr_review': ('hr', 'hr'),
            'finance_review': ('finance', 'finance'),
            'admin_review': ('admin', None),
        }
        for step_code, (user_key, dept_key) in assignments.items():
            dept = self.departments.get(dept_key) if dept_key else None
            StepAssignmentConfig.objects.update_or_create(
                org=self.org,
                trigger_type='mrf',
                step_code=step_code,
                client=None,
                site=None,
                defaults={
                    'department': dept,
                    'assignment_mode': 'named_user',
                    'named_user': self.users[user_key],
                    'is_active': True,
                },
            )
            ApprovalRouteStepAssignment.objects.update_or_create(
                route=route,
                step_code=step_code,
                defaults={
                    'department': dept,
                    'assignment_mode': 'named_user',
                    'named_user': self.users[user_key],
                    'is_active': True,
                },
            )
        return template, route

"""
Seed demo sales proposal internal approval workflow (trigger_type sales_proposal).

This command is a thin wrapper around the reusable seeder at
`apps.workflow.seeders.seed_sales_proposal_workflow_route`, which is also
called by `seed_sales_demo`. Do not import this command's internals from other
commands; use the seeder module instead.

Usage:
    python manage.py seed_sales_proposal_workflow_demo
    python manage.py seed_sales_proposal_workflow_demo --org logicon \\
        --sales-head-user rohan.sales --finance-user bhakti.finance --admin-user admin
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'Seed Sales Proposal internal approval workflow template (trigger sales_proposal) '
        'with Sales Head, Finance, and Admin steps. Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--org', default='logicon', help='Organization code (default: logicon)')
        parser.add_argument('--sales-head-user', default=None, help='Username for sales_head_review')
        parser.add_argument('--finance-user', default=None, help='Username for finance_review')
        parser.add_argument('--admin-user', default=None, help='Username for admin_approval')

    def handle(self, *args, **options):
        from apps.core.models import Organization
        from apps.accounts.models import User
        from apps.workflow.seeders import seed_sales_proposal_workflow_route

        org_code = options['org']
        try:
            org = Organization.objects.get(code=org_code)
        except Organization.DoesNotExist:
            raise CommandError(f'Organization with code "{org_code}" not found.')

        self.stdout.write('=== Sales Proposal Workflow Demo Seed ===')
        self.stdout.write(f'  Org: {org.name} ({org.code})')

        def _resolve_user(username):
            if not username:
                return None
            try:
                return User.objects.get(username=username, org=org)
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'  [Assignment] user "{username}" not found')
                )
                return None

        seed_sales_proposal_workflow_route(
            org,
            sales_head_user=_resolve_user(options['sales_head_user']),
            finance_user=_resolve_user(options['finance_user']),
            admin_user=_resolve_user(options['admin_user']),
            writer=self.stdout.write,
        )

        self.stdout.write(self.style.SUCCESS('\n[OK] Sales proposal workflow demo seed complete.'))

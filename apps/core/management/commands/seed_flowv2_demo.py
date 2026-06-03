"""Seed the Logicon demo state for browser verification."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.flowv2_seed.flowv2_base import reset_logicon_demo_org, seed_logicon_demo_base
from apps.core.flowv2_seed.flowv2_constants import LOGICON_DEMO_ORG_CODE
from apps.core.flowv2_seed.flowv2_sales import seed_logicon_demo_sales_ready
from apps.core.flowv2_seed.flowv2_summary import (
    build_logicon_demo_summary,
    print_logicon_demo_summary,
)
from apps.core.flowv2_seed.flowv2_workflows import seed_logicon_demo_workflows


class Command(BaseCommand):
    help = (
        'Seed the Logicon demo org, users, master data, workflows, and a '
        'proposal-ready sales lead. Idempotent by default.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--org', default=LOGICON_DEMO_ORG_CODE, help='Demo organization code.')
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete only a disposable Logicon demo org/users before seeding.',
        )
        parser.add_argument(
            '--frontend-base-url',
            default='http://127.0.0.1:5173',
            help='Frontend base URL used in printed links.',
        )
        parser.add_argument(
            '--won-ready',
            action='store_true',
            help='Advance the seeded sales proposal to client-approved/won, ready for mobilisation conversion.',
        )
        parser.add_argument(
            '--lead-client-name',
            default=None,
            help='Optional client name for an additional sales lead case.',
        )
        parser.add_argument(
            '--lead-site-name',
            default=None,
            help='Optional site name for an additional sales lead case.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        org_code = options['org']
        if options['reset']:
            try:
                reset_logicon_demo_org(org_code=org_code, writer=self.stdout.write)
            except RuntimeError as exc:
                raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== Logicon Demo Seed ({org_code}) ===\n'))
        context = seed_logicon_demo_base(org_code=org_code, writer=self.stdout.write)
        workflow_context = seed_logicon_demo_workflows(context, writer=self.stdout.write)
        context['workflows'] = workflow_context
        sales_context = seed_logicon_demo_sales_ready(
            context,
            writer=self.stdout.write,
            won_ready=options['won_ready'],
            **({
                'client_name': options['lead_client_name'],
                'site_name': options['lead_site_name'] or f"{options['lead_client_name']} Pune Site",
            } if options['lead_client_name'] else {}),
        )

        summary = build_logicon_demo_summary(
            context,
            sales_context,
            frontend_base_url=options['frontend_base_url'].rstrip('/'),
        )
        print_logicon_demo_summary(summary, self.stdout.write)
        self.stdout.write(self.style.SUCCESS('[OK] Logicon demo seed complete.'))







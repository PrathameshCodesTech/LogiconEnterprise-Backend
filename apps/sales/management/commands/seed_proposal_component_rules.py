from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Organization
from apps.sales.proposal_calculation import seed_default_proposal_component_rules


class Command(BaseCommand):
    help = (
        'Seed default ProposalComponentRule rows. Without --org-code, seeds '
        'global rules that every org can inherit.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--org-code',
            help='Seed rules for one organization instead of global defaults.',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Reset existing matching rules to default values.',
        )

    def handle(self, *args, **options):
        org_code = options.get('org_code')
        org = None
        if org_code:
            try:
                org = Organization.objects.get(code=org_code)
            except Organization.DoesNotExist as exc:
                raise CommandError(f'Organization with code "{org_code}" not found.') from exc

        counts = seed_default_proposal_component_rules(
            org=org,
            overwrite=options['overwrite'],
        )
        scope = f'org {org.code}' if org else 'global'
        self.stdout.write(
            self.style.SUCCESS(
                'Seeded proposal component rules for '
                f'{scope}: created={counts["created"]}, '
                f'updated={counts["updated"]}, unchanged={counts["unchanged"]}'
            )
        )

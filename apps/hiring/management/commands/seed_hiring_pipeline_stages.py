from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Organization
from apps.hiring.lifecycle import ensure_default_pipeline_stages


class Command(BaseCommand):
    help = 'Seed standard hiring pipeline stages for one organization or all organizations.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--org-code',
            dest='org_code',
            help='Organization code to seed. If omitted, all active organizations are seeded.',
        )

    def handle(self, *args, **options):
        org_code = options.get('org_code')
        if org_code:
            try:
                orgs = [Organization.objects.get(code=org_code)]
            except Organization.DoesNotExist as exc:
                raise CommandError(f'Organization with code "{org_code}" was not found.') from exc
        else:
            orgs = list(Organization.objects.filter(is_active=True).order_by('id'))

        if not orgs:
            self.stdout.write(self.style.WARNING('No organizations found to seed.'))
            return

        for org in orgs:
            stages = ensure_default_pipeline_stages(org)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Seeded {len(stages)} hiring pipeline stages for {org.code}.'
                )
            )

"""
ServerUAT survey seed.

This seed aligns the operations survey Deployment tab with the ServerUAT job
roles and creates the SurveyRoleMapping rows used to generate SRRs.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError


SURVEY_ROLE_MAPPING_SPECS = [
    ('Electrician', 'electrician', 'skilled', 'Technical'),
    ('Plumber', 'plumber', 'skilled', 'Technical'),
    ('MST', 'mst', 'skilled', 'Technical'),
    ('HVAC', 'hvac', 'skilled', 'Technical'),
    ('Carpenter', 'carpenter', 'skilled', 'Civil'),
    ('Painter', 'painter', 'skilled', 'Civil'),
    ('Mason', 'mason', 'skilled', 'Civil'),
    ('Helper', 'helper', 'unskilled', 'Support'),
    ('HTP Operator', 'htp_operator', 'skilled', 'Technical'),
    ('WTP Operator', 'wtp_operator', 'skilled', 'Technical'),
]

LEGACY_DEPLOYMENT_DESCRIPTIONS = [
    'Site Management team',
    'Technical(8 Hrs x 6 days)',
    'Tech Supervisor',
    'STP',
    'HK Supervisor',
    'Janitor',
    'Machinery',
    'HK Consumables',
    'Sub Total',
    'TOTAL',
]

SHIFT_HOURS = Decimal('8.0')
WORKING_DAYS = Decimal('26.0')


class Command(BaseCommand):
    help = 'Seed ServerUAT survey deployment role mappings and clean active survey rows.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Survey Seed ===\n'))

        org = self._get_org()
        job_roles = self._get_job_roles(org)
        wage_categories = self._get_wage_categories()
        mapping_counts = self._seed_survey_role_mappings(org, job_roles, wage_categories)
        survey_counts = self._align_existing_surveys(org)

        self.stdout.write(
            self.style.SUCCESS(
                '\n[OK] ServerUAT survey seed complete. '
                f'Mappings: {mapping_counts}. Existing surveys: {survey_counts}.\n'
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

    def _get_job_roles(self, org):
        from apps.jobs.models import JobRole

        roles = {
            role.code: role
            for role in JobRole.objects.filter(org=org, is_active=True)
        }
        missing = [
            role_code
            for _description, role_code, _wage_code, _service_category
            in SURVEY_ROLE_MAPPING_SPECS
            if role_code not in roles
        ]
        if missing:
            raise CommandError(
                'Missing ServerUAT job roles. Run seed_server_uat masters first. '
                f'Missing: {", ".join(missing)}'
            )
        return roles

    def _get_wage_categories(self):
        from apps.wages.models import WageCategory

        categories = {
            category.code: category
            for category in WageCategory.objects.all()
        }
        missing = sorted({
            wage_code
            for _description, _role_code, wage_code, _service_category
            in SURVEY_ROLE_MAPPING_SPECS
            if wage_code not in categories
        })
        if missing:
            raise CommandError(
                'Missing wage categories. Run seed_server_uat masters first. '
                f'Missing: {", ".join(missing)}'
            )
        return categories

    def _seed_survey_role_mappings(self, org, job_roles, wage_categories):
        from apps.sales.models import SurveyRoleMapping

        desired_descriptions = [
            description
            for description, _role_code, _wage_code, _service_category
            in SURVEY_ROLE_MAPPING_SPECS
        ]

        retired = SurveyRoleMapping.objects.filter(
            org=org,
            description_text__in=LEGACY_DEPLOYMENT_DESCRIPTIONS,
            is_active=True,
        ).exclude(description_text__in=desired_descriptions).update(is_active=False)

        created = updated = unchanged = 0
        for description, role_code, wage_code, service_category in SURVEY_ROLE_MAPPING_SPECS:
            expected = {
                'job_role': job_roles[role_code],
                'wage_category': wage_categories[wage_code],
                'service_category': service_category,
                'shift_hours': SHIFT_HOURS,
                'working_days': WORKING_DAYS,
                'is_active': True,
                'remarks': 'ServerUAT survey role mapping.',
            }
            mapping, was_created = SurveyRoleMapping.objects.get_or_create(
                org=org,
                description_text=description,
                defaults=expected,
            )
            changed_fields = []
            for field, value in expected.items():
                current = getattr(mapping, f'{field}_id') if hasattr(value, 'pk') else getattr(mapping, field)
                expected_value = value.pk if hasattr(value, 'pk') else value
                if current != expected_value:
                    setattr(mapping, field, value)
                    changed_fields.append(field)
            if changed_fields:
                mapping.save(update_fields=changed_fields + ['updated_at'])

            if was_created:
                created += 1
                status = 'CREATED'
            elif changed_fields:
                updated += 1
                status = 'UPDATED'
            else:
                unchanged += 1
                status = 'EXISTS'

            self.stdout.write(
                f'  [SurveyRoleMapping] {description} -> {role_code} / {wage_code} - {status}'
            )

        if retired:
            self.stdout.write(f'  [SurveyRoleMapping] retired legacy mappings: {retired}')
        return {'created': created, 'updated': updated, 'unchanged': unchanged, 'retired': retired}

    def _align_existing_surveys(self, org):
        from apps.sales.models import SiteSurvey, SiteSurveyShiftDeployment

        desired_rows = [
            (index, description)
            for index, (description, _role_code, _wage_code, _service_category)
            in enumerate(SURVEY_ROLE_MAPPING_SPECS, start=1)
        ]
        surveys = SiteSurvey.objects.filter(
            lead__org=org,
            status__in=['pending', 'in_progress'],
        )

        created = updated = existing = deleted = retired = 0
        for survey in surveys:
            for sort_order, description in desired_rows:
                row, was_created = SiteSurveyShiftDeployment.objects.get_or_create(
                    survey=survey,
                    description=description,
                    defaults={
                        'line_type': 'item',
                        'sort_order': sort_order,
                        'is_applicable': True,
                    },
                )
                if was_created:
                    created += 1
                    continue

                changed_fields = []
                for field, value in {
                    'line_type': 'item',
                    'sort_order': sort_order,
                    'is_applicable': True,
                }.items():
                    if getattr(row, field) != value:
                        setattr(row, field, value)
                        changed_fields.append(field)
                if changed_fields:
                    row.save(update_fields=changed_fields + ['updated_at'])
                    updated += 1
                else:
                    existing += 1

            legacy_rows = SiteSurveyShiftDeployment.objects.filter(
                survey=survey,
                description__in=LEGACY_DEPLOYMENT_DESCRIPTIONS,
            )
            for row in legacy_rows:
                if self._is_empty_shift_row(row):
                    row.delete()
                    deleted += 1
                    continue
                row.is_applicable = False
                row.not_applicable_reason = 'Legacy ServerUAT deployment row retired.'
                row.sort_order = 900 + row.sort_order
                row.save(update_fields=['is_applicable', 'not_applicable_reason', 'sort_order', 'updated_at'])
                retired += 1

        return {
            'surveys': surveys.count(),
            'created': created,
            'updated': updated,
            'existing': existing,
            'deleted_legacy_empty': deleted,
            'retired_legacy_with_data': retired,
        }

    def _is_empty_shift_row(self, row):
        return (
            not row.remarks.strip()
            and row.general_count == 0
            and row.first_shift_count == 0
            and row.second_shift_count == 0
            and row.total_count == 0
        )

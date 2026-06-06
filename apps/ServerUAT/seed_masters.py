"""
ServerUAT master/config seed for operational lookup data.

This seed contains only masters needed before user-driven UAT flows:
job roles, wage geography, wage categories, and minimum wage rates.
It does not create clients, sites, SRRs, sales records, MRFs, hiring records,
or deployments.
"""

import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError


JOB_ROLES = [
    ('electrician', 'Electrician', 'skilled', 'Electrical maintenance and repair'),
    ('plumber', 'Plumber', 'skilled', 'Plumbing maintenance and repair'),
    ('mst', 'MST', 'skilled', 'Multi-skilled technician'),
    ('hvac', 'HVAC', 'skilled', 'HVAC operation and maintenance'),
    ('carpenter', 'Carpenter', 'skilled', 'Carpentry and civil maintenance support'),
    ('painter', 'Painter', 'skilled', 'Painting and finishing work'),
    ('mason', 'Mason', 'skilled', 'Masonry and civil repair work'),
    ('helper', 'Helper', 'unskilled', 'General helper and support work'),
    ('htp_operator', 'HTP Operator', 'skilled', 'HTP operations support'),
    ('wtp_operator', 'WTP Operator', 'skilled', 'WTP operations support'),
]

WAGE_CATEGORIES = [
    ('unskilled', 'Unskilled', 'Unskilled wage category'),
    ('semi_skilled', 'Semi-Skilled', 'Semi-skilled wage category'),
    ('skilled', 'Skilled', 'Skilled wage category'),
    ('highly_skilled', 'Highly Skilled', 'Highly skilled wage category'),
    ('supervisor', 'Supervisor', 'Supervisor wage category'),
]

LOCATION_TREE = [
    ('maharashtra', 'Maharashtra', 'state', None),
    ('pune', 'Pune', 'city', 'maharashtra'),
    ('pune_metro', 'Pune Metro', 'zone', 'pune'),
    ('mumbai', 'Mumbai', 'city', 'maharashtra'),
    ('mumbai_metro', 'Mumbai Metro', 'zone', 'mumbai'),
]

MINIMUM_WAGE_RATES = {
    'pune_metro': {
        'unskilled': ('15000.00', '576.92'),
        'semi_skilled': ('17000.00', '653.85'),
        'skilled': ('19000.00', '730.77'),
        'highly_skilled': ('21000.00', '807.69'),
        'supervisor': ('22000.00', '846.15'),
    },
    'mumbai_metro': {
        'unskilled': ('16000.00', '615.38'),
        'semi_skilled': ('18000.00', '692.31'),
        'skilled': ('20000.00', '769.23'),
        'highly_skilled': ('23000.00', '884.62'),
        'supervisor': ('24000.00', '923.08'),
    },
}

ROLE_SPECIFIC_WAGE_RATES = {
    'pune_metro': {
        'electrician': ('skilled', '19000.00', '730.77'),
        'plumber': ('skilled', '18500.00', '711.54'),
        'mst': ('skilled', '21000.00', '807.69'),
        'hvac': ('skilled', '22000.00', '846.15'),
        'carpenter': ('skilled', '18000.00', '692.31'),
        'painter': ('skilled', '17500.00', '673.08'),
        'mason': ('skilled', '18000.00', '692.31'),
        'helper': ('unskilled', '15000.00', '576.92'),
        'htp_operator': ('skilled', '20500.00', '788.46'),
        'wtp_operator': ('skilled', '20500.00', '788.46'),
    },
    'mumbai_metro': {
        'electrician': ('skilled', '20000.00', '769.23'),
        'plumber': ('skilled', '19500.00', '750.00'),
        'mst': ('skilled', '22500.00', '865.38'),
        'hvac': ('skilled', '23500.00', '903.85'),
        'carpenter': ('skilled', '19000.00', '730.77'),
        'painter': ('skilled', '18500.00', '711.54'),
        'mason': ('skilled', '19000.00', '730.77'),
        'helper': ('unskilled', '16000.00', '615.38'),
        'htp_operator': ('skilled', '21500.00', '826.92'),
        'wtp_operator': ('skilled', '21500.00', '826.92'),
    },
}

WAGE_EFFECTIVE_FROM = datetime.date(2026, 1, 1)
SOURCE_NOTE = 'server_uat_seed'


class Command(BaseCommand):
    help = 'Seed ServerUAT masters: job roles, wage locations, wage categories, and wage rates.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Masters Seed ===\n'))

        org = self._get_org()
        job_roles = self._seed_job_roles(org)
        wage_categories = self._seed_wage_categories()
        locations = self._seed_location_areas()
        self._seed_minimum_wage_rates(org, locations, wage_categories, job_roles)

        self.stdout.write(
            self.style.SUCCESS(
                f'\n[OK] ServerUAT masters seed complete. Job roles available: {len(job_roles)}.\n'
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

    def _seed_job_roles(self, org):
        from apps.jobs.models import JobRole

        roles = {}
        for code, name, skill_category, description in JOB_ROLES:
            role, created = JobRole.objects.get_or_create(
                org=org,
                code=code,
                defaults={
                    'name': name,
                    'description': description,
                    'skill_category': skill_category,
                    'is_active': True,
                },
            )
            changed_fields = []
            for field, value in {
                'name': name,
                'description': description,
                'skill_category': skill_category,
                'is_active': True,
            }.items():
                if getattr(role, field) != value:
                    setattr(role, field, value)
                    changed_fields.append(field)
            if changed_fields:
                role.save(update_fields=changed_fields)
            roles[code] = role
            self.stdout.write(
                f'  [JobRole] {code} / {name} ({skill_category}) - {"CREATED" if created else "EXISTS"}'
            )
        return roles

    def _seed_wage_categories(self):
        from apps.wages.models import WageCategory

        categories = {}
        for code, name, description in WAGE_CATEGORIES:
            category, created = WageCategory.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'description': description,
                },
            )
            changed_fields = []
            for field, value in {
                'name': name,
                'description': description,
            }.items():
                if getattr(category, field) != value:
                    setattr(category, field, value)
                    changed_fields.append(field)
            if changed_fields:
                category.save(update_fields=changed_fields)
            categories[code] = category
            self.stdout.write(
                f'  [WageCategory] {code} / {name} - {"CREATED" if created else "EXISTS"}'
            )
        return categories

    def _seed_location_areas(self):
        from apps.wages.models import LocationArea

        locations = {}
        for code, name, area_type, parent_code in LOCATION_TREE:
            parent = locations.get(parent_code) if parent_code else None
            state_name = name if area_type == 'state' else self._state_name_for(parent)
            location, created = LocationArea.objects.get_or_create(
                parent=parent,
                code=code,
                defaults={
                    'name': name,
                    'area_type': area_type,
                    'state_name': state_name,
                    'is_active': True,
                },
            )
            changed_fields = []
            for field, value in {
                'name': name,
                'area_type': area_type,
                'state_name': state_name,
                'is_active': True,
            }.items():
                if getattr(location, field) != value:
                    setattr(location, field, value)
                    changed_fields.append(field)
            if changed_fields:
                location.save(update_fields=changed_fields)
            locations[code] = location
            parent_label = parent.code if parent else 'root'
            self.stdout.write(
                f'  [LocationArea] {code} / {name} ({area_type}, parent={parent_label}) - '
                f'{"CREATED" if created else "EXISTS"}'
            )
        return locations

    def _state_name_for(self, location):
        node = location
        while node is not None:
            if node.area_type == 'state':
                return node.name
            node = node.parent
        return ''

    def _seed_minimum_wage_rates(self, org, locations, wage_categories, job_roles):
        from apps.wages.models import MinimumWageRate

        category_rows = 0
        for location_code, category_rates in MINIMUM_WAGE_RATES.items():
            location = locations[location_code]
            for category_code, amounts in category_rates.items():
                category = wage_categories[category_code]
                monthly_wage, daily_wage = amounts
                created = self._upsert_wage_rate(
                    MinimumWageRate,
                    org=org,
                    location=location,
                    wage_category=category,
                    role=None,
                    monthly_wage=monthly_wage,
                    daily_wage=daily_wage,
                )
                category_rows += 1
                self.stdout.write(
                    f'  [MinimumWageRate] {location_code} / {category_code} = {monthly_wage} monthly - '
                    f'{"CREATED" if created else "EXISTS"}'
                )

        role_rows = 0
        for location_code, role_rates in ROLE_SPECIFIC_WAGE_RATES.items():
            location = locations[location_code]
            for role_code, values in role_rates.items():
                category_code, monthly_wage, daily_wage = values
                role = job_roles[role_code]
                category = wage_categories[category_code]
                created = self._upsert_wage_rate(
                    MinimumWageRate,
                    org=org,
                    location=location,
                    wage_category=category,
                    role=role,
                    monthly_wage=monthly_wage,
                    daily_wage=daily_wage,
                )
                role_rows += 1
                self.stdout.write(
                    f'  [MinimumWageRate] {location_code} / {role_code} / {category_code} = '
                    f'{monthly_wage} monthly - {"CREATED" if created else "EXISTS"}'
                )

        self.stdout.write(
            f'  [MinimumWageRate] category fallback rows: {category_rows}, role-specific rows: {role_rows}'
        )

    def _upsert_wage_rate(
        self,
        model,
        *,
        org,
        location,
        wage_category,
        role,
        monthly_wage,
        daily_wage,
    ):
        rate, created = model.objects.get_or_create(
            org=org,
            location=location,
            wage_category=wage_category,
            role=role,
            effective_from=WAGE_EFFECTIVE_FROM,
            defaults={
                'state': location.state_name,
                'city': location.name,
                'monthly_wage': Decimal(monthly_wage),
                'daily_wage': Decimal(daily_wage),
                'effective_to': None,
                'source_note': SOURCE_NOTE,
                'is_active': True,
            },
        )
        changed_fields = []
        for field, value in {
            'state': location.state_name,
            'city': location.name,
            'monthly_wage': Decimal(monthly_wage),
            'daily_wage': Decimal(daily_wage),
            'effective_to': None,
            'source_note': SOURCE_NOTE,
            'is_active': True,
        }.items():
            if getattr(rate, field) != value:
                setattr(rate, field, value)
                changed_fields.append(field)
        if changed_fields:
            rate.save(update_fields=changed_fields)
        return created

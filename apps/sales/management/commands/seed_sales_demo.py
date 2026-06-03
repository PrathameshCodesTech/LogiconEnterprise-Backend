"""
seed_sales_demo
===============

End-to-end idempotent seed for the sales pipeline demo:

  Organization → Users → Job roles/Wage categories/Location/Min wage rates
  → Sales proposal workflow route
  → A `new_client` lead and a `site_expansion` lead
  → One SiteSurvey with seeded default Excel lines
  → One proposal-ready SalesRoleRequirement set

Usage:
    python manage.py seed_sales_demo
    python manage.py seed_sales_demo --org logicon-demo

Safe to run multiple times: every step is implemented with get_or_create/update_or_create.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction


DEFAULT_ORG_CODE = 'logicon-demo'
DEFAULT_ORG_NAME = 'Logicon Demo'

# Stable usernames so re-runs idempotently find the same users
SALES_USER = 'demo.sales'
OPS_USER = 'demo.ops'
FINANCE_USER = 'demo.finance'
ADMIN_USER = 'demo.admin'

# Job roles seeded with names matching the default Excel shift descriptions
# so generate_role_requirements_from_survey can find them.
DEMO_JOB_ROLES = [
    ('electrician', 'Electrician', 'skilled'),
    ('plumber', 'Plumber', 'skilled'),
    ('security_guard', 'Security Guard', 'unskilled'),
]


class Command(BaseCommand):
    help = (
        'Seed an end-to-end sales demo: org, users, master data, workflow route, '
        'demo leads, survey with default lines, proposal-ready role requirements. '
        'Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--org', default=DEFAULT_ORG_CODE, help='Organization code')

    @transaction.atomic
    def handle(self, *args, **options):
        org_code = options['org']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== seed_sales_demo (org={org_code}) ===\n'
        ))

        org = self._seed_organization(org_code)
        org_node = self._seed_company_scope_node(org)
        users = self._seed_users(org)
        roles = self._seed_job_roles(org)
        categories = self._seed_wage_categories()
        location = self._seed_location_area()
        self._seed_minimum_wages(org, location, roles, categories)
        self._seed_proposal_component_rules(org)
        self._seed_workflow_route(org, users)

        existing_demo_client = self._seed_demo_existing_client(org, org_node)
        new_client_lead, new_client_site = self._seed_new_client_lead(org, users, roles, categories, location)
        site_expansion_lead, site_expansion_site = self._seed_site_expansion_lead(
            org, users, roles, categories, location, existing_demo_client,
        )

        survey = self._seed_survey_with_default_lines(new_client_lead, new_client_site, users)
        self._seed_proposal_ready_role_requirements(
            new_client_lead, new_client_site, survey, roles, categories,
        )

        self.stdout.write(self.style.SUCCESS(
            '\n[OK] seed_sales_demo complete. Demo lead is ready for proposal generation.'
        ))

    def _seed_organization(self, code):
        from apps.core.models import Organization
        org, created = Organization.objects.get_or_create(
            code=code,
            defaults={'name': DEFAULT_ORG_NAME, 'is_active': True},
        )
        self.stdout.write(f'  [Organization] {org.name} ({org.code}) -> '
                          f'{"CREATED" if created else "EXISTS"}')
        return org

    def _seed_proposal_component_rules(self, org):
        from apps.sales.proposal_calculation import seed_default_proposal_component_rules

        counts = seed_default_proposal_component_rules(org=org)
        self.stdout.write(
            '  [ProposalComponentRule] '
            f'created={counts["created"]} updated={counts["updated"]} '
            f'unchanged={counts["unchanged"]}'
        )

    def _seed_company_scope_node(self, org):
        from apps.core.models import ScopeNode
        node, created = ScopeNode.objects.get_or_create(
            org=org, code=org.code, node_type='company', parent=None,
            defaults={
                'name': org.name,
                'depth': 0,
                'path': org.code,
                'is_active': True,
            },
        )
        self.stdout.write(f'  [ScopeNode] company {org.code} -> '
                          f'{"CREATED" if created else "EXISTS"}')
        return node

    def _seed_users(self, org):
        from apps.accounts.models import User
        results = {}
        for username, label in [
            (SALES_USER, 'Sales User'),
            (OPS_USER, 'Operations User'),
            (FINANCE_USER, 'Finance User'),
            (ADMIN_USER, 'Admin User'),
        ]:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'first_name': label, 'is_active': True},
            )
            updated_fields = []
            if user.org_id != org.pk:
                user.org = org
                updated_fields.append('org')
            if not user.has_usable_password():
                user.set_password('demo1234')
                updated_fields.append('password')
            if updated_fields:
                user.save(update_fields=updated_fields if 'password' not in updated_fields else None)
            results[username] = user
            self.stdout.write(f'  [User] {username} -> '
                              f'{"CREATED" if created else "EXISTS"}')
        return results

    def _seed_job_roles(self, org):
        from apps.jobs.models import JobRole
        results = {}
        for code, name, skill in DEMO_JOB_ROLES:
            role, created = JobRole.objects.get_or_create(
                org=org, code=code,
                defaults={'name': name, 'skill_category': skill, 'is_active': True},
            )
            results[code] = role
            self.stdout.write(f'  [JobRole] {code} -> {"CREATED" if created else "EXISTS"}')
        return results

    def _seed_wage_categories(self):
        from apps.wages.models import WageCategory
        results = {}
        for code, name in [('skilled', 'Skilled'), ('unskilled', 'Unskilled')]:
            cat, created = WageCategory.objects.get_or_create(
                code=code, defaults={'name': name, 'description': ''},
            )
            results[code] = cat
            self.stdout.write(f'  [WageCategory] {code} -> {"CREATED" if created else "EXISTS"}')
        return results

    def _seed_location_area(self):
        from apps.wages.models import LocationArea
        state, _ = LocationArea.objects.get_or_create(
            code='mh-demo', parent=None,
            defaults={
                'name': 'Maharashtra (demo)',
                'area_type': 'state',
                'state_name': 'Maharashtra',
                'is_active': True,
            },
        )
        city, created = LocationArea.objects.get_or_create(
            code='pune-demo', parent=state,
            defaults={
                'name': 'Pune (demo)',
                'area_type': 'city',
                'state_name': 'Maharashtra',
                'is_active': True,
            },
        )
        self.stdout.write(f'  [LocationArea] pune-demo -> {"CREATED" if created else "EXISTS"}')
        return city

    def _seed_minimum_wages(self, org, location, roles, categories):
        from apps.wages.models import MinimumWageRate
        seeded = 0
        existed = 0
        for role_code, role in roles.items():
            cat_code = 'skilled' if role_code in ('electrician', 'plumber') else 'unskilled'
            cat = categories[cat_code]
            monthly_wage = Decimal('18000') if cat_code == 'skilled' else Decimal('14000')
            _, created = MinimumWageRate.objects.get_or_create(
                org=org, location=location, wage_category=cat, role=role,
                effective_from=date(2025, 1, 1),
                defaults={
                    'monthly_wage': monthly_wage,
                    'daily_wage': (monthly_wage / Decimal('26')).quantize(Decimal('0.01')),
                    'is_active': True,
                    'source_note': 'seed_sales_demo',
                },
            )
            if created:
                seeded += 1
            else:
                existed += 1
        self.stdout.write(
            f'  [MinimumWageRate] created={seeded} existed={existed}'
        )

    def _seed_workflow_route(self, org, users):
        from apps.workflow.seeders import seed_sales_proposal_workflow_route
        result = seed_sales_proposal_workflow_route(
            org,
            sales_head_user=users[SALES_USER],
            finance_user=users[FINANCE_USER],
            admin_user=users[ADMIN_USER],
            writer=self.stdout.write,
        )
        return result

    def _seed_demo_existing_client(self, org, org_node):
        """Create a stable existing Client used by the site_expansion demo lead."""
        from apps.core.models import ScopeNode
        from apps.sites.models import Client

        client, created = Client.objects.get_or_create(
            org=org, code='demo-existing-client',
            defaults={
                'name': 'Demo Existing Client',
                'is_active': True,
            },
        )
        if client.scope_node_id is None:
            path = f'{org_node.path}/demo-existing-client'
            node, _ = ScopeNode.objects.get_or_create(
                org=org, code='demo-existing-client', parent=org_node,
                defaults={
                    'name': client.name,
                    'node_type': 'client',
                    'depth': org_node.depth + 1,
                    'path': path,
                    'is_active': True,
                },
            )
            client.scope_node = node
            client.save(update_fields=['scope_node'])
        self.stdout.write(
            f'  [Client] demo-existing-client -> {"CREATED" if created else "EXISTS"}'
        )
        return client

    def _seed_new_client_lead(self, org, users, roles, categories, location):
        from apps.sales.models import SalesLead, SalesLeadSite
        lead, created = SalesLead.objects.get_or_create(
            org=org, lead_type='new_client',
            client_name='Acme Logistics (Demo)',
            defaults={
                'client_email': 'sales+demo@acme.example',
                'current_stage': 'draft',
                'current_status': 'draft',
                'created_by': users[SALES_USER],
            },
        )
        site, _ = SalesLeadSite.objects.get_or_create(
            lead=lead, site_name='Acme Hub 1',
            defaults={
                'city': 'Pune',
                'state': 'Maharashtra',
                'is_active': True,
                'location_area': location,
            },
        )
        if site.location_area_id is None:
            site.location_area = location
            site.save(update_fields=['location_area'])
        self.stdout.write(
            f'  [SalesLead/new_client] {lead.client_name} -> '
            f'{"CREATED" if created else "EXISTS"}'
        )
        return lead, site

    def _seed_site_expansion_lead(self, org, users, roles, categories, location, existing_client):
        from apps.sales.models import SalesLead, SalesLeadSite
        lead, created = SalesLead.objects.get_or_create(
            org=org, lead_type='site_expansion',
            existing_client=existing_client,
            client_name=existing_client.name,
            defaults={
                'client_email': 'expansion+demo@acme.example',
                'current_stage': 'draft',
                'current_status': 'draft',
                'created_by': users[SALES_USER],
            },
        )
        site, _ = SalesLeadSite.objects.get_or_create(
            lead=lead, site_name='Beta Wing B',
            defaults={
                'city': 'Pune',
                'state': 'Maharashtra',
                'is_active': True,
                'location_area': location,
            },
        )
        if site.location_area_id is None:
            site.location_area = location
            site.save(update_fields=['location_area'])
        self.stdout.write(
            f'  [SalesLead/site_expansion] {lead.client_name} -> '
            f'{"CREATED" if created else "EXISTS"}'
        )
        return lead, site

    def _seed_survey_with_default_lines(self, lead, site, users):
        from apps.sales.models import SiteSurvey
        from apps.sales.services import seed_default_survey_lines

        survey, created = SiteSurvey.objects.get_or_create(
            lead=lead, site=site,
            defaults={
                'status': 'pending',
                'assigned_to': users[OPS_USER],
            },
        )
        counts = seed_default_survey_lines(survey, overwrite=False)
        self.stdout.write(
            f'  [SiteSurvey] {survey.pk} -> '
            f'{"CREATED" if created else "EXISTS"} (rows: {counts})'
        )
        return survey

    def _seed_proposal_ready_role_requirements(self, lead, site, survey, roles, categories):
        """Seed one SRR per JobRole so proposal calculation has manpower to price."""
        from apps.sales.models import SalesRoleRequirement
        created_count = 0
        existing_count = 0
        for role_code, role in roles.items():
            cat_code = 'skilled' if role_code in ('electrician', 'plumber') else 'unskilled'
            cat = categories[cat_code]
            _, created = SalesRoleRequirement.objects.get_or_create(
                lead=lead, site=site, survey=survey, job_role=role,
                defaults={
                    'wage_category': cat,
                    'manpower_count': 2,
                    'is_active': True,
                    'created_from_survey': True,
                },
            )
            if created:
                created_count += 1
            else:
                existing_count += 1
        self.stdout.write(
            f'  [SalesRoleRequirement] created={created_count} existed={existing_count}'
        )

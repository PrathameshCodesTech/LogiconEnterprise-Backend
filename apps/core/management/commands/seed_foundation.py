"""
management/commands/seed_foundation.py

Idempotent seed command for Logicon ATS foundation data.
Uses get_or_create throughout — safe to run multiple times.
"""

import datetime
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Seed foundation data for Logicon ATS (idempotent)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Logicon ATS — Foundation Seed ===\n'))

        org = self._seed_organization()
        scope_nodes = self._seed_scope_nodes(org)
        roles = self._seed_access_roles(org)
        permissions = self._seed_permissions()
        self._seed_role_permissions(roles, permissions)
        self._seed_job_roles(org)
        self._seed_wage_categories()
        location_areas = self._seed_location_areas()
        self._seed_minimum_wage_rates(org, location_areas)
        client = self._seed_client(org, scope_nodes)
        site = self._seed_site_profile(org, client, scope_nodes, location_areas)
        self._seed_site_role_requirements(site)
        self._seed_module_activations(scope_nodes)
        depts = self._seed_departments(org)
        self._seed_budget_plans(org, client, depts)
        self._seed_users(org, roles, scope_nodes)

        self.stdout.write(self.style.SUCCESS('\n[OK] Foundation seed complete.\n'))

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_organization(self):
        from apps.core.models import Organization
        org, created = Organization.objects.get_or_create(
            code='logicon',
            defaults={'name': 'Logicon Facility Management', 'is_active': True},
        )
        self.stdout.write(f'  [Organization] {org.name} — {"CREATED" if created else "EXISTS"}')
        return org

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_scope_nodes(self, org):
        from apps.core.models import ScopeNode

        nodes = {}

        def make_node(code, name, node_type, parent, depth, path):
            obj, created = ScopeNode.objects.get_or_create(
                org=org,
                parent=parent,
                code=code,
                defaults={
                    'name': name,
                    'node_type': node_type,
                    'depth': depth,
                    'path': path,
                    'is_active': True,
                },
            )
            self.stdout.write(f'  [ScopeNode] {path} ({node_type}) — {"CREATED" if created else "EXISTS"}')
            return obj

        nodes['company'] = make_node(
            code='logicon', name='Logicon', node_type='company',
            parent=None, depth=0, path='logicon',
        )
        department_defs = [
            ('sales', 'Sales'),
            ('operations', 'Operations'),
            ('finance', 'Finance'),
            ('hr-admin', 'HR/Admin'),
            ('support', 'Support'),
        ]
        for code, name in department_defs:
            nodes[f'dept_{code}'] = make_node(
                code=code, name=name, node_type='department',
                parent=nodes['company'], depth=1, path=f'logicon/{code}',
            )
        nodes['west'] = make_node(
            code='west', name='West Region', node_type='region',
            parent=nodes['company'], depth=1, path='logicon/west',
        )
        nodes['mumbai'] = make_node(
            code='mumbai', name='Mumbai', node_type='city',
            parent=nodes['west'], depth=2, path='logicon/west/mumbai',
        )
        return nodes

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_access_roles(self, org):
        from apps.access.models import AccessRole

        role_defs = [
            ('admin', 'Admin', None),
            ('hr_admin', 'HR Admin', None),
            ('hr_executive', 'HR Executive', None),
            ('finance', 'Finance', None),
            ('hod', 'Head of Department', 'department'),
            ('sales_manager', 'Sales Manager', None),
            ('sales_executive', 'Sales Executive', None),
            ('site_manager', 'Site Manager', 'site'),
            ('field_supervisor', 'Field Supervisor', 'site'),
            ('client_admin', 'Client Admin', 'client'),
            ('client_site_user', 'Client Site User', 'site'),
            ('site_supervisor', 'Site Supervisor', 'site'),
            ('client_user', 'Client User', None),
        ]

        roles = {}
        for code, name, scope in role_defs:
            obj, created = AccessRole.objects.get_or_create(
                org=org,
                code=code,
                defaults={
                    'name': name,
                    'node_type_scope': scope or '',
                    'is_active': True,
                },
            )
            self.stdout.write(f'  [AccessRole] {code} — {"CREATED" if created else "EXISTS"}')
            roles[code] = obj
        return roles

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_permissions(self):
        """
        Create one Permission row per capability in ALL_CAPABILITIES, keyed by code.
        For 2-part strings (resource.action) resource/action are populated directly.
        For 3-part strings (workflow.config.read) resource = joined prefix with '_'.
        """
        from apps.access.models import Permission
        from apps.access.capabilities import ALL_CAPABILITIES

        permissions = {}
        created_count = 0
        existed_count = 0

        for cap in ALL_CAPABILITIES:
            parts = cap.split('.')
            if len(parts) == 2:
                resource, action = parts
            else:
                resource = '_'.join(parts[:-1])  # e.g. workflow_config
                action = parts[-1]

            obj, created = Permission.objects.get_or_create(
                code=cap,
                defaults={
                    'resource': resource,
                    'action': action,
                    'description': f'Can {action} {cap}',
                },
            )
            permissions[cap] = obj
            if created:
                created_count += 1
            else:
                existed_count += 1

        self.stdout.write(
            f'  [Permission] Created: {created_count}, Existed: {existed_count}'
        )
        return permissions

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_role_permissions(self, roles, permissions):
        from apps.access.models import AccessRolePermission
        from apps.access.capabilities import ROLE_CAPABILITIES

        created_count = 0
        existed_count = 0
        skipped_count = 0

        for role_code, role_obj in roles.items():
            caps = ROLE_CAPABILITIES.get(role_code, [])
            for cap in caps:
                perm = permissions.get(cap)
                if not perm:
                    skipped_count += 1
                    continue
                _, created = AccessRolePermission.objects.get_or_create(
                    role=role_obj,
                    permission=perm,
                )
                if created:
                    created_count += 1
                else:
                    existed_count += 1

        msg = f'  [AccessRolePermission] Created: {created_count}, Existed: {existed_count}'
        if skipped_count:
            msg += f', Skipped (no Permission row): {skipped_count}'
        self.stdout.write(msg)

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_job_roles(self, org):
        from apps.jobs.models import JobRole

        job_role_defs = [
            ('Housekeeping', 'housekeeping', 'unskilled'),
            ('Security Guard', 'security_guard', 'unskilled'),
            ('Electrician', 'electrician', 'skilled'),
            ('Plumber', 'plumber', 'skilled'),
            ('Supervisor', 'supervisor', 'supervisor'),
        ]

        for name, code, skill_category in job_role_defs:
            obj, created = JobRole.objects.get_or_create(
                org=org,
                code=code,
                defaults={'name': name, 'skill_category': skill_category, 'is_active': True},
            )
            self.stdout.write(f'  [JobRole] {name} — {"CREATED" if created else "EXISTS"}')

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_wage_categories(self):
        from apps.wages.models import WageCategory

        categories = [
            ('Unskilled', 'unskilled'),
            ('Semi-Skilled', 'semi_skilled'),
            ('Skilled', 'skilled'),
            ('Highly Skilled', 'highly_skilled'),
        ]

        for name, code in categories:
            obj, created = WageCategory.objects.get_or_create(
                code=code,
                defaults={'name': name},
            )
            self.stdout.write(f'  [WageCategory] {name} — {"CREATED" if created else "EXISTS"}')

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_location_areas(self):
        from apps.wages.models import LocationArea

        areas = {}

        def make_area(code, name, area_type, parent, state_name=''):
            obj, created = LocationArea.objects.get_or_create(
                parent=parent,
                code=code,
                defaults={
                    'name': name,
                    'area_type': area_type,
                    'state_name': state_name,
                    'is_active': True,
                },
            )
            self.stdout.write(f'  [LocationArea] {name} ({area_type}) — {"CREATED" if created else "EXISTS"}')
            return obj

        areas['maharashtra'] = make_area('maharashtra', 'Maharashtra', 'state', None, 'Maharashtra')
        areas['west_region'] = make_area('west-region', 'West Region', 'region', areas['maharashtra'], 'Maharashtra')
        areas['mumbai'] = make_area('mumbai', 'Mumbai', 'city', areas['west_region'], 'Maharashtra')
        areas['pune'] = make_area('pune', 'Pune', 'city', areas['west_region'], 'Maharashtra')
        return areas

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_minimum_wage_rates(self, org, location_areas):
        from apps.jobs.models import JobRole
        from apps.wages.models import WageCategory, MinimumWageRate

        effective_from = datetime.date(2025, 1, 1)
        mumbai = location_areas['mumbai']

        rate_defs = [
            {
                'job_role_code': 'housekeeping',
                'wage_category_code': 'unskilled',
                'monthly_wage': 10000,
                'daily_wage': 385,
                'source_note': 'Maharashtra MW Jan 2025 — Housekeeping',
            },
            {
                'job_role_code': 'security_guard',
                'wage_category_code': 'unskilled',
                'monthly_wage': 12000,
                'daily_wage': 462,
                'source_note': 'Maharashtra MW Jan 2025 — Security Guard',
            },
            {
                'job_role_code': 'electrician',
                'wage_category_code': 'skilled',
                'monthly_wage': 18000,
                'daily_wage': 692,
                'source_note': 'Maharashtra MW Jan 2025 — Electrician',
            },
        ]

        for defn in rate_defs:
            try:
                job_role = JobRole.objects.get(org=org, code=defn['job_role_code'])
                wage_cat = WageCategory.objects.get(code=defn['wage_category_code'])
            except (JobRole.DoesNotExist, WageCategory.DoesNotExist):
                self.stdout.write(
                    self.style.WARNING(
                        f'  [MinimumWageRate] Skipped {defn["job_role_code"]} — role/category not found'
                    )
                )
                continue

            obj, created = MinimumWageRate.objects.get_or_create(
                org=org,
                location=mumbai,
                wage_category=wage_cat,
                role=job_role,
                effective_from=effective_from,
                defaults={
                    'monthly_wage': defn['monthly_wage'],
                    'daily_wage': defn['daily_wage'],
                    'source_note': defn['source_note'],
                    'is_active': True,
                },
            )
            self.stdout.write(
                f'  [MinimumWageRate] {job_role.name} @ Mumbai — {"CREATED" if created else "EXISTS"}'
            )

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_client(self, org, scope_nodes):
        from apps.core.models import ScopeNode
        from apps.sites.models import Client

        company_node = scope_nodes['company']
        client_scope, scope_created = ScopeNode.objects.get_or_create(
            org=org,
            parent=company_node,
            code='demo-client',
            defaults={
                'name': 'Demo Client Pvt Ltd',
                'node_type': 'client',
                'depth': company_node.depth + 1,
                'path': f'{company_node.path}/demo-client',
                'is_active': True,
            },
        )
        self.stdout.write(
            f'  [ScopeNode] {client_scope.path} (client) — {"CREATED" if scope_created else "EXISTS"}'
        )

        client, created = Client.objects.get_or_create(
            org=org,
            code='demo-client',
            defaults={
                'name': 'Demo Client Pvt Ltd',
                'contact_name': 'Demo Contact',
                'industry': 'Manufacturing',
                'scope_node': client_scope,
                'is_active': True,
            },
        )
        if not created and client.scope_node_id != client_scope.id:
            client.scope_node = client_scope
            client.save(update_fields=['scope_node', 'updated_at'])
            self.stdout.write('  [Client] Demo Client Pvt Ltd scope_node — UPDATED')
        self.stdout.write(f'  [Client] {client.name} — {"CREATED" if created else "EXISTS"}')
        return client

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_site_profile(self, org, client, scope_nodes, location_areas):
        from apps.core.models import ScopeNode
        from apps.sites.models import SiteProfile

        parent_scope = client.scope_node or scope_nodes['company']
        site_scope, scope_created = ScopeNode.objects.get_or_create(
            org=org,
            parent=parent_scope,
            code='demo-mumbai-site',
            defaults={
                'name': 'Demo Site Mumbai',
                'node_type': 'site',
                'depth': parent_scope.depth + 1,
                'path': f'{parent_scope.path}/demo-mumbai-site',
                'is_active': True,
            },
        )
        self.stdout.write(
            f'  [ScopeNode] {site_scope.path} (site) — {"CREATED" if scope_created else "EXISTS"}'
        )

        mumbai_loc = location_areas.get('mumbai')
        site, created = SiteProfile.objects.get_or_create(
            org=org,
            code='demo-mumbai',
            defaults={
                'client': client,
                'scope_node': site_scope,
                'location_area': mumbai_loc,
                'name': 'Demo Site Mumbai',
                'city': 'Mumbai',
                'state': 'Maharashtra',
                'contact_person': 'Site Contact',
                'is_active': True,
            },
        )
        changed_fields = []
        if site.scope_node_id != site_scope.id:
            site.scope_node = site_scope
            changed_fields.append('scope_node')
        if site.client_id != client.id:
            site.client = client
            changed_fields.append('client')
        if mumbai_loc and site.location_area_id != mumbai_loc.id:
            site.location_area = mumbai_loc
            changed_fields.append('location_area')
        if changed_fields:
            changed_fields.append('updated_at')
            site.save(update_fields=changed_fields)
            self.stdout.write(f'  [SiteProfile] {site.name} client/scope — UPDATED')
        self.stdout.write(f'  [SiteProfile] {site.name} — {"CREATED" if created else "EXISTS"}')
        return site

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_site_role_requirements(self, site):
        from apps.jobs.models import JobRole
        from apps.wages.models import WageCategory
        from apps.sites.models import SiteRoleRequirement

        effective_from = datetime.date(2025, 1, 1)

        requirements = [
            {
                'job_role_code': 'housekeeping',
                'approved_headcount': 20,
                'billing_type': 'billable',
                'billing_rate': 18000,
                'wage_min': 10000,
                'wage_max': 13000,
                'shift_hours': 8.0,
                'wage_category_code': 'unskilled',
            },
            {
                'job_role_code': 'security_guard',
                'approved_headcount': 10,
                'billing_type': 'billable',
                'billing_rate': 20000,
                'wage_min': 12000,
                'wage_max': 15000,
                'shift_hours': 8.0,
                'wage_category_code': 'unskilled',
            },
            {
                'job_role_code': 'electrician',
                'approved_headcount': 4,
                'billing_type': 'billable',
                'billing_rate': 28000,
                'wage_min': 18000,
                'wage_max': 22000,
                'shift_hours': 8.0,
                'wage_category_code': 'skilled',
            },
        ]

        for req in requirements:
            try:
                job_role = JobRole.objects.get(
                    org=site.org, code=req['job_role_code']
                )
                wage_cat = WageCategory.objects.get(code=req['wage_category_code'])
            except (JobRole.DoesNotExist, WageCategory.DoesNotExist):
                self.stdout.write(
                    self.style.WARNING(
                        f'  [SiteRoleRequirement] Skipped {req["job_role_code"]} — role/category not found'
                    )
                )
                continue

            obj, created = SiteRoleRequirement.objects.get_or_create(
                site=site,
                job_role=job_role,
                effective_from=effective_from,
                defaults={
                    'approved_headcount': req['approved_headcount'],
                    'billing_type': req['billing_type'],
                    'billing_rate': req['billing_rate'],
                    'wage_min': req['wage_min'],
                    'wage_max': req['wage_max'],
                    'shift_hours': req['shift_hours'],
                    'wage_category': wage_cat,
                    'is_active': True,
                },
            )
            self.stdout.write(
                f'  [SiteRoleRequirement] {job_role.name} × {obj.approved_headcount} '
                f'— {"CREATED" if created else "EXISTS"}'
            )

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_module_activations(self, scope_nodes):
        from apps.modules.models import ModuleActivation, MODULE_TYPE_CHOICES

        company_node = scope_nodes['company']
        modules = [m[0] for m in MODULE_TYPE_CHOICES]

        created_count = 0
        existed_count = 0

        for module in modules:
            obj, created = ModuleActivation.objects.get_or_create(
                module=module,
                scope_node=company_node,
                defaults={'is_active': True, 'override_parent': False},
            )
            if created:
                created_count += 1
            else:
                existed_count += 1

        self.stdout.write(
            f'  [ModuleActivation] Created: {created_count}, Existed: {existed_count} '
            f'(all modules @ company scope)'
        )

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_departments(self, org):
        from apps.core.models import Department

        dept_defs = [
            ('HR', 'hr', 'Human Resources — recruitment, onboarding, payroll'),
            ('Finance', 'finance', 'Finance and accounts'),
            ('Sales', 'sales', 'Sales and client acquisition'),
            ('Operations', 'operations', 'Operations and service delivery'),
            ('Admin', 'admin', 'Administration and compliance'),
            ('Support', 'support', 'IT and general support'),
        ]

        depts = {}
        for name, code, description in dept_defs:
            obj, created = Department.objects.get_or_create(
                org=org,
                code=code,
                client=None,
                site=None,
                is_active=True,
                defaults={'name': name, 'description': description},
            )
            self.stdout.write(f'  [Department] {name} — {"CREATED" if created else "EXISTS"}')
            depts[code] = obj
        return depts

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_budget_plans(self, org, client, depts):
        from apps.budgets.models import BudgetPlan
        import datetime

        finance_dept = depts.get('finance')

        plans = []

        if client:
            plans.append({
                'code': 'logicon-billable-manpower-fy26',
                'name': 'ABC Infra Mumbai Manpower Budget FY26',
                'budget_nature': 'billable',
                'budget_type': 'manpower',
                'client': client,
                'site': None,
                'department': None,
                'period_start': datetime.date(2025, 4, 1),
                'period_end': datetime.date(2026, 3, 31),
                'amount': '5000000.00',
                'currency': 'INR',
                'status': 'active',
            })
        else:
            self.stdout.write('  [BudgetPlan] Billable sample skipped — demo client not found.')

        if finance_dept:
            plans.append({
                'code': 'logicon-internal-hiring-fy26',
                'name': 'Logicon HR Hiring Budget FY26',
                'budget_nature': 'non_billable',
                'budget_type': 'hiring',
                'client': None,
                'site': None,
                'department': finance_dept,
                'period_start': datetime.date(2025, 4, 1),
                'period_end': datetime.date(2026, 3, 31),
                'amount': '1000000.00',
                'currency': 'INR',
                'status': 'active',
            })
        else:
            self.stdout.write('  [BudgetPlan] Non-billable sample skipped — finance dept not found.')

        for plan in plans:
            obj, created = BudgetPlan.objects.get_or_create(
                org=org,
                code=plan.pop('code'),
                defaults={**plan, 'notes': ''},
            )
            self.stdout.write(f'  [BudgetPlan] {obj.name} — {"CREATED" if created else "EXISTS"}')

    # ──────────────────────────────────────────────────────────────────────────
    def _seed_users(self, org, roles, scope_nodes):
        from apps.accounts.models import User
        from apps.access.models import UserRoleAssignment

        company_node = scope_nodes['company']
        password = 'Password@123'

        user_defs = [
            {
                'username': 'admin.logicon',
                'email': 'admin@logicon.local',
                'first_name': 'Admin',
                'last_name': 'Logicon',
                'user_type': 'internal',
                'role_code': 'admin',
            },
            {
                'username': 'hr.admin',
                'email': 'hradmin@logicon.local',
                'first_name': 'HR',
                'last_name': 'Admin',
                'user_type': 'internal',
                'role_code': 'hr_admin',
            },
            {
                'username': 'hr.executive',
                'email': 'hrexec@logicon.local',
                'first_name': 'HR',
                'last_name': 'Executive',
                'user_type': 'internal',
                'role_code': 'hr_executive',
            },
            {
                'username': 'finance.user',
                'email': 'finance@logicon.local',
                'first_name': 'Finance',
                'last_name': 'User',
                'user_type': 'internal',
                'role_code': 'finance',
            },
            {
                'username': 'sales.manager',
                'email': 'salesmanager@logicon.local',
                'first_name': 'Sales',
                'last_name': 'Manager',
                'user_type': 'internal',
                'role_code': 'sales_manager',
            },
        ]

        for defn in user_defs:
            role_code = defn.pop('role_code')
            role_obj = roles.get(role_code)
            user, created = User.objects.get_or_create(
                username=defn['username'],
                defaults={**defn, 'org': org, 'is_active': True},
            )
            if created:
                user.set_password(password)
                user.save(update_fields=['password'])
            if role_obj:
                UserRoleAssignment.objects.get_or_create(
                    user=user,
                    role=role_obj,
                    scope_node=company_node,
                )
            self.stdout.write(
                f'  [User] {user.email} ({role_code}) — {"CREATED" if created else "EXISTS"}'
            )







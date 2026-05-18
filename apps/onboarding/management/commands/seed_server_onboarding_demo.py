"""
Seed server demo data for client onboarding workflow testing.

Idempotent by business codes. Safe to run repeatedly on local or server.
Creates foundation users/roles/workflow config and five draft onboarding
requests with proposed sites, departments, role requirements, budgets, users.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Seed Logicon server demo data: workflow config + 5 onboarding drafts.'

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
            self.job_roles = self._seed_job_roles()
            self.wage_categories = self._seed_wage_categories()
            self.location_areas = self._seed_location_areas()
            self._seed_onboarding_workflow()
            requests = self._seed_onboarding_requests()

        self.stdout.write(self.style.SUCCESS('Seed complete.'))
        self.stdout.write('Internal demo users:')
        for key in ['admin', 'sales', 'ops', 'hr', 'finance']:
            user = self.users[key]
            self.stdout.write(f'  {key}: {user.email}')
        self.stdout.write('Draft onboarding requests:')
        for req in requests:
            self.stdout.write(f'  #{req.pk}: {req.proposed_client_name} ({req.proposed_client_code})')

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
                defaults={'name': name, 'description': f'{name} department', 'is_active': True},
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
            ('sales_executive', 'Sales Executive', ''),
            ('sales_manager', 'Sales Manager', ''),
            ('operations_executive', 'Operations Executive', ''),
            ('operations_manager', 'Operations Manager', ''),
            ('hr_executive', 'HR Executive', ''),
            ('hr_manager', 'HR Manager', ''),
            ('finance_executive', 'Finance Executive', ''),
            ('finance_manager', 'Finance Manager', ''),
            ('client_admin', 'Client Manager', 'client'),
            ('client_site_user', 'Site Manager', 'site'),
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
            'sales': {
                'username': 'rohan.sales',
                'email': 'rohan.sales@logicon.in',
                'first_name': 'Rohan',
                'last_name': 'Sales',
                'department': self.departments['sales'],
                'role': 'sales_executive',
            },
            'ops': {
                'username': 'suresh.ops',
                'email': 'suresh.ops@logicon.in',
                'first_name': 'Suresh',
                'last_name': 'Operations',
                'department': self.departments['operations'],
                'role': 'operations_manager',
            },
            'hr': {
                'username': 'jyoti.hr',
                'email': 'jyoti.hr@logicon.in',
                'first_name': 'Jyoti',
                'last_name': 'HR',
                'department': self.departments['hr'],
                'role': 'hr_manager',
            },
            'finance': {
                'username': 'bhakti.finance',
                'email': 'bhakti.finance@logicon.in',
                'first_name': 'Bhakti',
                'last_name': 'Finance',
                'department': self.departments['finance'],
                'role': 'finance_manager',
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
                    'is_superuser': data.get('is_superuser', False),
                    'is_staff': data.get('is_staff', False),
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

    def _seed_job_roles(self):
        from apps.jobs.models import JobRole

        defs = [
            ('housekeeping-associate', 'Housekeeping Associate', 'unskilled'),
            ('security-guard', 'Security Guard', 'skilled'),
            ('electrician', 'Electrician', 'skilled'),
            ('site-supervisor', 'Site Supervisor', 'supervisor'),
        ]
        rows = {}
        for code, name, skill in defs:
            row, _ = JobRole.objects.update_or_create(
                org=self.org,
                code=code,
                defaults={'name': name, 'skill_category': skill, 'is_active': True},
            )
            rows[code] = row
        return rows

    def _seed_wage_categories(self):
        from apps.wages.models import WageCategory

        defs = [
            ('unskilled', 'Unskilled'),
            ('skilled', 'Skilled'),
            ('supervisor', 'Supervisor'),
        ]
        rows = {}
        for code, name in defs:
            row, _ = WageCategory.objects.update_or_create(
                code=code,
                defaults={'name': name},
            )
            rows[code] = row
        return rows

    def _seed_location_areas(self):
        from apps.wages.models import LocationArea

        state, _ = LocationArea.objects.update_or_create(
            parent=None,
            code='maharashtra',
            defaults={
                'name': 'Maharashtra',
                'area_type': 'state',
                'state_name': 'Maharashtra',
                'is_active': True,
            },
        )
        west, _ = LocationArea.objects.update_or_create(
            parent=state,
            code='west-region',
            defaults={
                'name': 'West Region',
                'area_type': 'region',
                'state_name': 'Maharashtra',
                'is_active': True,
            },
        )
        rows = {'maharashtra': state, 'west': west}
        for code, name in [('mumbai', 'Mumbai'), ('pune', 'Pune'), ('nashik', 'Nashik')]:
            row, _ = LocationArea.objects.update_or_create(
                parent=west,
                code=code,
                defaults={
                    'name': name,
                    'area_type': 'city',
                    'state_name': 'Maharashtra',
                    'is_active': True,
                },
            )
            rows[code] = row
        return rows

    def _seed_onboarding_workflow(self):
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
            code='client_onboarding_standard_v1',
            defaults={
                'name': 'Client Onboarding Standard Approval',
                'trigger_type': 'client_onboarding',
                'version': 1,
                'description': 'Sales, Operations, HR, Finance, Admin approval.',
                'is_active': True,
            },
        )
        steps = [
            (1, 'sales_submit_review', 'Sales Review', '', 'sales_submit_review'),
            (2, 'operations_review', 'Operations Review', 'sales_submit_review', 'sales_submit_review'),
            (3, 'hr_review', 'HR Review', 'operations_review', 'sales_submit_review'),
            (4, 'finance_review', 'Finance Review', 'hr_review', 'sales_submit_review'),
            (5, 'final_admin_review', 'Final Admin Review', 'finance_review', 'sales_submit_review'),
        ]
        for order, code, name, reject_target, changes_target in steps:
            WorkflowStepTemplate.objects.update_or_create(
                template=template,
                code=code,
                defaults={
                    'order': order,
                    'name': name,
                    'assignment_mode': 'named_user',
                    'actor_type': 'internal',
                    'on_approve_next': 'END' if code == 'final_admin_review' else '',
                    'on_reject_target': reject_target,
                    'on_request_changes_target': changes_target,
                    'requires_comment_on_reject': True,
                    'requires_comment_on_request_changes': True,
                },
            )

        WorkflowTemplateMapping.objects.update_or_create(
            org=self.org,
            trigger_type='client_onboarding',
            client=None,
            site=None,
            defaults={'template': template, 'is_active': True},
        )
        ApprovalRoute.objects.filter(
            org=self.org,
            trigger_type='client_onboarding',
            client__isnull=True,
            site__isnull=True,
            is_default=True,
        ).exclude(code='client_onboarding_standard_route').update(is_default=False)
        route, _ = ApprovalRoute.objects.update_or_create(
            org=self.org,
            code='client_onboarding_standard_route',
            defaults={
                'name': 'Standard Client Onboarding Route',
                'trigger_type': 'client_onboarding',
                'template': template,
                'client': None,
                'site': None,
                'description': 'Default onboarding approval route.',
                'is_default': True,
                'is_active': True,
            },
        )
        assignments = {
            'sales_submit_review': ('sales', 'sales'),
            'operations_review': ('ops', 'operations'),
            'hr_review': ('hr', 'hr'),
            'finance_review': ('finance', 'finance'),
            'final_admin_review': ('admin', None),
        }
        for step_code, (user_key, dept_key) in assignments.items():
            dept = self.departments.get(dept_key) if dept_key else None
            StepAssignmentConfig.objects.update_or_create(
                org=self.org,
                trigger_type='client_onboarding',
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

    def _seed_onboarding_requests(self):
        from apps.onboarding.models import (
            ClientOnboardingProposedBudget,
            ClientOnboardingProposedDepartment,
            ClientOnboardingProposedSite,
            ClientOnboardingProposedSiteRoleRequirement,
            ClientOnboardingProposedUser,
            ClientOnboardingRequest,
        )

        clients = [
            ('VibeCopilot Pvt Ltd', 'VIBE-COPILOT', 'Riya Shah', 'Karan Malhotra'),
            ('Nova Facilities Pvt Ltd', 'NOVA-FAC', 'Meera Iyer', 'Arjun Mehta'),
            ('Apex Infra Services Pvt Ltd', 'APEX-INFRA', 'Nisha Rao', 'Dev Patel'),
            ('Orion Retail Parks Pvt Ltd', 'ORION-RP', 'Ananya Sen', 'Rahul Nair'),
            ('Zenith Manufacturing Pvt Ltd', 'ZENITH-MFG', 'Priya Kulkarni', 'Sameer Joshi'),
            ('Meridian Corporate Services Pvt Ltd', 'MERIDIAN-CS', 'Lavanya Menon', 'Aditya Rao'),
            ('BluePeak Logistics Pvt Ltd', 'BLUEPEAK-LOG', 'Sneha Kapoor', 'Vikram Bhat'),
            ('Silverline Hospitals Pvt Ltd', 'SILVERLINE-HSP', 'Pooja Deshmukh', 'Nikhil Jain'),
            ('UrbanGrid Malls Pvt Ltd', 'URBANGRID-MALLS', 'Ishita Banerjee', 'Rohit Verma'),
            ('Greenfield Industrial Parks Pvt Ltd', 'GREENFIELD-IP', 'Neha Chatterjee', 'Manish Pillai'),
            ('PrimeNest Residences Pvt Ltd', 'PRIMENEST-RES', 'Aarohi Sinha', 'Kabir Anand'),
            ('MetroAxis Business Parks Pvt Ltd', 'METROAXIS-BP', 'Tanvi Mehra', 'Siddharth Khanna'),
            ('NorthStar Warehousing Pvt Ltd', 'NORTHSTAR-WH', 'Maya Krishnan', 'Harsh Vora'),
            ('ClearPath Healthcare Pvt Ltd', 'CLEARPATH-HC', 'Radhika Bose', 'Amit Tandon'),
            ('SummitEdge Technologies Pvt Ltd', 'SUMMITEDGE-TECH', 'Anika Nair', 'Varun Sethi'),
            ('LotusGate Hospitality Pvt Ltd', 'LOTUSGATE-HSP', 'Kavya Reddy', 'Pranav Shetty'),
            ('HarborView Retail Pvt Ltd', 'HARBORVIEW-RTL', 'Sonal Gupta', 'Akash Malhotra'),
            ('TerraNova Estates Pvt Ltd', 'TERRANOVA-EST', 'Mitali Joshi', 'Raghav Bansal'),
            ('QuantumWorks Manufacturing Pvt Ltd', 'QUANTUMWORKS-MFG', 'Shreya Iyer', 'Naveen Rao'),
            ('Skyline Civic Services Pvt Ltd', 'SKYLINE-CS', 'Dia Fernandes', 'Omkar Kulkarni'),
        ]
        seeded = []
        for index, (name, code, primary_user, ops_user) in enumerate(clients, start=1):
            req, _ = ClientOnboardingRequest.objects.update_or_create(
                org=self.org,
                proposed_client_code=code,
                defaults={
                    'client': None,
                    'requested_by': self.users['sales'],
                    'status': 'draft',
                    'onboarding_type': 'new_client',
                    'expected_site_count': 3,
                    'proposed_client_name': name,
                    'proposed_contact_name': primary_user,
                    'proposed_contact_email': f'{code.lower()}@example.com',
                    'proposed_contact_phone': f'98765{index}0001',
                    'proposed_industry': 'Facility management services',
                    'proposed_billing_address': f'{name}, Mumbai, Maharashtra',
                    'proposed_gst_number': f'27{code.replace("-", "")[:10]}1Z{index}',
                    'summary': f'Demo onboarding for {name} with 3 sites, departments, SRRs, budgets, and users.',
                    'operations_notes': 'Seeded for server workflow testing.',
                },
            )
            self._seed_request_children(req, code, primary_user, ops_user, index)
            seeded.append(req)
        return seeded

    def _seed_request_children(self, req, code, primary_user, ops_user, index):
        from apps.onboarding.models import (
            ClientOnboardingProposedBudget,
            ClientOnboardingProposedDepartment,
            ClientOnboardingProposedSite,
            ClientOnboardingProposedSiteRoleRequirement,
            ClientOnboardingProposedUser,
        )

        site_defs = [
            ('MUM', 'Mumbai Office', 'Mumbai', '400051', 'mumbai'),
            ('PUN', 'Pune Hub', 'Pune', '411045', 'pune'),
            ('NAS', 'Nashik Center', 'Nashik', '422001', 'nashik'),
        ]
        sites = {}
        for short, label, city, pin, area_key in site_defs:
            site_code = f'{code}-{short}-01'
            site, _ = ClientOnboardingProposedSite.objects.update_or_create(
                request=req,
                code=site_code,
                defaults={
                    'name': f'{req.proposed_client_name.split(" Pvt")[0]} {label}',
                    'address': f'{label}, {city}, Maharashtra',
                    'city': city,
                    'state': 'Maharashtra',
                    'pincode': pin,
                    'contact_person': ops_user,
                    'contact_phone': f'98765{index}0002',
                    'contact_email': f'{code.lower()}-{short.lower()}@example.com',
                    'location_area': self.location_areas[area_key],
                    'is_active': True,
                },
            )
            sites[short] = site

        client_dept, _ = ClientOnboardingProposedDepartment.objects.update_or_create(
            request=req,
            code=f'{code}-OPS',
            proposed_site=None,
            defaults={
                'scope_level': 'client',
                'name': 'Client Operations',
                'description': 'Client-level MRF coordination team.',
                'is_active': True,
            },
        )
        dept_defs = [
            ('MUM-HK', 'Mumbai Housekeeping', sites['MUM']),
            ('PUN-SEC', 'Pune Security', sites['PUN']),
            ('NAS-FS', 'Nashik Facility Support', sites['NAS']),
        ]
        depts = {'OPS': client_dept}
        for suffix, name, site in dept_defs:
            dept, _ = ClientOnboardingProposedDepartment.objects.update_or_create(
                request=req,
                code=f'{code}-{suffix}',
                proposed_site=site,
                defaults={
                    'scope_level': 'site',
                    'name': name,
                    'description': f'{name} manpower department.',
                    'is_active': True,
                },
            )
            depts[suffix] = dept

        srr_defs = [
            (sites['MUM'], depts['MUM-HK'], 'housekeeping-associate', 24, 'unskilled', '18500.00', '21000.00', '26500.00'),
            (sites['MUM'], depts['MUM-HK'], 'site-supervisor', 2, 'supervisor', '33000.00', '39000.00', '54000.00'),
            (sites['PUN'], depts['PUN-SEC'], 'security-guard', 16, 'skilled', '22000.00', '26500.00', '35000.00'),
            (sites['NAS'], depts['NAS-FS'], 'electrician', 5, 'skilled', '26000.00', '32500.00', '46000.00'),
        ]
        for site, dept, role_code, headcount, wage_code, wage_min, wage_max, billing in srr_defs:
            ClientOnboardingProposedSiteRoleRequirement.objects.update_or_create(
                request=req,
                proposed_site=site,
                proposed_department=dept,
                job_role=self.job_roles[role_code],
                defaults={
                    'approved_headcount': headcount,
                    'wage_category': self.wage_categories[wage_code],
                    'wage_min': Decimal(wage_min),
                    'wage_max': Decimal(wage_max),
                    'billing_rate': Decimal(billing),
                    'shift_hours': Decimal('8.0'),
                    'billing_type': 'billable',
                    'effective_from': '2026-06-01',
                    'effective_to': '2027-05-31',
                    'is_active': True,
                },
            )

        budget_defs = [
            (f'{code}-TOTAL', f'{req.proposed_client_name.split(" Pvt")[0]} Client Total Budget', 'client', None, None, '4600000.00', 'onboarding'),
            (f'{code}-MUM-HK-BUD', f'{req.proposed_client_name.split(" Pvt")[0]} Mumbai Housekeeping Budget', 'department', sites['MUM'], depts['MUM-HK'], '1450000.00', 'manpower'),
            (f'{code}-PUN-SEC-BUD', f'{req.proposed_client_name.split(" Pvt")[0]} Pune Security Budget', 'department', sites['PUN'], depts['PUN-SEC'], '1050000.00', 'manpower'),
            (f'{code}-NAS-FS-BUD', f'{req.proposed_client_name.split(" Pvt")[0]} Nashik Facility Support Budget', 'department', sites['NAS'], depts['NAS-FS'], '800000.00', 'manpower'),
        ]
        for budget_code, name, scope, site, dept, amount, budget_type in budget_defs:
            ClientOnboardingProposedBudget.objects.update_or_create(
                request=req,
                code=budget_code,
                defaults={
                    'name': name,
                    'budget_nature': 'billable',
                    'budget_type': budget_type,
                    'scope_level': scope,
                    'proposed_site': site,
                    'proposed_department': dept,
                    'amount': Decimal(amount),
                    'currency': 'INR',
                    'period_start': '2026-06-01',
                    'period_end': '2027-05-31',
                    'notes': 'Seeded demo budget.',
                    'is_active': True,
                },
            )

        desired_users = [
            (ops_user, 'ops', False),
            (primary_user, 'primary', True),
        ]
        desired_emails = [f'{email_prefix}.{code.lower()}@example.com' for _, email_prefix, _ in desired_users]
        req.proposed_users.exclude(email__in=desired_emails).update(
            is_active=False,
            is_primary_contact=False,
        )
        req.proposed_users.update(is_primary_contact=False)

        for full_name, email_prefix, primary in desired_users:
            ClientOnboardingProposedUser.objects.update_or_create(
                request=req,
                email=f'{email_prefix}.{code.lower()}@example.com',
                defaults={
                    'full_name': full_name,
                    'phone': f'98765{index}000{1 if primary else 2}',
                    'user_type': 'client',
                    'access_role': self.roles['client_admin'],
                    'scope_level': 'client',
                    'proposed_site': None,
                    'is_primary_contact': primary,
                    'send_invite_on_finalization': True,
                    'is_active': True,
                },
            )

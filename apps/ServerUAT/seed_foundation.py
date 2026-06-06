"""
ServerUAT foundation seed.

This command intentionally seeds only the foundation needed to verify server
login, scope, roles, permissions, and internal users. It does not seed demo
clients, sites, budgets, wages, MRFs, hiring, or deployment data.
"""

from django.core.management.base import BaseCommand


PASSWORD = 'Password@123'

SCOPE_DEPARTMENTS = [
    ('sales', 'Sales'),
    ('operations', 'Operations'),
    ('finance', 'Finance'),
    ('human-resources', 'Human Resources'),
]

DEPARTMENTS = [
    ('sales', 'Sales', 'Sales and client acquisition'),
    ('operations', 'Operations', 'Operations and service delivery'),
    ('finance', 'Finance', 'Finance and accounts'),
    ('human-resources', 'Human Resources', 'Recruitment, onboarding, and HR operations'),
]

ACCESS_ROLES = [
    ('admin', 'Admin', ''),
    ('sales_manager', 'Sales Manager', 'department'),
    ('operations_manager', 'Operations Manager', 'department'),
    ('finance_manager', 'Finance Manager', 'department'),
    ('hr_admin', 'HR Admin', 'department'),
]

USERS = [
    {
        'username': 'admin.logicon',
        'email': 'admin@logicon.local',
        'first_name': 'Admin',
        'last_name': 'Logicon',
        'role_code': 'admin',
        'scope_key': 'company',
    },
    {
        'username': 'sales.manager',
        'email': 'sales.manager@logicon.local',
        'first_name': 'Sales',
        'last_name': 'Manager',
        'role_code': 'sales_manager',
        'scope_key': 'dept_sales',
    },
    {
        'username': 'sales.user',
        'email': 'sales.user@logicon.local',
        'first_name': 'Sales',
        'last_name': 'User',
        'role_code': 'sales_manager',
        'scope_key': 'dept_sales',
    },
    {
        'username': 'ops.manager',
        'email': 'ops.manager@logicon.local',
        'first_name': 'Operations',
        'last_name': 'Manager',
        'role_code': 'operations_manager',
        'scope_key': 'dept_operations',
    },
    {
        'username': 'ops.user',
        'email': 'ops.user@logicon.local',
        'first_name': 'Operations',
        'last_name': 'User',
        'role_code': 'operations_manager',
        'scope_key': 'dept_operations',
    },
    {
        'username': 'finance.manager',
        'email': 'finance.manager@logicon.local',
        'first_name': 'Finance',
        'last_name': 'Manager',
        'role_code': 'finance_manager',
        'scope_key': 'dept_finance',
    },
    {
        'username': 'finance.user',
        'email': 'finance.user@logicon.local',
        'first_name': 'Finance',
        'last_name': 'User',
        'role_code': 'finance_manager',
        'scope_key': 'dept_finance',
    },
    {
        'username': 'hr.admin',
        'email': 'hr.admin@logicon.local',
        'first_name': 'HR',
        'last_name': 'Admin',
        'role_code': 'hr_admin',
        'scope_key': 'dept_human-resources',
    },
    {
        'username': 'hr.user',
        'email': 'hr.user@logicon.local',
        'first_name': 'HR',
        'last_name': 'User',
        'role_code': 'hr_admin',
        'scope_key': 'dept_human-resources',
    },
]


class Command(BaseCommand):
    help = 'Seed ServerUAT foundation data only: org, scopes, departments, roles, permissions, users.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Foundation Seed ===\n'))

        org = self._seed_organization()
        scope_nodes = self._seed_scope_nodes(org)
        self._seed_departments(org)
        roles = self._seed_access_roles(org)
        permissions = self._seed_permissions()
        self._seed_role_permissions(roles, permissions)
        self._seed_users(org, roles, scope_nodes)

        self.stdout.write(self.style.SUCCESS('\n[OK] ServerUAT foundation seed complete.\n'))

    def _seed_organization(self):
        from apps.core.models import Organization

        org, created = Organization.objects.get_or_create(
            code='logicon',
            defaults={'name': 'Logicon Facility Management', 'is_active': True},
        )
        changed_fields = []
        if org.name != 'Logicon Facility Management':
            org.name = 'Logicon Facility Management'
            changed_fields.append('name')
        if not org.is_active:
            org.is_active = True
            changed_fields.append('is_active')
        if changed_fields:
            org.save(update_fields=changed_fields)

        self.stdout.write(
            f'  [Organization] {org.code} / {org.name} - {"CREATED" if created else "EXISTS"}'
        )
        return org

    def _seed_scope_nodes(self, org):
        from apps.core.models import ScopeNode

        nodes = {}

        company, created = ScopeNode.objects.get_or_create(
            org=org,
            parent=None,
            code='logicon',
            defaults={
                'name': 'Logicon',
                'node_type': 'company',
                'depth': 0,
                'path': 'logicon',
                'is_active': True,
            },
        )
        self._sync_scope_node(company, name='Logicon', node_type='company', depth=0, path='logicon')
        nodes['company'] = company
        self.stdout.write(
            f'  [ScopeNode] logicon (company) - {"CREATED" if created else "EXISTS"}'
        )

        for code, name in SCOPE_DEPARTMENTS:
            node, created = ScopeNode.objects.get_or_create(
                org=org,
                parent=company,
                code=code,
                defaults={
                    'name': name,
                    'node_type': 'department',
                    'depth': 1,
                    'path': f'logicon/{code}',
                    'is_active': True,
                },
            )
            self._sync_scope_node(
                node,
                name=name,
                node_type='department',
                depth=1,
                path=f'logicon/{code}',
            )
            nodes[f'dept_{code}'] = node
            self.stdout.write(
                f'  [ScopeNode] logicon/{code} (department) - {"CREATED" if created else "EXISTS"}'
            )

        return nodes

    def _sync_scope_node(self, node, *, name, node_type, depth, path):
        changed_fields = []
        expected = {
            'name': name,
            'node_type': node_type,
            'depth': depth,
            'path': path,
            'is_active': True,
        }
        for field, value in expected.items():
            if getattr(node, field) != value:
                setattr(node, field, value)
                changed_fields.append(field)
        if changed_fields:
            node.save(update_fields=changed_fields)

    def _seed_departments(self, org):
        from apps.core.models import Department

        departments = {}
        for code, name, description in DEPARTMENTS:
            dept, created = Department.objects.get_or_create(
                org=org,
                code=code,
                client=None,
                site=None,
                defaults={
                    'name': name,
                    'description': description,
                    'is_active': True,
                },
            )
            changed_fields = []
            for field, value in {
                'name': name,
                'description': description,
                'is_active': True,
            }.items():
                if getattr(dept, field) != value:
                    setattr(dept, field, value)
                    changed_fields.append(field)
            if changed_fields:
                dept.save(update_fields=changed_fields)
            departments[code] = dept
            self.stdout.write(
                f'  [Department] {code} / {name} - {"CREATED" if created else "EXISTS"}'
            )
        return departments

    def _seed_access_roles(self, org):
        from apps.access.models import AccessRole

        roles = {}
        for code, name, node_type_scope in ACCESS_ROLES:
            role, created = AccessRole.objects.get_or_create(
                org=org,
                code=code,
                defaults={
                    'name': name,
                    'node_type_scope': node_type_scope,
                    'is_active': True,
                },
            )
            changed_fields = []
            for field, value in {
                'name': name,
                'node_type_scope': node_type_scope,
                'is_active': True,
            }.items():
                if getattr(role, field) != value:
                    setattr(role, field, value)
                    changed_fields.append(field)
            if changed_fields:
                role.save(update_fields=changed_fields)
            roles[code] = role
            self.stdout.write(
                f'  [AccessRole] {code} / {name} - {"CREATED" if created else "EXISTS"}'
            )
        return roles

    def _seed_permissions(self):
        from apps.access.capabilities import ALL_CAPABILITIES
        from apps.access.models import Permission

        permissions = {}
        created_count = 0
        existed_count = 0

        for capability in ALL_CAPABILITIES:
            parts = capability.split('.')
            if len(parts) == 2:
                resource, action = parts
            else:
                resource = '_'.join(parts[:-1])
                action = parts[-1]

            permission, created = Permission.objects.get_or_create(
                code=capability,
                defaults={
                    'resource': resource,
                    'action': action,
                    'description': f'Can {action} {capability}',
                },
            )
            permissions[capability] = permission
            if created:
                created_count += 1
            else:
                existed_count += 1

        self.stdout.write(
            f'  [Permission] Created: {created_count}, Existed: {existed_count}'
        )
        return permissions

    def _seed_role_permissions(self, roles, permissions):
        from apps.access.capabilities import ROLE_CAPABILITIES
        from apps.access.models import AccessRolePermission

        created_count = 0
        existed_count = 0
        skipped_count = 0

        for role_code, role in roles.items():
            capabilities = ROLE_CAPABILITIES.get(role_code, [])
            if not capabilities:
                self.stderr.write(f'  [AccessRolePermission] WARNING: no capabilities for role {role_code}')
            for capability in capabilities:
                permission = permissions.get(capability)
                if permission is None:
                    skipped_count += 1
                    continue
                _, created = AccessRolePermission.objects.get_or_create(
                    role=role,
                    permission=permission,
                )
                if created:
                    created_count += 1
                else:
                    existed_count += 1

        message = f'  [AccessRolePermission] Created: {created_count}, Existed: {existed_count}'
        if skipped_count:
            message += f', Skipped: {skipped_count}'
        self.stdout.write(message)

    def _seed_users(self, org, roles, scope_nodes):
        from apps.access.models import UserRoleAssignment
        from apps.accounts.models import User

        for definition in USERS:
            role = roles.get(definition['role_code'])
            scope_node = scope_nodes.get(definition['scope_key'])
            if role is None:
                self.stderr.write(
                    f'  [User] SKIPPED {definition["username"]}: missing role {definition["role_code"]}'
                )
                continue
            if scope_node is None:
                self.stderr.write(
                    f'  [User] SKIPPED {definition["username"]}: missing scope {definition["scope_key"]}'
                )
                continue

            user_defaults = {
                'email': definition['email'],
                'first_name': definition['first_name'],
                'last_name': definition['last_name'],
                'user_type': 'internal',
                'org': org,
                'is_active': True,
            }
            user, created = User.objects.get_or_create(
                username=definition['username'],
                defaults=user_defaults,
            )
            changed_fields = []
            for field, value in user_defaults.items():
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changed_fields.append(field)
            if created:
                user.set_password(PASSWORD)
                user.save()
            elif changed_fields:
                user.save(update_fields=changed_fields)

            UserRoleAssignment.objects.get_or_create(
                user=user,
                role=role,
                scope_node=scope_node,
            )
            self.stdout.write(
                f'  [User] {user.username} / {user.email} ({definition["role_code"]} @ {scope_node.path}) - '
                f'{"CREATED" if created else "EXISTS"}'
            )

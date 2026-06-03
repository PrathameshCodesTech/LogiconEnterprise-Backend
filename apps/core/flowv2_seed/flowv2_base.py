"""Base organization, access, users, and master data for Logicon demo."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.core.flowv2_seed.flowv2_constants import (
    LOGICON_DEMO_DEPARTMENTS,
    LOGICON_DEMO_EXISTING_CLIENT_CODE,
    LOGICON_DEMO_JOB_ROLES,
    LOGICON_DEMO_ORG_CODE,
    LOGICON_DEMO_ORG_NAME,
    LOGICON_DEMO_PASSWORD,
    LOGICON_DEMO_RESETTABLE_ORG_CODES,
    LOGICON_DEMO_USER_DEFS,
)


def _write(writer, message):
    if writer:
        writer(message)


def seed_logicon_demo_base(*, org_code=LOGICON_DEMO_ORG_CODE, writer=None):
    """Seed the shared Logicon org, access rows, users, and master data."""
    org = _seed_org(org_code, writer)
    scope_root = _seed_scope_root(org, writer)
    departments = _seed_departments(org, writer)
    roles = _seed_access_roles(org, writer)
    permissions = _seed_permissions(writer)
    _seed_role_permissions(roles, permissions, writer)
    users = _seed_users(org, departments, roles, scope_root, writer)
    job_roles = _seed_job_roles(org, writer)
    wage_categories = _seed_wage_categories(writer)
    _seed_survey_role_mappings(org, job_roles, wage_categories, writer)
    location = _seed_location_area(writer)
    _seed_minimum_wages(org, location, job_roles, wage_categories, writer)
    existing_client = _seed_existing_client(org, scope_root, users['sales'], writer)
    return {
        'org': org,
        'scope_root': scope_root,
        'departments': departments,
        'roles': roles,
        'permissions': permissions,
        'users': users,
        'job_roles': job_roles,
        'wage_categories': wage_categories,
        'location': location,
        'existing_client': existing_client,
    }


def _seed_org(org_code, writer):
    from apps.core.models import Organization

    org, created = Organization.objects.get_or_create(
        code=org_code,
        defaults={'name': LOGICON_DEMO_ORG_NAME, 'is_active': True},
    )
    changed = []
    if org.name != LOGICON_DEMO_ORG_NAME:
        org.name = LOGICON_DEMO_ORG_NAME
        changed.append('name')
    if not org.is_active:
        org.is_active = True
        changed.append('is_active')
    if changed:
        org.save(update_fields=changed + ['updated_at'])
    _write(writer, f'[LogiconSeed] Organization {org.code}: {"created" if created else "exists"}')
    return org


def _seed_scope_root(org, writer):
    from apps.core.models import ScopeNode

    root, created = ScopeNode.objects.get_or_create(
        org=org,
        parent=None,
        code=org.code,
        defaults={
            'name': org.name,
            'node_type': 'company',
            'path': org.code,
            'depth': 0,
            'is_active': True,
        },
    )
    _write(writer, f'[LogiconSeed] Scope root {root.path}: {"created" if created else "exists"}')
    return root


def _seed_departments(org, writer):
    from apps.core.models import Department

    result = {}
    for code, name in LOGICON_DEMO_DEPARTMENTS:
        dept, created = Department.objects.get_or_create(
            org=org,
            client=None,
            site=None,
            code=code,
            defaults={'name': name, 'is_active': True},
        )
        if dept.name != name or not dept.is_active:
            dept.name = name
            dept.is_active = True
            dept.save(update_fields=['name', 'is_active', 'updated_at'])
        result[code] = dept
        _write(writer, f'[LogiconSeed] Department {code}: {"created" if created else "exists"}')
    return result


def _seed_access_roles(org, writer):
    from apps.access.capabilities import ROLE_CAPABILITIES
    from apps.access.models import AccessRole

    result = {}
    for code in sorted(ROLE_CAPABILITIES):
        name = code.replace('_', ' ').title()
        role, created = AccessRole.objects.get_or_create(
            org=org,
            code=code,
            defaults={'name': name, 'is_active': True},
        )
        if not role.is_active:
            role.is_active = True
            role.save(update_fields=['is_active', 'updated_at'])
        result[code] = role
        _write(writer, f'[LogiconSeed] Access role {code}: {"created" if created else "exists"}')
    return result


def _seed_permissions(writer):
    from apps.access.capabilities import ALL_CAPABILITIES
    from apps.access.models import Permission

    result = {}
    created_count = 0
    for cap in ALL_CAPABILITIES:
        parts = cap.split('.')
        if len(parts) == 2:
            resource, action = parts
        else:
            resource = '_'.join(parts[:-1])
            action = parts[-1]
        perm, created = Permission.objects.get_or_create(
            code=cap,
            defaults={
                'resource': resource,
                'action': action,
                'description': f'Can {action} {cap}',
            },
        )
        result[cap] = perm
        created_count += int(created)
    _write(writer, f'[LogiconSeed] Permissions created={created_count} total={len(result)}')
    return result


def _seed_role_permissions(roles, permissions, writer):
    from apps.access.capabilities import ROLE_CAPABILITIES
    from apps.access.models import AccessRolePermission

    created_count = 0
    for role_code, caps in ROLE_CAPABILITIES.items():
        role = roles.get(role_code)
        if role is None:
            continue
        for cap in caps:
            perm = permissions.get(cap)
            if perm is None:
                continue
            _, created = AccessRolePermission.objects.get_or_create(role=role, permission=perm)
            created_count += int(created)
    _write(writer, f'[LogiconSeed] Role permissions created={created_count}')


def _seed_users(org, departments, roles, scope_root, writer):
    from apps.access.models import UserRoleAssignment, UserScopeAssignment

    User = get_user_model()
    result = {}
    for key, spec in LOGICON_DEMO_USER_DEFS.items():
        username = spec['username']
        email = spec['email']
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'first_name': spec['first_name'],
                'last_name': spec['last_name'],
                'org': org,
                'department': departments.get(spec['department_code']),
                'user_type': spec.get('user_type', 'internal'),
                'is_active': True,
                'is_staff': key == 'admin',
            },
        )
        changed = []
        field_values = {
            'email': email,
            'first_name': spec['first_name'],
            'last_name': spec['last_name'],
            'org': org,
            'department': departments.get(spec['department_code']),
            'user_type': spec.get('user_type', 'internal'),
            'is_active': True,
            'is_staff': key == 'admin',
        }
        for field, value in field_values.items():
            if hasattr(value, 'pk'):
                current_value = getattr(user, f'{field}_id', None)
                expected_value = value.pk
            else:
                current_value = getattr(user, field)
                expected_value = value
            if current_value != expected_value:
                setattr(user, field, value)
                changed.append(field)
        user.set_password(LOGICON_DEMO_PASSWORD)
        if changed:
            user.save()
        else:
            user.save()

        role = roles[spec['role_code']]
        UserRoleAssignment.objects.get_or_create(user=user, role=role, scope_node=scope_root)
        UserScopeAssignment.objects.get_or_create(
            user=user,
            scope_node=scope_root,
            assignment_type='primary',
        )
        result[key] = user
        _write(writer, f'[LogiconSeed] User {username}: {"created" if created else "exists"}')
    return result


def _seed_job_roles(org, writer):
    from apps.jobs.models import JobRole

    result = {}
    for code, name, skill_category, _wage_category, _monthly_wage in LOGICON_DEMO_JOB_ROLES:
        role, created = JobRole.objects.get_or_create(
            org=org,
            code=code,
            defaults={'name': name, 'skill_category': skill_category, 'is_active': True},
        )
        if role.name != name or role.skill_category != skill_category or not role.is_active:
            role.name = name
            role.skill_category = skill_category
            role.is_active = True
            role.save(update_fields=['name', 'skill_category', 'is_active', 'updated_at'])
        result[code] = role
        _write(writer, f'[LogiconSeed] Job role {code}: {"created" if created else "exists"}')
    return result


def _seed_survey_role_mappings(org, job_roles, wage_categories, writer):
    from apps.sales.models import SurveyRoleMapping

    SurveyRoleMapping.objects.filter(
        org=org,
        description_text__iexact='Technical(8 Hrs x 6 days)',
        is_active=True,
    ).update(is_active=False)

    specs = [
        ('Tech Supervisor', 'tech_supervisor', 'skilled', 'Technical', Decimal('8.0'), Decimal('26.0')),
        ('Electrician', 'electrician', 'skilled', 'Technical', Decimal('8.0'), Decimal('26.0')),
        ('Plumber', 'plumber', 'skilled', 'Technical', Decimal('8.0'), Decimal('26.0')),
        ('STP', 'stp_operator', 'skilled', 'Technical', Decimal('8.0'), Decimal('26.0')),
        ('HK Supervisor', 'hk_supervisor', 'skilled', 'Housekeeping', Decimal('8.0'), Decimal('26.0')),
        ('Janitor', 'janitor', 'unskilled', 'Housekeeping', Decimal('8.0'), Decimal('26.0')),
    ]
    created_count = 0
    for description, role_code, wage_code, service_category, shift_hours, working_days in specs:
        mapping, created = SurveyRoleMapping.objects.get_or_create(
            org=org,
            description_text=description,
            defaults={
                'job_role': job_roles[role_code],
                'wage_category': wage_categories[wage_code],
                'service_category': service_category,
                'shift_hours': shift_hours,
                'working_days': working_days,
                'is_active': True,
            },
        )
        changed = []
        expected = {
            'job_role': job_roles[role_code],
            'wage_category': wage_categories[wage_code],
            'service_category': service_category,
            'shift_hours': shift_hours,
            'working_days': working_days,
            'is_active': True,
        }
        for field, value in expected.items():
            current = getattr(mapping, f'{field}_id') if hasattr(value, 'pk') else getattr(mapping, field)
            expected_value = value.pk if hasattr(value, 'pk') else value
            if current != expected_value:
                setattr(mapping, field, value)
                changed.append(field)
        if changed:
            mapping.save(update_fields=changed + ['updated_at'])
        created_count += int(created)
    _write(writer, f'[LogiconSeed] Survey role mappings created={created_count}')


def _seed_wage_categories(writer):
    from apps.wages.models import WageCategory

    result = {}
    for code, name in [('skilled', 'Skilled'), ('unskilled', 'Unskilled')]:
        cat, created = WageCategory.objects.get_or_create(
            code=code,
            defaults={'name': name, 'description': 'Logicon demo wage category'},
        )
        result[code] = cat
        _write(writer, f'[LogiconSeed] Wage category {code}: {"created" if created else "exists"}')
    return result


def _seed_location_area(writer):
    from apps.wages.models import LocationArea

    state, _ = LocationArea.objects.get_or_create(
        parent=None,
        code='maharashtra',
        defaults={
            'name': 'Maharashtra',
            'area_type': 'state',
            'state_name': 'Maharashtra',
            'is_active': True,
        },
    )
    city, created = LocationArea.objects.get_or_create(
        parent=state,
        code='pune-metro',
        defaults={
            'name': 'Pune Metro',
            'area_type': 'city',
            'state_name': 'Maharashtra',
            'is_active': True,
        },
    )
    _write(writer, f'[LogiconSeed] Location pune-metro: {"created" if created else "exists"}')
    return city


def _seed_minimum_wages(org, location, job_roles, wage_categories, writer):
    from apps.wages.models import MinimumWageRate

    created_count = 0
    for code, _name, _skill, wage_category_code, monthly_wage in LOGICON_DEMO_JOB_ROLES:
        role = job_roles[code]
        wage_category = wage_categories[wage_category_code]
        daily_wage = (monthly_wage / Decimal('26')).quantize(Decimal('0.01'))
        _, created = MinimumWageRate.objects.get_or_create(
            org=org,
            location=location,
            wage_category=wage_category,
            role=role,
            effective_from=date(2026, 1, 1),
            defaults={
                'monthly_wage': monthly_wage,
                'daily_wage': daily_wage,
                'source_note': 'logicon_seed',
                'is_active': True,
            },
        )
        created_count += int(created)
    _write(writer, f'[LogiconSeed] Minimum wage rates created={created_count}')


def _seed_existing_client(org, scope_root, sales_user, writer):
    from apps.core.models import ScopeNode
    from apps.sites.models import Client

    client_scope, _ = ScopeNode.objects.get_or_create(
        org=org,
        parent=scope_root,
        code=LOGICON_DEMO_EXISTING_CLIENT_CODE,
        defaults={
            'name': 'Beta Industries Pvt Ltd',
            'node_type': 'client',
            'path': f'{scope_root.path}/{LOGICON_DEMO_EXISTING_CLIENT_CODE}',
            'depth': scope_root.depth + 1,
            'is_active': True,
        },
    )
    client, created = Client.objects.get_or_create(
        org=org,
        code=LOGICON_DEMO_EXISTING_CLIENT_CODE,
        defaults={
            'name': 'Beta Industries Pvt Ltd',
            'contact_name': 'Nikhil Patil',
            'contact_email': 'nikhil.patil@beta.example.com',
            'industry': 'Manufacturing',
            'scope_node': client_scope,
            'created_by': sales_user,
            'owner_sales_user': sales_user,
            'is_active': True,
        },
    )
    if client.scope_node_id != client_scope.pk:
        client.scope_node = client_scope
        client.save(update_fields=['scope_node', 'updated_at'])
    _write(writer, f'[LogiconSeed] Existing client {client.code}: {"created" if created else "exists"}')
    return client


def reset_logicon_demo_org(*, org_code=LOGICON_DEMO_ORG_CODE, writer=None):
    """Delete only disposable Logicon demo orgs and fixed users."""
    from apps.core.models import Organization

    if org_code not in LOGICON_DEMO_RESETTABLE_ORG_CODES:
        raise RuntimeError(
            f'Refusing to reset org={org_code}. Use a disposable org code such as '
            'logicon-sandbox if you need destructive reset behavior.'
        )

    User = get_user_model()
    usernames = [spec['username'] for spec in LOGICON_DEMO_USER_DEFS.values()]
    org = Organization.objects.filter(code=org_code).first()
    if org is None:
        deleted_users, _ = User.objects.filter(username__in=usernames).delete()
        _write(writer, f'[LogiconSeed] Reset: org {org_code} not found; deleted users={deleted_users}')
        return

    _delete_logicon_demo_workflow_rows(org)
    _delete_logicon_demo_mobilisation_rows(org)

    # ScopeNode.parent uses PROTECT, so delete leaves before parents.
    while org.scope_nodes.exists():
        leaves = org.scope_nodes.filter(children__isnull=True)
        if not leaves.exists():
            raise RuntimeError(f'Could not resolve scope-node delete order for org={org_code}.')
        leaves.delete()
    org.delete()
    deleted_users, _ = User.objects.filter(username__in=usernames).delete()
    _write(writer, f'[LogiconSeed] Reset complete for org={org_code}; deleted users={deleted_users}')


def _delete_logicon_demo_workflow_rows(org):
    from apps.workflow.models import (
        ApprovalRoute,
        StepAssignmentConfig,
        WorkflowInstance,
        WorkflowTemplate,
        WorkflowTemplateMapping,
    )

    WorkflowInstance.objects.filter(org=org).delete()
    ApprovalRoute.objects.filter(org=org).delete()
    WorkflowTemplateMapping.objects.filter(org=org).delete()
    StepAssignmentConfig.objects.filter(org=org).delete()
    WorkflowTemplate.objects.filter(org=org).delete()


def _delete_logicon_demo_mobilisation_rows(org):
    from apps.mobilisation.models import MobilisationSetupRequest

    MobilisationSetupRequest.objects.filter(org=org).delete()







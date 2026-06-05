from django.db import migrations


CLIENT_FACING_ROLE_CODES = (
    'client_admin',
    'client_site_user',
    'site_supervisor',
    'client_user',
)

CLIENT_DEPLOYMENT_READ_CODES = (
    'employee.read',
    'site_deployment.read',
    'deployment.read',
)


def add_client_deployment_read(apps, schema_editor):
    AccessRole = apps.get_model('access', 'AccessRole')
    AccessRolePermission = apps.get_model('access', 'AccessRolePermission')
    Permission = apps.get_model('access', 'Permission')

    permissions = {
        p.code: p
        for p in Permission.objects.filter(code__in=CLIENT_DEPLOYMENT_READ_CODES)
    }
    if not permissions:
        return

    rows = []
    for role in AccessRole.objects.filter(code__in=CLIENT_FACING_ROLE_CODES):
        for code in CLIENT_DEPLOYMENT_READ_CODES:
            permission = permissions.get(code)
            if permission is not None:
                rows.append(AccessRolePermission(role=role, permission=permission))
    AccessRolePermission.objects.bulk_create(rows, ignore_conflicts=True)


def remove_client_deployment_read(apps, schema_editor):
    AccessRolePermission = apps.get_model('access', 'AccessRolePermission')
    AccessRolePermission.objects.filter(
        role__code__in=CLIENT_FACING_ROLE_CODES,
        permission__code__in=CLIENT_DEPLOYMENT_READ_CODES,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('access', '0007_remove_client_department_read'),
    ]

    operations = [
        migrations.RunPython(add_client_deployment_read, remove_client_deployment_read),
    ]

from django.db import migrations


CLIENT_FACING_ROLE_CODES = (
    'client_admin',
    'client_site_user',
    'site_supervisor',
    'client_user',
)


def remove_client_department_read(apps, schema_editor):
    AccessRolePermission = apps.get_model('access', 'AccessRolePermission')
    Permission = apps.get_model('access', 'Permission')

    try:
        department_read = Permission.objects.get(code='department.read')
    except Permission.DoesNotExist:
        return

    AccessRolePermission.objects.filter(
        role__code__in=CLIENT_FACING_ROLE_CODES,
        permission=department_read,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('access', '0006_permission_code'),
    ]

    operations = [
        migrations.RunPython(remove_client_department_read, migrations.RunPython.noop),
    ]

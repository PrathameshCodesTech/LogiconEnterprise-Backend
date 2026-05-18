"""
Migration 0006: Add Permission.code field and workflow_config resource choice.

Steps:
  1. Add workflow_config to resource choices.
  2. Add code field (blank, no unique constraint yet).
  3. Populate code = "{resource}.{action}" for all existing rows.
  4. Apply unique constraint on code.
"""

from django.db import migrations, models


def populate_permission_codes(apps, schema_editor):
    Permission = apps.get_model('access', 'Permission')
    for perm in Permission.objects.all():
        perm.code = f"{perm.resource}.{perm.action}"
        perm.save(update_fields=['code'])


class Migration(migrations.Migration):

    dependencies = [
        ('access', '0005_alter_permission_resource'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permission',
            name='resource',
            field=models.CharField(
                choices=[
                    ('organization', 'Organization'),
                    ('user', 'User'),
                    ('role', 'Role'),
                    ('module', 'Module'),
                    ('client', 'Client'),
                    ('site', 'Site'),
                    ('site_role_requirement', 'Site Role Requirement'),
                    ('job_role', 'Job Role'),
                    ('campaign', 'Campaign'),
                    ('submission', 'Submission'),
                    ('candidate', 'Candidate'),
                    ('resume', 'Resume'),
                    ('mrf', 'MRF'),
                    ('workflow', 'Workflow'),
                    ('workflow_config', 'Workflow Config'),
                    ('wage', 'Wage'),
                    ('budget', 'Budget'),
                    ('interview', 'Interview'),
                    ('offer', 'Offer'),
                    ('hiring_application', 'Hiring Application'),
                    ('employee', 'Employee'),
                    ('site_deployment', 'Site Deployment'),
                    ('department', 'Department'),
                    ('deployment', 'Deployment'),
                    ('report', 'Report'),
                    ('field_tracking', 'Field Tracking'),
                    ('client_onboarding', 'Client Onboarding'),
                ],
                max_length=64,
            ),
        ),
        # Add without unique so existing rows (all code='') don't violate the constraint.
        migrations.AddField(
            model_name='permission',
            name='code',
            field=models.CharField(blank=True, default='', max_length=128),
            preserve_default=False,
        ),
        # Populate code for all existing rows before enforcing uniqueness.
        migrations.RunPython(populate_permission_codes, migrations.RunPython.noop),
        # Now it is safe to make code unique.
        migrations.AlterField(
            model_name='permission',
            name='code',
            field=models.CharField(blank=True, max_length=128, unique=True),
        ),
    ]

"""
mobilisation/migrations/0001_initial.py

Fresh CreateModel migration for mobilisation tables (no onboarding rename path).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0002_add_department'),
        ('sites', '0001_initial'),
        ('budgets', '0001_initial'),
        ('access', '0001_initial'),
        ('sales', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MobilisationSetupRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'), ('submitted', 'Submitted'),
                        ('in_review', 'In Review'), ('approved', 'Approved'),
                        ('rejected', 'Rejected'), ('cancelled', 'Cancelled'),
                    ],
                    default='draft', max_length=16,
                )),
                ('mobilisation_type', models.CharField(
                    choices=[('new_client', 'New Client'), ('new_site_expansion', 'New Site Expansion')],
                    max_length=32,
                )),
                ('summary', models.TextField(blank=True)),
                ('operations_notes', models.TextField(blank=True)),
                ('hr_notes', models.TextField(blank=True)),
                ('finance_notes', models.TextField(blank=True)),
                ('finalization_status', models.CharField(
                    choices=[
                        ('not_finalized', 'Not Finalized'),
                        ('finalized', 'Finalized'),
                        ('failed', 'Failed'),
                    ],
                    default='not_finalized', max_length=16,
                )),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('finalization_error', models.TextField(blank=True)),
                ('mobilisation_requires_approval', models.BooleanField(default=True)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('org', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='mobilisation_requests',
                    to='core.organization',
                )),
                ('client', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='mobilisation_requests',
                    to='sites.client',
                )),
                ('requested_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='mobilisation_requests',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('budget_plan', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mobilisation_requests',
                    to='budgets.budgetplan',
                )),
                ('finalized_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='finalized_mobilisation_requests',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('source_sales_lead', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mobilisation_requests',
                    to='sales.saleslead',
                )),
                ('source_proposal_version', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mobilisation_requests',
                    to='sales.proposalversion',
                )),
            ],
            options={
                'verbose_name': 'Mobilisation Setup Request',
                'verbose_name_plural': 'Mobilisation Setup Requests',
                'db_table': 'mobilisation_setup_request',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MobilisationProposedDepartment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('scope_level', models.CharField(
                    choices=[('client', 'Client'), ('site', 'Site')],
                    default='site', max_length=8,
                )),
                ('name', models.CharField(max_length=128)),
                ('code', models.CharField(max_length=64)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='proposed_departments',
                    to='mobilisation.mobilisationsetuprequest',
                )),
                ('real_site', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mobilisation_proposed_departments',
                    to='sites.siteprofile',
                )),
            ],
            options={
                'verbose_name': 'Mobilisation Proposed Department',
                'verbose_name_plural': 'Mobilisation Proposed Departments',
                'db_table': 'mobilisation_proposed_department',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='mobilisationproposeddepartment',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_active', True), ('real_site__isnull', True)),
                fields=('request', 'code'),
                name='unique_active_mob_dept_client_level',
            ),
        ),
        migrations.AddConstraint(
            model_name='mobilisationproposeddepartment',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_active', True), ('real_site__isnull', False)),
                fields=('request', 'real_site', 'code'),
                name='unique_active_mob_dept_real_site_level',
            ),
        ),
        migrations.CreateModel(
            name='MobilisationProposedUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('full_name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(blank=True, max_length=32)),
                ('user_type', models.CharField(
                    choices=[('client', 'Client'), ('site_manager', 'Site Manager')],
                    default='client', max_length=32,
                )),
                ('scope_level', models.CharField(
                    choices=[('client', 'Client'), ('site', 'Site')],
                    default='client', max_length=16,
                )),
                ('is_primary_contact', models.BooleanField(default=False)),
                ('send_invite_on_finalization', models.BooleanField(default=True)),
                ('is_active', models.BooleanField(default=True)),
                ('invite_status', models.CharField(
                    choices=[
                        ('pending', 'Pending'), ('not_required', 'Not required'),
                        ('sent', 'Sent'), ('failed', 'Failed'),
                    ],
                    default='pending', max_length=32,
                )),
                ('invite_error', models.TextField(blank=True)),
                ('request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='proposed_users',
                    to='mobilisation.mobilisationsetuprequest',
                )),
                ('access_role', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='mobilisation_proposed_users',
                    to='access.accessrole',
                )),
                ('real_site', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mobilisation_proposed_users',
                    to='sites.siteprofile',
                )),
                ('created_user', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_from_mobilisation_proposals',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Mobilisation Proposed User',
                'verbose_name_plural': 'Mobilisation Proposed Users',
                'db_table': 'mobilisation_proposed_user',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='mobilisationproposeduser',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_active', True)),
                fields=('request', 'email'),
                name='unique_active_mob_proposed_user_email',
            ),
        ),
        migrations.AddConstraint(
            model_name='mobilisationproposeduser',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_active', True), ('is_primary_contact', True)),
                fields=('request',),
                name='unique_primary_mob_proposed_user',
            ),
        ),
    ]

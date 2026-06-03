# Generated manually for Phase Deployment-Lifecycle-Backend-G

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('sites', '0002_client_scope_node_and_more'),
        ('jobs', '0001_initial'),
        ('deployment', '0003_employee_employee_code_not_blank'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='sitedeployment',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'active')),
                fields=('employee',),
                name='unique_active_deployment_per_employee',
            ),
        ),
        migrations.CreateModel(
            name='DeploymentHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action_type', models.CharField(
                    choices=[
                        ('deployment_activated', 'Deployment Activated'),
                        ('deployment_cancelled', 'Deployment Cancelled'),
                        ('deployment_completed', 'Deployment Completed'),
                        ('deployment_transferred_out', 'Deployment Transferred (Out)'),
                        ('deployment_transferred_in', 'Deployment Transferred (In)'),
                        ('employee_suspended', 'Employee Suspended'),
                        ('employee_reactivated', 'Employee Reactivated'),
                        ('employee_exited', 'Employee Exited'),
                    ],
                    max_length=40,
                )),
                ('from_status', models.CharField(blank=True, max_length=24)),
                ('to_status', models.CharField(blank=True, max_length=24)),
                ('note', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('actor', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('deployment', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='history',
                    to='deployment.sitedeployment',
                )),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='history',
                    to='deployment.employee',
                )),
                ('from_job_role', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to='jobs.jobrole',
                )),
                ('from_site', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to='sites.siteprofile',
                )),
                ('org', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='deployment_history',
                    to='core.organization',
                )),
                ('to_job_role', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to='jobs.jobrole',
                )),
                ('to_site', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to='sites.siteprofile',
                )),
            ],
            options={
                'verbose_name': 'Deployment History',
                'verbose_name_plural': 'Deployment History',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='deploymenthistory',
            index=models.Index(fields=['employee', '-created_at'], name='deployment__employe_2e3257_idx'),
        ),
        migrations.AddIndex(
            model_name='deploymenthistory',
            index=models.Index(fields=['org', 'action_type', '-created_at'], name='deployment__org_id_a74e5c_idx'),
        ),
        migrations.AddIndex(
            model_name='deploymenthistory',
            index=models.Index(fields=['deployment', '-created_at'], name='deployment__deploym_4910b7_idx'),
        ),
    ]

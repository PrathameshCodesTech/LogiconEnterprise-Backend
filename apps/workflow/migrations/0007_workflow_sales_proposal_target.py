# Generated manually for sales_proposal workflow target

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0004_saleslead_source_mobilisation_request'),
        ('workflow', '0006_approval_routes'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='workflowinstance',
            name='workflow_instance_exactly_one_target',
        ),
        migrations.AddField(
            model_name='workflowinstance',
            name='proposal_version',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='workflow_instances',
                to='sales.proposalversion',
            ),
        ),
        migrations.AddConstraint(
            model_name='workflowinstance',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        ('client_onboarding_request__isnull', True),
                        ('mrf__isnull', False),
                        ('proposal_version__isnull', True),
                    ),
                    models.Q(
                        ('client_onboarding_request__isnull', False),
                        ('mrf__isnull', True),
                        ('proposal_version__isnull', True),
                    ),
                    models.Q(
                        ('client_onboarding_request__isnull', True),
                        ('mrf__isnull', True),
                        ('proposal_version__isnull', False),
                    ),
                    _connector='OR',
                ),
                name='workflow_instance_exactly_one_target',
            ),
        ),
        migrations.AddConstraint(
            model_name='workflowinstance',
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ('proposal_version__isnull', False),
                    ('status', 'active'),
                ),
                fields=('proposal_version',),
                name='unique_active_workflow_per_proposal_version',
            ),
        ),
        migrations.AlterField(
            model_name='approvalroute',
            name='trigger_type',
            field=models.CharField(
                choices=[
                    ('mrf', 'MRF'),
                    ('client_onboarding', 'Client Onboarding'),
                    ('sales_proposal', 'Sales Proposal'),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='stepassignmentconfig',
            name='trigger_type',
            field=models.CharField(
                choices=[
                    ('mrf', 'MRF'),
                    ('client_onboarding', 'Client Onboarding'),
                    ('sales_proposal', 'Sales Proposal'),
                ],
                default='mrf',
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='workflowtemplate',
            name='trigger_type',
            field=models.CharField(
                choices=[
                    ('mrf', 'MRF'),
                    ('client_onboarding', 'Client Onboarding'),
                    ('sales_proposal', 'Sales Proposal'),
                ],
                default='mrf',
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='workflowtemplatemapping',
            name='trigger_type',
            field=models.CharField(
                choices=[
                    ('mrf', 'MRF'),
                    ('client_onboarding', 'Client Onboarding'),
                    ('sales_proposal', 'Sales Proposal'),
                ],
                default='mrf',
                max_length=32,
            ),
        ),
    ]

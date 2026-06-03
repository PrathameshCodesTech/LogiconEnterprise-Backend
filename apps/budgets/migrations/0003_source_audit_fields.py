"""
budgets/migrations/0003_source_audit_fields.py

Add source_type / source_sales_lead / source_proposal_version to BudgetPlan
for tracing how each budget was created (sales-conversion, manual-admin, import).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0002_budget_reservation'),
        ('sales', '0006_proposalversion_amount_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='budgetplan',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('sales_conversion', 'Sales Conversion'),
                    ('manual_admin', 'Manual Admin'),
                    ('import', 'Import'),
                ],
                default='manual_admin',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='budgetplan',
            name='source_sales_lead',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_budget_plans',
                to='sales.saleslead',
            ),
        ),
        migrations.AddField(
            model_name='budgetplan',
            name='source_proposal_version',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_budget_plans',
                to='sales.proposalversion',
            ),
        ),
    ]

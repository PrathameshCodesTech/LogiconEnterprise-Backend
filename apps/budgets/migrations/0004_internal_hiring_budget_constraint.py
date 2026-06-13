from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0003_source_audit_fields'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='budgetplan',
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(('is_active', True))
                    & models.Q(('status', 'active'))
                    & models.Q(('budget_nature', 'non_billable'))
                    & models.Q(('budget_type', 'hiring'))
                    & models.Q(('department__isnull', False))
                ),
                fields=('org', 'department'),
                name='unique_active_internal_hiring_budget_per_department',
            ),
        ),
    ]

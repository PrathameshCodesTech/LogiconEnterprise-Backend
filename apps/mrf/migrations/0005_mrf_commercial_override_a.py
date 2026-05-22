"""
Migration: 0005_mrf_commercial_override_a

Adds commercial override fields to MRFLineItem:
  - master_wage_min_snapshot / master_wage_max_snapshot / master_billing_rate_snapshot / master_shift_hours_snapshot
  - commercial_override_enabled / commercial_override_reason / commercial_overridden_by / commercial_overridden_at
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mrf', '0004_mrf_client_form_a'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='mrflineitem',
            name='master_wage_min_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='master_wage_max_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='master_billing_rate_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='master_shift_hours_snapshot',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=4, null=True),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='commercial_override_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='commercial_override_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='commercial_overridden_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='mrf_line_item_overrides',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='mrflineitem',
            name='commercial_overridden_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

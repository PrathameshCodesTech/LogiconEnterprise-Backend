"""
sales/migrations/0004_saleslead_source_mobilisation_request.py

Add SalesLead.source_onboarding_request FK to mobilisation (fresh DB path).
Field name kept for API compatibility; target is MobilisationSetupRequest.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0002_sales_backend_hardening_b'),
        ('mobilisation', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='saleslead',
            name='source_onboarding_request',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='source_sales_leads',
                to='mobilisation.mobilisationsetuprequest',
            ),
        ),
    ]

"""
sales/migrations/0007_saleslead_lead_type_and_existing_client.py

Add lead_type + existing_client to SalesLead for the 4 business entry paths:
  new_client, site_expansion, scope_expansion, renewal.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_proposalversion_amount_fields'),
        ('sites', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='saleslead',
            name='lead_type',
            field=models.CharField(
                choices=[
                    ('new_client', 'New Client'),
                    ('site_expansion', 'Site Expansion'),
                    ('scope_expansion', 'Scope Expansion'),
                    ('renewal', 'Renewal'),
                ],
                default='new_client',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='saleslead',
            name='existing_client',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='expansion_leads',
                to='sites.client',
            ),
        ),
        migrations.AddIndex(
            model_name='saleslead',
            index=models.Index(fields=['org', 'lead_type'], name='sales_sales_org_id_8cafbb_idx'),
        ),
    ]

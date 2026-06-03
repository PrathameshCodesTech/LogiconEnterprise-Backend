"""
sites/migrations/0006_source_audit_fields.py

Add source_type / source_sales_lead / source_proposal_version to Client,
SiteProfile, and SiteRoleRequirement for tracing how each real record was
created (sales-conversion, manual-admin, or import).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sites', '0005_srr_department_fk'),
        ('sales', '0006_proposalversion_amount_fields'),
    ]

    operations = [
        # ── Client ────────────────────────────────────────────────────────────
        migrations.AddField(
            model_name='client',
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
            model_name='client',
            name='source_sales_lead',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_clients',
                to='sales.saleslead',
            ),
        ),
        migrations.AddField(
            model_name='client',
            name='source_proposal_version',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_clients',
                to='sales.proposalversion',
            ),
        ),
        migrations.AddIndex(
            model_name='client',
            index=models.Index(fields=['org', 'source_type'], name='sites_clien_org_id_39736b_idx'),
        ),

        # ── SiteProfile ───────────────────────────────────────────────────────
        migrations.AddField(
            model_name='siteprofile',
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
            model_name='siteprofile',
            name='source_sales_lead',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_sites',
                to='sales.saleslead',
            ),
        ),
        migrations.AddField(
            model_name='siteprofile',
            name='source_proposal_version',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_sites',
                to='sales.proposalversion',
            ),
        ),
        migrations.AddIndex(
            model_name='siteprofile',
            index=models.Index(fields=['org', 'source_type'], name='sites_sitep_org_id_1d46e8_idx'),
        ),

        # ── SiteRoleRequirement ───────────────────────────────────────────────
        migrations.AddField(
            model_name='siterolerequirement',
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
            model_name='siterolerequirement',
            name='source_sales_lead',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_srrs',
                to='sales.saleslead',
            ),
        ),
        migrations.AddField(
            model_name='siterolerequirement',
            name='source_proposal_version',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_srrs',
                to='sales.proposalversion',
            ),
        ),
        migrations.AddIndex(
            model_name='siterolerequirement',
            index=models.Index(fields=['source_type'], name='sites_siter_source__4c4a32_idx'),
        ),
    ]

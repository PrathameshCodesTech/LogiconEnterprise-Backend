"""
Migration: form_section_contract_cleanup

Adds description, is_active, created_at, updated_at to FormSection,
and a conditional unique constraint on (template, code) when code is not blank.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0005_form_builder_backend_a_fixup'),
    ]

    operations = [
        migrations.AddField(
            model_name='formsection',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='formsection',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='formsection',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='formsection',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddConstraint(
            model_name='formsection',
            constraint=models.UniqueConstraint(
                condition=models.Q(code__gt=''),
                fields=['template', 'code'],
                name='uq_form_section_template_code',
            ),
        ),
    ]

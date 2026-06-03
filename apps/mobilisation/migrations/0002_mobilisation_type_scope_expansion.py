"""
mobilisation/migrations/0002_mobilisation_type_scope_expansion.py

Register the 'scope_expansion' choice on MobilisationSetupRequest.mobilisation_type
so scope_expansion sales leads can be converted into mobilisation requests.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mobilisation', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mobilisationsetuprequest',
            name='mobilisation_type',
            field=models.CharField(
                choices=[
                    ('new_client', 'New Client'),
                    ('new_site_expansion', 'New Site Expansion'),
                    ('scope_expansion', 'Scope Expansion'),
                ],
                max_length=32,
            ),
        ),
    ]

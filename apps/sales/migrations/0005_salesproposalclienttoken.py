# Generated manually for client proposal response tokens

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sales', '0004_saleslead_source_mobilisation_request'),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesProposalClientToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('recipient_email', models.EmailField(max_length=254)),
                ('recipient_name', models.CharField(blank=True, max_length=255)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('is_revoked', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_accessed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_proposal_client_tokens',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('lead', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='proposal_client_tokens',
                    to='sales.saleslead',
                )),
                ('proposal_version', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='client_tokens',
                    to='sales.proposalversion',
                )),
            ],
            options={
                'verbose_name': 'Sales Proposal Client Token',
                'verbose_name_plural': 'Sales Proposal Client Tokens',
                'ordering': ['-created_at'],
            },
        ),
    ]

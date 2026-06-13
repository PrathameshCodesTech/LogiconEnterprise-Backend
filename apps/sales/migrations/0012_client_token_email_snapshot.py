# Generated manually for proposal client email draft snapshots

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0011_surveyrolemapping_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesproposalclienttoken',
            name='email_subject',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='salesproposalclienttoken',
            name='email_body',
            field=models.TextField(blank=True),
        ),
    ]

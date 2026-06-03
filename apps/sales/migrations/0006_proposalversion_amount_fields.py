# Generated manually for proposal calculation amount fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0005_salesproposalclienttoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposalversion',
            name='subtotal_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='proposalversion',
            name='management_fee_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='proposalversion',
            name='gst_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]

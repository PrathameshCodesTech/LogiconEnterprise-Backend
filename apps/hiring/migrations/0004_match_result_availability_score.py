from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hiring', '0003_match_result_score_breakdown'),
    ]

    operations = [
        migrations.AddField(
            model_name='candidatematchresult',
            name='availability_score',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]

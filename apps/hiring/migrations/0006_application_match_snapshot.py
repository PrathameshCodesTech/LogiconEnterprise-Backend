from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('hiring', '0005_interviewplan_alter_interview_status_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hiringapplication',
            name='match_score',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Optional rules/manual match score out of 100.',
                max_digits=5,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='hiringapplication',
            name='match_result',
            field=models.ForeignKey(
                blank=True,
                help_text='Match result used when this candidate was shortlisted.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='shortlisted_applications',
                to='hiring.candidatematchresult',
            ),
        ),
        migrations.AddField(
            model_name='hiringapplication',
            name='match_snapshot',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Snapshot of scorecard used at shortlist time.',
            ),
        ),
    ]

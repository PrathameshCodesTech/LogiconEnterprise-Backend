from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0001_initial'),
        ('sales', '0012_client_token_email_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='job_role',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional structured role for rows added from the job role master.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='survey_shift_deployments',
                to='jobs.jobrole',
            ),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='night_shift_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]

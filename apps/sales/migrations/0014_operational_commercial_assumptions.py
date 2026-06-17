from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0013_shift_deployment_night_shift_and_job_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='reliever_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='shift_hours',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=4, null=True),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='working_days',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=4, null=True),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='base_wage',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='base_wage_source',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='base_wage_overridden',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='base_wage_override_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='sitesurveyshiftdeployment',
            name='monthly_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='source_shift_deployment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='generated_role_requirements',
                to='sales.sitesurveyshiftdeployment',
            ),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='general_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='first_shift_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='second_shift_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='night_shift_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='reliever_count',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='operational_base_wage',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='operational_base_wage_source',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='operational_base_wage_overridden',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='operational_base_wage_override_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='salesrolerequirement',
            name='operational_monthly_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='proposalbudgetline',
            name='source_unit_cost',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='proposalbudgetline',
            name='source_unit_cost_origin',
            field=models.CharField(blank=True, max_length=40),
        ),
    ]

# Generated manually for role-tagged resume intake.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0001_initial'),
        ('talent', '0004_talent_resume_review_b'),
    ]

    operations = [
        migrations.AddField(
            model_name='candidate',
            name='target_job_role',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='targeted_candidates',
                to='jobs.jobrole',
            ),
        ),
        migrations.AddField(
            model_name='resume',
            name='import_batch_id',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='resume',
            name='target_job_role',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='targeted_resumes',
                to='jobs.jobrole',
            ),
        ),
        migrations.AddField(
            model_name='resume',
            name='target_role_source',
            field=models.CharField(
                blank=True,
                choices=[
                    ('manual', 'Manual'),
                    ('bulk_upload', 'Bulk Upload'),
                    ('excel_import', 'Excel Import'),
                    ('campaign', 'Campaign'),
                    ('qr_intake', 'QR Intake'),
                ],
                default='',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='resume',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('qr_intake', 'QR Intake'),
                    ('manual_upload', 'Manual Upload'),
                    ('recruiter_upload', 'Recruiter Upload'),
                    ('portal', 'Portal'),
                    ('referral', 'Referral'),
                    ('import_', 'Import'),
                    ('bulk_upload', 'Bulk Upload'),
                    ('excel_import', 'Excel Import'),
                    ('campaign', 'Campaign'),
                ],
                default='manual_upload',
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name='candidate',
            index=models.Index(fields=['org', 'target_job_role'], name='talent_cand_org_id_30e0fc_idx'),
        ),
        migrations.AddIndex(
            model_name='resume',
            index=models.Index(fields=['target_job_role', 'status'], name='talent_resu_target__b764c7_idx'),
        ),
        migrations.AddIndex(
            model_name='resume',
            index=models.Index(fields=['source_type', 'target_job_role'], name='talent_resu_source__14c561_idx'),
        ),
        migrations.AddIndex(
            model_name='resume',
            index=models.Index(fields=['import_batch_id'], name='talent_resu_import__c1bf82_idx'),
        ),
    ]

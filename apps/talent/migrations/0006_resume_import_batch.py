# Generated manually for asynchronous resume bulk imports.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('jobs', '0001_initial'),
        ('talent', '0005_resume_role_tagging'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResumeImportBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source_type', models.CharField(choices=[('qr_intake', 'QR Intake'), ('manual_upload', 'Manual Upload'), ('recruiter_upload', 'Recruiter Upload'), ('portal', 'Portal'), ('referral', 'Referral'), ('import_', 'Import'), ('bulk_upload', 'Bulk Upload'), ('excel_import', 'Excel Import'), ('campaign', 'Campaign')], default='bulk_upload', max_length=20)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('completed', 'Completed'), ('completed_with_errors', 'Completed With Errors'), ('failed', 'Failed')], default='queued', max_length=32)),
                ('total_count', models.PositiveIntegerField(default=0)),
                ('processed_count', models.PositiveIntegerField(default=0)),
                ('success_count', models.PositiveIntegerField(default=0)),
                ('duplicate_count', models.PositiveIntegerField(default=0)),
                ('failed_count', models.PositiveIntegerField(default=0)),
                ('manual_review_count', models.PositiveIntegerField(default=0)),
                ('view_only_note', models.CharField(blank=True, max_length=255)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resume_import_batches', to=settings.AUTH_USER_MODEL)),
                ('org', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='resume_import_batches', to='core.organization')),
                ('target_job_role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resume_import_batches', to='jobs.jobrole')),
            ],
            options={
                'verbose_name': 'Resume Import Batch',
                'verbose_name_plural': 'Resume Import Batches',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ResumeImportItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='resume_imports/%Y/%m/')),
                ('original_filename', models.CharField(blank=True, max_length=255)),
                ('content_type', models.CharField(blank=True, max_length=128)),
                ('size_bytes', models.PositiveBigIntegerField(blank=True, null=True)),
                ('file_hash', models.CharField(blank=True, max_length=64)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('indexed', 'Indexed'), ('duplicate_file', 'Duplicate File'), ('manual_review', 'Manual Review'), ('failed', 'Failed')], default='queued', max_length=32)),
                ('error_message', models.TextField(blank=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='talent.resumeimportbatch')),
                ('candidate', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resume_import_items', to='talent.candidate')),
                ('resume', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='import_items', to='talent.resume')),
            ],
            options={
                'verbose_name': 'Resume Import Item',
                'verbose_name_plural': 'Resume Import Items',
                'ordering': ['id'],
            },
        ),
        migrations.AddIndex(
            model_name='resumeimportbatch',
            index=models.Index(fields=['org', 'status'], name='talent_resu_org_id_244e22_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportbatch',
            index=models.Index(fields=['org', 'target_job_role'], name='talent_resu_org_id_a01ec4_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportbatch',
            index=models.Index(fields=['created_by', 'status'], name='talent_resu_created_e4280c_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportitem',
            index=models.Index(fields=['batch', 'status'], name='talent_resu_batch_i_2e95d0_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportitem',
            index=models.Index(fields=['file_hash'], name='talent_resu_file_ha_1835a0_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportitem',
            index=models.Index(fields=['candidate'], name='talent_resu_candida_08b090_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportitem',
            index=models.Index(fields=['resume'], name='talent_resu_resume__3855c0_idx'),
        ),
    ]

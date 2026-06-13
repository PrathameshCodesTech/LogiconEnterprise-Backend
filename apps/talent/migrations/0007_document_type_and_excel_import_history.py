from django.db import migrations, models


DOCUMENT_TYPE_CHOICES = [
    ('pdf', 'PDF'),
    ('docx', 'DOCX'),
    ('doc', 'DOC'),
    ('txt', 'Text'),
    ('xlsx', 'Excel'),
    ('csv', 'CSV'),
    ('unknown', 'Unknown'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('talent', '0006_resume_import_batch'),
    ]

    operations = [
        migrations.AddField(
            model_name='resume',
            name='document_type',
            field=models.CharField(
                choices=DOCUMENT_TYPE_CHOICES,
                default='unknown',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='resumeimportbatch',
            name='content_type',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='resumeimportbatch',
            name='document_type',
            field=models.CharField(
                choices=DOCUMENT_TYPE_CHOICES,
                default='unknown',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='resumeimportbatch',
            name='import_file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='candidate_imports/%Y/%m/',
            ),
        ),
        migrations.AddField(
            model_name='resumeimportbatch',
            name='original_filename',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='resumeimportbatch',
            name='size_bytes',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='resumeimportitem',
            name='document_type',
            field=models.CharField(
                choices=DOCUMENT_TYPE_CHOICES,
                default='unknown',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='resumeimportitem',
            name='row_number',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='resumeimportitem',
            name='file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='resume_imports/%Y/%m/',
            ),
        ),
        migrations.AddIndex(
            model_name='resume',
            index=models.Index(fields=['document_type'], name='talent_resu_documen_85a851_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportbatch',
            index=models.Index(fields=['org', 'document_type'], name='talent_resu_org_id_7f4ef1_idx'),
        ),
        migrations.AddIndex(
            model_name='resumeimportitem',
            index=models.Index(fields=['batch', 'document_type'], name='talent_resu_batch_i_c38097_idx'),
        ),
    ]

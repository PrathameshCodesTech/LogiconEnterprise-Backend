"""
Migration 0002 — Phase 3E: Port QR intake fields into enterprise models.

Adds: token, title, language settings, date window, flags on QRCampaign;
      is_active on CampaignJobRole;
      full field set on FormField (renames field_name→label, order→sort_order);
      full submission fields on IntakeSubmission;
      JSONField value + snapshots on IntakeSubmissionAnswer;
      original_filename/content_type/size_bytes + field FK on IntakeDocument.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
import secrets


def _generate_tokens(apps, schema_editor):
    QRCampaign = apps.get_model('intake', 'QRCampaign')
    for campaign in QRCampaign.objects.filter(token=''):
        campaign.token = secrets.token_urlsafe(16)
        campaign.save(update_fields=['token'])


def _convert_answers_to_json(apps, schema_editor):
    """Convert existing TextField answers to JSON-compatible values."""
    IntakeSubmissionAnswer = apps.get_model('intake', 'IntakeSubmissionAnswer')
    for answer in IntakeSubmissionAnswer.objects.all():
        if answer.value in ('', None):
            answer.value = None
            answer.save(update_fields=['value'])


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0001_initial'),
        ('jobs', '0001_initial'),
    ]

    operations = [
        # ── QRCampaign: make site nullable ────────────────────────────────────
        migrations.AlterField(
            model_name='qrcampaign',
            name='site',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='qr_campaigns',
                to='sites.siteprofile',
            ),
        ),

        # ── QRCampaign: new fields ─────────────────────────────────────────────
        migrations.AddField(
            model_name='qrcampaign',
            name='title',
            field=models.CharField(blank=True, max_length=255, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='token',
            field=models.CharField(blank=True, default='', max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(_generate_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='qrcampaign',
            name='token',
            field=models.CharField(blank=True, max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='starts_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='ends_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='allow_duplicates',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='requires_otp',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='shuffle_fields',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='default_language',
            field=models.CharField(
                choices=[('en', 'English'), ('hi', 'Hindi'), ('mr', 'Marathi')],
                default='en',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='qrcampaign',
            name='enabled_languages',
            field=models.JSONField(default=list),
        ),

        # ── CampaignJobRole: is_active ─────────────────────────────────────────
        migrations.AddField(
            model_name='campaignjobrole',
            name='is_active',
            field=models.BooleanField(default=True),
        ),

        # ── FormField: rename field_name → label, order → sort_order ──────────
        migrations.RenameField(
            model_name='formfield',
            old_name='field_name',
            new_name='label',
        ),
        migrations.RenameField(
            model_name='formfield',
            old_name='order',
            new_name='sort_order',
        ),
        migrations.AlterField(
            model_name='formfield',
            name='sort_order',
            field=models.IntegerField(default=0),
        ),

        # ── FormField: update field_type choices ───────────────────────────────
        migrations.AlterField(
            model_name='formfield',
            name='field_type',
            field=models.CharField(
                choices=[
                    ('text', 'Text'),
                    ('textarea', 'Textarea'),
                    ('number', 'Number'),
                    ('date', 'Date'),
                    ('email', 'Email'),
                    ('select', 'Select'),
                    ('multi_select', 'Multi Select'),
                    ('boolean', 'Boolean'),
                    ('file', 'File'),
                ],
                default='text',
                max_length=20,
            ),
        ),

        # ── FormField: new fields ──────────────────────────────────────────────
        migrations.AddField(
            model_name='formfield',
            name='role',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='form_fields',
                to='jobs.jobrole',
            ),
        ),
        migrations.AddField(
            model_name='formfield',
            name='field_key',
            field=models.CharField(default='', max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='formfield',
            name='help_text',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='placeholder',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='formfield',
            name='options',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='formfield',
            name='min_length',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='max_length',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='min_value',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='max_value',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='translations',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='formfield',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='formfield',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='formfield',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterModelOptions(
            name='formfield',
            options={
                'ordering': ['sort_order', 'id'],
                'verbose_name': 'Form Field',
                'verbose_name_plural': 'Form Fields',
            },
        ),

        # ── IntakeSubmission: make site nullable ───────────────────────────────
        migrations.AlterField(
            model_name='intakesubmission',
            name='site',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='intake_submissions',
                to='sites.siteprofile',
            ),
        ),

        # ── IntakeSubmission: new fields ───────────────────────────────────────
        migrations.AddField(
            model_name='intakesubmission',
            name='job_role',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='intake_submissions',
                to='jobs.jobrole',
            ),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='first_name',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='middle_name',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='last_name',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='full_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='other_role_title',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='other_role_title_normalized',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='mobile_number',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='mobile_number_normalized',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'New'),
                    ('reviewed', 'Reviewed'),
                    ('shortlisted', 'Shortlisted'),
                    ('rejected', 'Rejected'),
                    ('contacted', 'Contacted'),
                    ('hired', 'Hired'),
                    ('duplicate', 'Duplicate'),
                ],
                default='new',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='language',
            field=models.CharField(
                choices=[('en', 'English'), ('hi', 'Hindi'), ('mr', 'Marathi')],
                default='en',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='is_possible_duplicate',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='duplicate_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='user_agent',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='intakesubmission',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddIndex(
            model_name='intakesubmission',
            index=models.Index(
                fields=['mobile_number_normalized'],
                name='intake_intak_mobile__idx',
            ),
        ),
        migrations.AddIndex(
            model_name='intakesubmission',
            index=models.Index(
                fields=['status'],
                name='intake_intak_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='intakesubmission',
            index=models.Index(
                fields=['campaign', 'mobile_number_normalized'],
                name='intake_intak_camp_mob_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='intakesubmission',
            index=models.Index(
                fields=['campaign', 'mobile_number_normalized', 'job_role'],
                name='intake_intak_camp_mob_role_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='intakesubmission',
            index=models.Index(
                fields=['candidate', 'campaign', 'submitted_at'],
                name='intake_intak_cand_camp_idx',
            ),
        ),

        # ── IntakeSubmissionAnswer: update field FK and value field ─────────────
        migrations.AlterField(
            model_name='intakesubmissionanswer',
            name='field',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='answers',
                to='intake.formfield',
            ),
        ),
        migrations.RunPython(_convert_answers_to_json, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='intakesubmissionanswer',
            name='value',
            field=models.JSONField(),
        ),
        migrations.AddField(
            model_name='intakesubmissionanswer',
            name='field_label_snapshot',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='intakesubmissionanswer',
            name='field_type_snapshot',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='intakesubmissionanswer',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),

        # ── IntakeDocument: update type choices and add file metadata ────────────
        migrations.AlterField(
            model_name='intakedocument',
            name='document_type',
            field=models.CharField(
                choices=[
                    ('resume', 'Resume'),
                    ('id_proof', 'ID Proof'),
                    ('certificate', 'Certificate'),
                    ('other', 'Other'),
                ],
                default='other',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='intakedocument',
            name='field',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='documents',
                to='intake.formfield',
            ),
        ),
        migrations.AddField(
            model_name='intakedocument',
            name='original_filename',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='intakedocument',
            name='content_type',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='intakedocument',
            name='size_bytes',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]

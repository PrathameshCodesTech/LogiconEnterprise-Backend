from django.contrib import admin
from .models import (
    QRCampaign, CampaignJobRole, FormField,
    IntakeSubmission, IntakeSubmissionAnswer, IntakeDocument,
)


@admin.register(QRCampaign)
class QRCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'title', 'code', 'token', 'org', 'site', 'is_active', 'created_at']
    search_fields = ['name', 'title', 'code', 'token']
    list_filter = ['is_active', 'org', 'default_language']
    readonly_fields = ['token', 'created_at', 'updated_at']
    raw_id_fields = ['org', 'site']


@admin.register(CampaignJobRole)
class CampaignJobRoleAdmin(admin.ModelAdmin):
    list_display = ['campaign', 'job_role', 'is_active']
    search_fields = ['campaign__name', 'job_role__name']
    list_filter = ['is_active']
    raw_id_fields = ['campaign', 'job_role']


@admin.register(FormField)
class FormFieldAdmin(admin.ModelAdmin):
    list_display = ['campaign', 'label', 'field_key', 'field_type', 'is_required', 'sort_order', 'is_active']
    search_fields = ['label', 'field_key', 'campaign__name']
    list_filter = ['field_type', 'is_required', 'is_active']
    raw_id_fields = ['campaign', 'role']


@admin.register(IntakeSubmission)
class IntakeSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'campaign', 'full_name', 'mobile_number_normalized',
        'job_role', 'status', 'is_possible_duplicate', 'submitted_at',
    ]
    search_fields = ['full_name', 'mobile_number_normalized', 'campaign__name']
    list_filter = ['status', 'is_possible_duplicate', 'language', 'campaign']
    readonly_fields = ['submitted_at', 'updated_at', 'mobile_number_normalized']
    raw_id_fields = ['campaign', 'site', 'candidate', 'job_role']


@admin.register(IntakeSubmissionAnswer)
class IntakeSubmissionAnswerAdmin(admin.ModelAdmin):
    list_display = ['submission', 'field_label_snapshot', 'field_type_snapshot', 'created_at']
    search_fields = ['field_label_snapshot', 'submission__full_name']
    raw_id_fields = ['submission', 'field']


@admin.register(IntakeDocument)
class IntakeDocumentAdmin(admin.ModelAdmin):
    list_display = ['submission', 'document_type', 'original_filename', 'size_bytes', 'uploaded_at']
    search_fields = ['document_type', 'original_filename']
    list_filter = ['document_type']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['submission', 'field']

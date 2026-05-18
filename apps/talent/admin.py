from django.contrib import admin
from .models import (
    Candidate, Resume, CandidateSkill,
    ParsedResume, CandidateExperience, CandidateEducation,
)


class CandidateSkillInline(admin.TabularInline):
    model = CandidateSkill
    extra = 0
    fields = ['skill_name', 'proficiency', 'years_experience', 'confidence', 'source']
    show_change_link = True


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = [
        'first_name', 'last_name', 'phone', 'email',
        'lifecycle_status', 'availability_status', 'source', 'org', 'created_at',
    ]
    search_fields = ['first_name', 'last_name', 'phone', 'phone_normalized', 'email', 'current_company']
    list_filter = ['source', 'lifecycle_status', 'availability_status', 'is_duplicate', 'do_not_contact', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['duplicate_of']
    inlines = [CandidateSkillInline]
    fieldsets = [
        (None, {'fields': [
            'org', 'first_name', 'middle_name', 'last_name',
            'phone', 'phone_normalized', 'email',
        ]}),
        ('Status', {'fields': [
            'lifecycle_status', 'availability_status',
            'is_blacklisted', 'blacklist_reason',
            'is_duplicate', 'duplicate_of', 'do_not_contact',
        ]}),
        ('Professional', {'fields': [
            'current_company', 'current_role', 'current_location',
            'preferred_location', 'notice_period_days',
            'total_experience_years', 'current_ctc', 'expected_ctc',
        ]}),
        ('Source', {'fields': ['source', 'source_reference']}),
        ('Timestamps', {'fields': ['created_at', 'updated_at']}),
    ]


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = [
        'candidate', 'original_filename', 'status', 'source_type',
        'ocr_used', 'size_bytes', 'uploaded_at',
    ]
    search_fields = ['candidate__first_name', 'candidate__last_name', 'original_filename']
    list_filter = ['status', 'source_type', 'ocr_used']
    readonly_fields = ['uploaded_at', 'file_hash']
    raw_id_fields = ['candidate', 'source_intake_document', 'uploaded_by']


@admin.register(CandidateSkill)
class CandidateSkillAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'skill_name', 'proficiency', 'years_experience', 'confidence', 'source']
    search_fields = ['skill_name', 'normalized_skill_name', 'candidate__first_name', 'candidate__last_name']
    list_filter = ['proficiency', 'source']
    raw_id_fields = ['candidate', 'source_resume']


@admin.register(ParsedResume)
class ParsedResumeAdmin(admin.ModelAdmin):
    list_display = ['id', 'resume', 'career_level', 'primary_domain', 'confidence', 'created_at']
    search_fields = ['resume__candidate__first_name', 'resume__candidate__last_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['resume']


@admin.register(CandidateExperience)
class CandidateExperienceAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'job_title', 'company_name', 'start_date', 'end_date', 'is_current']
    search_fields = ['job_title', 'company_name', 'candidate__first_name', 'candidate__last_name']
    list_filter = ['is_current', 'industry']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['candidate', 'source_resume']


@admin.register(CandidateEducation)
class CandidateEducationAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'degree', 'specialization', 'institute', 'end_year']
    search_fields = ['degree', 'institute', 'candidate__first_name', 'candidate__last_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['candidate', 'source_resume']

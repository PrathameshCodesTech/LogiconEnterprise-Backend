from django.contrib import admin
from .models import Candidate, Resume, CandidateSkill


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'phone', 'email', 'source', 'org', 'is_blacklisted', 'created_at']
    search_fields = ['first_name', 'last_name', 'phone', 'phone_normalized', 'email']
    list_filter = ['source', 'is_blacklisted', 'org']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'original_filename', 'parsed_status', 'size_bytes', 'uploaded_at']
    search_fields = ['candidate__first_name', 'candidate__last_name', 'original_filename']
    list_filter = ['parsed_status']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['candidate']


@admin.register(CandidateSkill)
class CandidateSkillAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'skill_name', 'normalized_skill_name', 'confidence']
    search_fields = ['skill_name', 'normalized_skill_name', 'candidate__first_name', 'candidate__last_name']
    raw_id_fields = ['candidate']

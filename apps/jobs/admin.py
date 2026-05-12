from django.contrib import admin
from .models import JobRole


@admin.register(JobRole)
class JobRoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'org', 'skill_category', 'is_active', 'created_at']
    search_fields = ['name', 'code', 'description']
    list_filter = ['skill_category', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org']

from django.contrib import admin
from .models import Organization, ScopeNode


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at', 'updated_at']
    search_fields = ['name', 'code']
    list_filter = ['is_active']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ScopeNode)
class ScopeNodeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'node_type', 'org', 'parent', 'depth', 'path', 'is_active']
    search_fields = ['name', 'code', 'path']
    list_filter = ['node_type', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['parent', 'org']

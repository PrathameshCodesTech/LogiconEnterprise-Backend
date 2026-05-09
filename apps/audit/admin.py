from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['actor', 'action', 'object_type', 'object_id', 'org', 'ip_address', 'created_at']
    search_fields = ['actor__username', 'action', 'object_type', 'object_id', 'ip_address']
    list_filter = ['action', 'object_type', 'org']
    readonly_fields = ['actor', 'org', 'scope_node', 'action', 'object_type', 'object_id', 'metadata', 'ip_address', 'user_agent', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

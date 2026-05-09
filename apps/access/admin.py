from django.contrib import admin
from .models import (
    AccessRole, Permission, AccessRolePermission,
    UserScopeAssignment, UserRoleAssignment,
)


@admin.register(AccessRole)
class AccessRoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'org', 'node_type_scope', 'is_active', 'created_at']
    search_fields = ['name', 'code']
    list_filter = ['is_active', 'org', 'node_type_scope']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['resource', 'action', 'description']
    search_fields = ['resource', 'action', 'description']
    list_filter = ['resource', 'action']


@admin.register(AccessRolePermission)
class AccessRolePermissionAdmin(admin.ModelAdmin):
    list_display = ['role', 'permission']
    search_fields = ['role__code', 'permission__resource', 'permission__action']
    list_filter = ['role__org', 'permission__resource']
    raw_id_fields = ['role', 'permission']


@admin.register(UserScopeAssignment)
class UserScopeAssignmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'scope_node', 'assignment_type', 'created_at']
    search_fields = ['user__username', 'scope_node__name', 'scope_node__code']
    list_filter = ['assignment_type']
    readonly_fields = ['created_at']
    raw_id_fields = ['user', 'scope_node']


@admin.register(UserRoleAssignment)
class UserRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'scope_node', 'created_at']
    search_fields = ['user__username', 'role__code', 'scope_node__name']
    list_filter = ['role__org', 'role']
    readonly_fields = ['created_at']
    raw_id_fields = ['user', 'role', 'scope_node']

from django.contrib import admin
from .models import ModuleActivation


@admin.register(ModuleActivation)
class ModuleActivationAdmin(admin.ModelAdmin):
    list_display = ['module', 'scope_node', 'is_active', 'override_parent', 'created_at']
    search_fields = ['module', 'scope_node__name', 'scope_node__path']
    list_filter = ['module', 'is_active', 'override_parent']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['scope_node']

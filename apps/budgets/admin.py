from django.contrib import admin

from .models import BudgetPlan, BudgetReservation


@admin.register(BudgetPlan)
class BudgetPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'budget_nature', 'budget_type', 'client', 'site', 'department', 'amount', 'currency', 'status', 'is_active']
    list_filter = ['budget_nature', 'budget_type', 'status', 'is_active', 'org']
    search_fields = ['name', 'code', 'client__name', 'site__name', 'department__name']
    raw_id_fields = ['org', 'client', 'site', 'department', 'created_by', 'updated_by']


@admin.register(BudgetReservation)
class BudgetReservationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'budget_plan', 'mrf', 'amount', 'status',
        'reserved_by', 'reserved_at', 'committed_at', 'released_at',
    ]
    list_filter = ['status', 'org']
    search_fields = ['mrf__id', 'budget_plan__name', 'budget_plan__code']
    readonly_fields = ['reserved_at', 'committed_at', 'released_at', 'created_at', 'updated_at']
    raw_id_fields = ['org', 'budget_plan', 'mrf', 'line_item', 'reserved_by']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin
from .models import LocationArea, WageCategory, MinimumWageRate


@admin.register(LocationArea)
class LocationAreaAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'area_type', 'parent', 'state_name', 'is_active']
    search_fields = ['name', 'code', 'state_name']
    list_filter = ['area_type', 'is_active']
    raw_id_fields = ['parent']


@admin.register(WageCategory)
class WageCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'description']
    search_fields = ['name', 'code']


@admin.register(MinimumWageRate)
class MinimumWageRateAdmin(admin.ModelAdmin):
    list_display = [
        'org', 'location', 'state', 'city', 'wage_category', 'role',
        'monthly_wage', 'daily_wage', 'effective_from', 'effective_to', 'is_active',
    ]
    search_fields = ['state', 'city', 'source_note', 'wage_category__name', 'role__name']
    list_filter = ['is_active', 'wage_category', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org', 'location', 'wage_category', 'role']

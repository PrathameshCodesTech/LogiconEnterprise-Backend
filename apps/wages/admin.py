from django.contrib import admin
from .models import WageCategory, MinimumWageRate


@admin.register(WageCategory)
class WageCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'description']
    search_fields = ['name', 'code']


@admin.register(MinimumWageRate)
class MinimumWageRateAdmin(admin.ModelAdmin):
    list_display = ['wage_category', 'state', 'city', 'monthly_wage', 'daily_wage', 'effective_from', 'effective_to', 'is_active']
    search_fields = ['state', 'city', 'wage_category__name']
    list_filter = ['is_active', 'wage_category', 'state']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['wage_category']

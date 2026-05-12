from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = [
        'username', 'email', 'get_full_name', 'org', 'department', 'user_type',
        'employee_code', 'is_invited', 'is_active', 'is_staff', 'date_joined',
    ]
    list_filter = ['user_type', 'is_invited', 'is_active', 'is_staff', 'org', 'department']
    search_fields = [
        'username', 'first_name', 'last_name', 'email',
        'employee_code', 'phone_number', 'phone_normalized',
    ]
    readonly_fields = ['created_at', 'updated_at', 'date_joined', 'last_login']
    raw_id_fields = ['org', 'department']

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Logicon Profile', {
            'fields': (
                'org', 'department', 'user_type', 'phone_number', 'phone_normalized',
                'employee_code', 'is_invited', 'last_invited_at',
            ),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Logicon Profile', {
            'fields': ('org', 'department', 'user_type', 'phone_number', 'employee_code', 'is_invited'),
        }),
    )

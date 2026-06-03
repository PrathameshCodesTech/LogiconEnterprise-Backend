from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'recipient', 'notification_type', 'title',
        'is_read', 'target_type', 'target_id', 'created_at',
    ]
    list_filter = ['notification_type', 'is_read', 'target_type', 'created_at']
    search_fields = ['title', 'message', 'recipient__username', 'actor__username']
    raw_id_fields = ['org', 'recipient', 'actor']
    readonly_fields = ['created_at', 'updated_at', 'read_at']
